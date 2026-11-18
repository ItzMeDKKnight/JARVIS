"""Typed configuration loading and validation.

All tunables live in ``config/config.yaml`` and ``config/apps.yaml``. They are
parsed into frozen pydantic models here so that a typo or a missing value fails
loudly at startup instead of surfacing as a strange bug mid-conversation.

The API key is deliberately *not* part of :class:`JarvisConfig`: it is read
separately by :func:`load_api_key` so that dumping or logging the config can
never leak it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jarvis.utils.language import Language

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"
CONFIG_ENV_VAR = "JARVIS_CONFIG"
# Optional, git-ignored overrides that sit next to the config file (e.g. your own name).
LOCAL_CONFIG_NAME = "config.local.yaml"
API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


class ConfigError(Exception):
    """Raised when configuration files are missing or invalid."""


class _Frozen(BaseModel):
    """Base for config sections: immutable and strict about unknown keys."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AssistantConfig(_Frozen):
    name: str = "JARVIS"
    user_name: str
    # Devanagari spelling of the name, used by the Hindi lines ({user_name_hi}).
    user_name_hi: str = ""
    persona_file: Path
    apps_file: Path


ThinkingMode = Literal["disabled", "adaptive", "between_tools"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class RouteConfig(_Frozen):
    """One model plus the settings used to call it."""

    model: str
    max_tokens: int = Field(gt=0)
    effort: Effort
    thinking: ThinkingMode
    fallbacks: Literal["default"] | None = None


class RouterRule(_Frozen):
    route: str
    keywords: tuple[str, ...] = Field(min_length=1)
    ignore_phrases: tuple[str, ...] = ()


class RouterConfig(_Frozen):
    default_route: str
    rules: tuple[RouterRule, ...] = ()


class LLMConfig(_Frozen):
    request_timeout_s: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    max_tool_rounds: int = Field(gt=0)
    history_max_turns: int = Field(gt=0)
    # Cost cap: output tokens one user turn may use across all its tool rounds.
    max_output_tokens_per_turn: int = Field(gt=0)
    # A round with less budget left than this is not started (it could only think, not answer).
    min_tokens_per_round: int = Field(gt=0)
    # Add the current local date and time to every request (after the cached prefix).
    time_note: bool = True
    routes: dict[str, RouteConfig]
    router: RouterConfig

    @model_validator(mode="after")
    def _routes_exist(self) -> LLMConfig:
        """Every route the router can pick must be defined, or routing would crash later."""
        referenced = {self.router.default_route, *(rule.route for rule in self.router.rules)}
        missing = referenced - self.routes.keys()
        if missing:
            raise ValueError(f"router references undefined routes: {sorted(missing)}")
        return self


class UserLocation(_Frozen):
    type: Literal["approximate"] = "approximate"
    city: str | None = None
    region: str | None = None
    country: str | None = None
    timezone: str | None = None


class SearchConfig(_Frozen):
    provider: Literal["anthropic"]
    model: str
    tool_type: Literal["web_search_20260209", "web_search_20250305"]
    max_uses: int = Field(gt=0)
    max_tokens: int = Field(gt=0)
    effort: Effort
    max_sources: int = Field(gt=0)
    timeout_s: float = Field(gt=0)
    user_location: UserLocation | None = None


class ToolsConfig(_Frozen):
    timeout_s: float = Field(gt=0)
    search_start_menu_for_unknown_apps: bool = True
    confirm_timeout_s: float = Field(gt=0)


class PdfConfig(_Frozen):
    """create_pdf: where PDFs go and which font renders them (must cover Devanagari)."""

    # Folder in the project root. If something with this name exists that Jarvis did not
    # create, fallback_dir_name (then fallback_dir_name_2, _3, ...) is used instead.
    output_dir_name: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    fallback_dir_name: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    # %ENV_VARS% are expanded. Nirmala UI ships with Windows and covers Latin + Devanagari.
    font_file: str = Field(min_length=1)
    font_index_regular: int = Field(ge=0)  # face numbers inside a .ttc collection
    font_index_bold: int = Field(ge=0)
    max_content_chars: int = Field(gt=0)


class LocalizedText(_Frozen):
    """A line available in every supported reply language."""

    en: str
    hi: str

    def for_language(self, language: Language) -> str:
        return self.hi if language == "hi" else self.en


class MessagesConfig(_Frozen):
    api_unreachable: LocalizedText
    refusal: LocalizedText
    internal_error: LocalizedText
    confirm_unknown_app: LocalizedText
    didnt_catch: LocalizedText
    speech_unavailable: LocalizedText
    turn_budget_exhausted: LocalizedText
    budget_warning: LocalizedText
    budget_exhausted: LocalizedText
    voice_unavailable: LocalizedText
    confirm_forget_matches: LocalizedText
    confirm_forget_all: LocalizedText


class MemoryConfig(_Frozen):
    """Long-term memory (Phase 4): SQLite file, consolidation call, recall limits."""

    enabled: bool = True
    # File name inside paths.data_dir; never inside the repository.
    db_file: str = Field(min_length=1)
    model: str
    effort: Effort
    thinking: ThinkingMode
    max_tokens: int = Field(gt=0)
    timeout_s: float = Field(gt=0)
    consolidate_every_turns: int = Field(gt=0)
    max_buffered_messages: int = Field(gt=0)
    shutdown_flush_timeout_s: float = Field(gt=0)
    max_summary_chars: int = Field(gt=0)
    max_facts: int = Field(gt=0)
    max_fact_chars: int = Field(gt=0)
    recall_max_facts: int = Field(gt=0)
    recall_min_score: float = Field(gt=0)
    search_max_results: int = Field(gt=0)


class ModelPrice(_Frozen):
    """USD per million tokens."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float = Field(ge=0)


class BudgetConfig(_Frozen):
    """Hard spending cap for one run of Jarvis (all Claude calls together)."""

    session_cap_usd: float = Field(gt=0)
    warn_at_fraction: float = Field(gt=0, lt=1)
    # Conservative token estimate for text not yet sent (Hindi uses more tokens per character).
    chars_per_token_estimate: float = Field(gt=0)
    # Search results count as input tokens; reserve this many for one search call.
    search_result_tokens_estimate: int = Field(ge=0)


class ServerConfig(_Frozen):
    """The local WebSocket the HUD connects to. Loopback only, by construction."""

    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(ge=0, le=65535)  # 0 = any free port, reported on stdout
    auth_timeout_s: float = Field(gt=0)
    max_message_bytes: int = Field(gt=0)
    max_clients: int = Field(gt=0)
    allowed_origins: tuple[str, ...] = Field(min_length=1)


class PricingConfig(_Frozen):
    """List prices used for the local cost log. An estimate, not a bill."""

    cache_write_multiplier: float = Field(ge=1)
    web_search_per_1000: float = Field(ge=0)
    models: dict[str, ModelPrice]


class LoggingConfig(_Frozen):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_bytes: int = Field(gt=0)
    backup_count: int = Field(ge=0)
    log_transcripts: bool = False


class PathsConfig(_Frozen):
    data_dir: Path | None = None
    # None = <repo>/models (git-ignored). Model files are only ever loaded from here.
    models_dir: Path | None = None

    @model_validator(mode="after")
    def _data_outside_repo(self) -> PathsConfig:
        """Logs and the memory database are personal data; they must never land in the repo."""
        if self.data_dir is not None and self.data_dir.expanduser().resolve().is_relative_to(
            REPO_ROOT
        ):
            raise ValueError("paths.data_dir must not be inside the repository")
        return self

    def resolved_models_dir(self) -> Path:
        return self.models_dir if self.models_dir is not None else REPO_ROOT / "models"


class LaunchSpec(_Frozen):
    kind: Literal["shell", "path", "appx"]
    target: str = Field(min_length=1)


class AppEntry(_Frozen):
    id: str
    display_name: str
    aliases: tuple[str, ...] = Field(min_length=1)
    launch: LaunchSpec


class WebsiteEntry(_Frozen):
    """A website Jarvis can open by its spoken name ("open GitHub", "जीमेल खोलो")."""

    id: str
    display_name: str
    aliases: tuple[str, ...] = Field(min_length=1)
    url: str = Field(pattern=r"^https?://[^\s@]+$")


class AppsFile(_Frozen):
    apps: tuple[AppEntry, ...]
    websites: tuple[WebsiteEntry, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> AppsFile:
        for kind, ids in (
            ("app", [app.id for app in self.apps]),
            ("website", [site.id for site in self.websites]),
        ):
            duplicates = {i for i in ids if ids.count(i) > 1}
            if duplicates:
                raise ValueError(f"duplicate {kind} ids: {sorted(duplicates)}")
        return self


class AudioConfig(_Frozen):
    # Device name substring or PortAudio index; None = the Windows default device.
    input_device: str | int | None = None
    output_device: str | int | None = None
    input_sample_rate: int = Field(gt=0)
    block_ms: int = Field(gt=0, le=200)
    level_floor_db: float
    level_ceiling_db: float
    # PortAudio output latency. "low" (~100 ms on MME) starts each reply sooner than
    # "high" (~200 ms); switch back to "high" if playback ever crackles.
    output_latency: Literal["low", "high"] = "high"
    debug_save_audio: bool = False

    @model_validator(mode="after")
    def _level_range(self) -> AudioConfig:
        if self.level_floor_db >= self.level_ceiling_db:
            raise ValueError("level_floor_db must be below level_ceiling_db")
        return self


PushToTalkKeyName = Literal["left_ctrl", "right_ctrl", "f9", "caps_lock"]


class VoiceConfig(_Frozen):
    triggers: tuple[TriggerName, ...] = Field(min_length=1)
    push_to_talk_key: PushToTalkKeyName
    hold_threshold_ms: int = Field(ge=0)
    poll_hz: int = Field(gt=0, le=200)
    min_utterance_ms: int = Field(ge=0)
    max_utterance_s: float = Field(gt=0)
    confirm_listen_timeout_s: float = Field(gt=0)


TriggerName = Literal["wake_word", "push_to_talk"]


class WakeWordConfig(_Frozen):
    melspectrogram_file: str
    embedding_file: str
    model_file: str
    threshold: float = Field(gt=0, lt=1)
    # Stricter while Jarvis is talking: laptop speakers leak into the mic.
    speaking_threshold: float = Field(gt=0, lt=1)
    barge_in: bool = True
    cooldown_ms: int = Field(ge=0)


class VADConfig(_Frozen):
    threshold: float = Field(gt=0, lt=1)
    neg_threshold: float = Field(gt=0, lt=1)
    min_speech_ms: int = Field(ge=0)
    end_silence_ms: int = Field(gt=0)
    start_timeout_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> VADConfig:
        if self.neg_threshold > self.threshold:
            raise ValueError("vad.neg_threshold must not exceed vad.threshold")
        return self


class SessionConfig(_Frozen):
    greeting: str
    # Greet only when the last conversation ended at least this long ago (0 = always).
    greeting_after_idle_min: float = Field(ge=0)
    follow_up_s: float = Field(ge=0)
    # Ignore the mic briefly after Jarvis stops talking (speaker tail / room echo).
    follow_up_guard_ms: int = Field(ge=0)
    preroll_ms: int = Field(ge=0)


class STTConfig(_Frozen):
    model: Literal["small", "medium", "large-v3-turbo"]
    compute_type: Literal["int8", "int8_float32", "float32"]
    cpu_threads: int = Field(gt=0)
    beam_size: int = Field(gt=0)
    allowed_languages: tuple[Language, ...] = Field(min_length=1)
    vad_filter: bool = True


class KokoroConfig(_Frozen):
    model_file: str
    voices_file: str
    voices: dict[Language, str]
    espeak_languages: dict[Language, str]
    threads: int = Field(gt=0)


class PiperConfig(_Frozen):
    voices: dict[Language, str]


class PostFXConfig(_Frozen):
    enabled: bool
    low_shelf_hz: float = Field(gt=0)
    low_shelf_gain_db: float
    reverb_wet: float = Field(ge=0, le=1)
    reverb_decay_s: float = Field(gt=0)
    doubling_delay_ms: float = Field(ge=0)
    doubling_gain_db: float


class SegmenterConfig(_Frozen):
    first_chunk_min_chars: int = Field(gt=0)
    max_chunk_chars: int = Field(gt=0)


class TTSConfig(_Frozen):
    engine: Literal["kokoro", "piper"]
    speed: float = Field(gt=0.5, le=2.0)
    kokoro: KokoroConfig
    piper: PiperConfig
    postfx: PostFXConfig
    segmenter: SegmenterConfig
    # Rendered audio kept for complete lines Jarvis says often (narration, greeting,
    # fallbacks), so a repeat plays at once instead of being synthesised again. 0 = off.
    phrase_cache_size: int = Field(default=64, ge=0)


class JarvisConfig(_Frozen):
    """The full, validated configuration (minus secrets)."""

    assistant: AssistantConfig
    llm: LLMConfig
    search: SearchConfig
    tools: ToolsConfig
    pdf: PdfConfig
    narration: dict[str, LocalizedText]
    messages: MessagesConfig
    logging: LoggingConfig
    paths: PathsConfig
    audio: AudioConfig
    voice: VoiceConfig
    wake_word: WakeWordConfig
    vad: VADConfig
    session: SessionConfig
    stt: STTConfig
    tts: TTSConfig
    memory: MemoryConfig
    pricing: PricingConfig
    budget: BudgetConfig
    server: ServerConfig
    apps: tuple[AppEntry, ...] = ()
    websites: tuple[WebsiteEntry, ...] = ()
    persona_template: str = ""


def _read_yaml(path: Path) -> object:
    try:
        with path.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc


def resolve_config_path(explicit: Path | None = None) -> Path:
    """Pick the config file: explicit argument, then $JARVIS_CONFIG, then the repo default."""
    if explicit is not None:
        return explicit
    from_env = os.environ.get(CONFIG_ENV_VAR)
    return Path(from_env) if from_env else DEFAULT_CONFIG_PATH


def _merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
    """Deep-merge ``override`` into ``base``: nested mappings merge, everything else replaces."""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _merge(current, value)
        else:
            merged[key] = value
    return merged


def _fill_names(node: object, names: dict[str, str]) -> object:
    """Replace {user_name}-style placeholders in every string of the raw config.

    Plain ``str.replace`` (not ``format``) so other placeholders such as
    ``{spent}`` survive for the code that fills them later.
    """
    if isinstance(node, str):
        for placeholder, value in names.items():
            node = node.replace(placeholder, value)
        return node
    if isinstance(node, dict):
        return {key: _fill_names(value, names) for key, value in node.items()}
    if isinstance(node, list):
        return [_fill_names(value, names) for value in node]
    return node


def load_config(path: Path | None = None, *, local_overrides: bool = True) -> JarvisConfig:
    """Load and validate config.yaml together with the apps file and persona it points to.

    A ``config.local.yaml`` next to the config file, if present, is merged on top.
    It is git-ignored, so personal values (your name) never end up in the repository.
    """
    config_path = resolve_config_path(path).resolve()
    raw = _read_yaml(config_path)
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a YAML mapping")

    local_path = config_path.parent / LOCAL_CONFIG_NAME
    if local_overrides and local_path.is_file():
        local = _read_yaml(local_path)
        if local is not None and not isinstance(local, dict):
            raise ConfigError(f"{local_path} must contain a YAML mapping")
        raw = _merge(raw, local or {})

    assistant = raw.get("assistant")
    if isinstance(assistant, dict):
        user_name = str(assistant.get("user_name", ""))
        names = {
            "{user_name}": user_name,
            "{user_name_hi}": str(assistant.get("user_name_hi") or user_name),
        }
        raw = {key: _fill_names(value, names) for key, value in raw.items()}

    try:
        base = JarvisConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {config_path}:\n{exc}") from exc

    config_dir = config_path.parent
    apps_path = config_dir / base.assistant.apps_file
    persona_path = config_dir / base.assistant.persona_file

    try:
        apps = AppsFile.model_validate(_read_yaml(apps_path))
    except ValidationError as exc:
        raise ConfigError(f"invalid app list in {apps_path}:\n{exc}") from exc

    try:
        persona = persona_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"persona file not found: {persona_path}") from exc

    return base.model_copy(
        update={"apps": apps.apps, "websites": apps.websites, "persona_template": persona}
    )


def load_api_key(env_file: Path | None = None) -> str:
    """Return the Anthropic API key from the environment or the git-ignored .env file.

    The environment wins over .env so a one-off override never requires editing
    the file. The value is returned to the caller only; it is never logged.
    """
    key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not key:
        dotenv_path = env_file if env_file is not None else REPO_ROOT / ".env"
        key = (dotenv_values(dotenv_path).get(API_KEY_ENV_VAR) or "").strip()
    if not key or key.endswith("..."):
        raise ConfigError(
            f"{API_KEY_ENV_VAR} is not set. Copy .env.example to .env and add your key."
        )
    return key
