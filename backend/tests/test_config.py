from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import ConfigError, JarvisConfig, load_api_key, load_config


def test_repo_config_loads(config: JarvisConfig) -> None:
    assert config.assistant.user_name == "Tony"
    assert config.apps, "apps.yaml should provide at least one app"
    assert "{user_name}" in config.persona_template
    assert config.llm.router.default_route in config.llm.routes


def test_every_tool_has_narration_in_both_languages(config: JarvisConfig) -> None:
    for tool in ("open_app", "open_url", "web_search", "show_code"):
        line = config.narration[tool]
        assert line.en and line.hi


def _write_config(tmp_path: Path, mutate: str = "") -> Path:
    source = load_config(local_overrides=False)
    text = (Path(__file__).resolve().parents[2] / "config" / "config.yaml").read_text("utf-8")
    (tmp_path / "persona.md").write_text(source.persona_template, encoding="utf-8")
    (tmp_path / "apps.yaml").write_text("apps: []\n", encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(text + mutate, encoding="utf-8")
    return path


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "\nsurprise_section: true\n")
    with pytest.raises(ConfigError, match="surprise_section"):
        load_config(path)


def test_router_referencing_missing_route_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    text = path.read_text("utf-8").replace("default_route: routine", "default_route: nope")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="undefined routes"):
        load_config(path)


def test_shipped_config_has_no_personal_name(config: JarvisConfig) -> None:
    assert config.assistant.user_name == "Tony"
    assert config.session.greeting == "Hello Tony, whatsup?"
    assert config.messages.didnt_catch.hi.startswith("माफ़ कीजिए टोनी")


def test_local_overrides_are_merged_and_fill_name_placeholders(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    (tmp_path / "config.local.yaml").write_text(
        "assistant:\n  user_name: Pepper\n  user_name_hi: पेपर\n", encoding="utf-8"
    )
    config = load_config(path)
    assert config.assistant.user_name == "Pepper"
    assert config.assistant.persona_file.name == "persona.md"  # untouched keys survive
    assert config.session.greeting == "Hello Pepper, whatsup?"
    assert config.messages.didnt_catch.hi.startswith("माफ़ कीजिए पेपर")
    assert "{spent}" in config.messages.budget_warning.en  # other placeholders survive

    assert load_config(path, local_overrides=False).assistant.user_name == "Tony"


def test_missing_config_file_gives_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_api_key_from_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert load_api_key(env_file) == "from-env"


def test_api_key_from_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert load_api_key(env_file) == "from-file"


def test_placeholder_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-...\n", encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="not set"):
        load_api_key(env_file)


def test_data_dir_inside_repo_is_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path)
    repo_folder = (Path(__file__).resolve().parents[2] / "build" / "data").as_posix()
    text = path.read_text("utf-8").replace("  data_dir: null", f"  data_dir: {repo_folder}")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="must not be inside the repository"):
        load_config(path)


def test_every_routed_model_has_a_price(config: JarvisConfig) -> None:
    models = {route.model for route in config.llm.routes.values()}
    models |= {config.search.model, config.memory.model}
    assert models <= config.pricing.models.keys()


def test_every_app_has_a_devanagari_alias(config: JarvisConfig) -> None:
    from jarvis.utils.language import is_devanagari

    for app in config.apps:
        assert any(any(is_devanagari(c) for c in alias) for alias in app.aliases), app.id
