"""Structured, redacted, rotating file logging.

Logs are JSON lines in the user data folder (never the repo). Two safety nets
keep secrets out of them:

* every formatted line passes through :func:`redact`, which masks anything
  shaped like an Anthropic key plus the exact key value registered at startup;
* conversation text is only logged when ``logging.log_transcripts`` is on,
  which callers check via :func:`transcripts_enabled`.

Raw audio is never passed to the logger at all.
"""

from __future__ import annotations

import json
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from jarvis.config import LoggingConfig

LOG_FILE_NAME = "jarvis.log"
_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")
_MASK = "[REDACTED]"

# Attributes every LogRecord has; anything else came from ``extra=`` and is logged as a field.
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}

_secrets: set[str] = set()
_log_transcripts = False


def register_secret(value: str) -> None:
    """Mask this exact string in every future log line."""
    if value:
        _secrets.add(value)


def redact(text: str) -> str:
    """Mask registered secrets and anything that looks like an Anthropic API key."""
    for secret in _secrets:
        text = text.replace(secret, _MASK)
    return _KEY_PATTERN.sub(_MASK, text)


def transcripts_enabled() -> bool:
    """Whether user/assistant text may be written to the log."""
    return _log_transcripts


class JsonFormatter(logging.Formatter):
    """One JSON object per line: easy to grep, easy to load for latency analysis."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False, default=str))


class _RedactingConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(config: LoggingConfig, log_dir: Path) -> Path:
    """Configure the root logger: full JSON log to file, only errors on the console.

    The console is the conversation itself in text mode, so warnings that are
    already spoken as a fallback line ("I could not reach my brain") would only
    clutter it; they are still in the log file.
    """
    global _log_transcripts
    _log_transcripts = config.log_transcripts

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())
    file_handler.setLevel(config.level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(_RedactingConsoleFormatter("[%(levelname)s] %(message)s"))
    console_handler.setLevel(logging.ERROR)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(config.level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # The HTTP stack logs request details at DEBUG/INFO; keep it quiet even if we run at DEBUG.
    for noisy in ("httpx2", "httpcore2", "anthropic", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # The SDK warns that an exported ANTHROPIC_API_KEY overrides profile discovery.
    # Jarvis passes the key explicitly and never uses profiles, so the notice is noise.
    logging.getLogger("anthropic.lib.credentials").setLevel(logging.ERROR)
    return log_path
