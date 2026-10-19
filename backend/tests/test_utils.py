from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from jarvis.config import JarvisConfig
from jarvis.events import CodeBlock, Transcript
from jarvis.llm.prompts import build_system_prompt
from jarvis.terminal import TerminalRenderer
from jarvis.utils.language import detect_language
from jarvis.utils.logging import JsonFormatter, redact, register_secret


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("open vs code", "en"),
        ("calculator kholo", "en"),
        ("कैलकुलेटर खोलो", "hi"),
        ("python में binary search लिखो", "hi"),
        ("", "en"),
    ],
)
def test_detect_language(text: str, language: str) -> None:
    assert detect_language(text) == language


def test_redaction_masks_key_shapes_and_registered_secrets() -> None:
    register_secret("my-very-secret-value")
    line = redact("key=sk-ant-api03-AbCdEf123456_-xyz and my-very-secret-value")
    assert "sk-ant-api03" not in line and "my-very-secret-value" not in line


def test_json_formatter_redacts_extra_fields(tmp_path: Path) -> None:
    record = logging.makeLogRecord(
        {"msg": "call", "levelname": "INFO", "name": "t", "api": "sk-ant-api03-ABCDEFGHIJK"}
    )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["api"] == "[REDACTED]"


def test_system_prompt_fills_placeholders(config: JarvisConfig) -> None:
    prompt = build_system_prompt(config)
    assert "{user_name}" not in prompt and "{assistant_name}" not in prompt
    assert "Tony" in prompt and "JARVIS" in prompt


async def test_terminal_renders_streamed_reply_and_code() -> None:
    out = io.StringIO()
    renderer = TerminalRenderer(out)
    for text, final in [("Hel", False), ("Hello.", False), ("Hello.", True)]:
        await renderer(
            Transcript(turn_id="t", role="assistant", text=text, language="en", final=final)
        )
    await renderer(CodeBlock(title="Demo", language="python", code="print(1)"))

    rendered = out.getvalue()
    assert rendered.startswith("Jarvis: Hello.\n")
    assert "Demo  [python]" in rendered and "print(1)" in rendered
