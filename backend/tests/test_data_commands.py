"""Export (``--export-memory``) and wipe (scripts/wipe_data.py): the user's data controls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis.config import JarvisConfig, PathsConfig
from jarvis.main import export_memory, open_memory_store

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import wipe_data  # noqa: E402


@pytest.fixture
def local_config(config: JarvisConfig, tmp_path: Path) -> JarvisConfig:
    data_dir = tmp_path / "Jarvis"
    return config.model_copy(update={"paths": PathsConfig(data_dir=data_dir)})


def test_export_writes_facts_and_summary(local_config: JarvisConfig, tmp_path: Path) -> None:
    store = open_memory_store(local_config)
    store.add_fact(
        "Tony likes chess.", category="preference", keywords=("शतरंज",), source="explicit"
    )
    store.set_summary("Talked about chess.", max_chars=100)
    store.close()

    target = tmp_path / "export.json"
    assert export_memory(local_config, target) == 1
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["summary"]["text"] == "Talked about chess."
    assert data["facts"][0]["text"] == "Tony likes chess."
    assert data["facts"][0]["keywords"] == ["शतरंज"]


def test_wipe_deletes_only_jarvis_files(local_config: JarvisConfig) -> None:
    data_dir = local_config.paths.data_dir
    assert data_dir is not None
    store = open_memory_store(local_config)
    store.close()
    (data_dir / "logs").mkdir()
    (data_dir / "logs" / "jarvis.log").write_text("x", encoding="utf-8")
    (data_dir / "debug-audio").mkdir()
    (data_dir / "hud").mkdir()
    (data_dir / "unrelated.txt").write_text("keep me", encoding="utf-8")

    targets = wipe_data.wipe_targets(data_dir, local_config.memory.db_file)
    assert {p.name for p in targets} == {"memory.db", "logs", "debug-audio", "hud"}
    assert wipe_data.wipe(targets) == []
    assert not wipe_data.remove_if_empty(data_dir)  # unrelated file keeps the folder
    assert sorted(p.name for p in data_dir.iterdir()) == ["unrelated.txt"]


def test_wipe_removes_empty_folder(local_config: JarvisConfig) -> None:
    data_dir = local_config.paths.data_dir
    assert data_dir is not None
    open_memory_store(local_config).close()
    assert wipe_data.wipe(wipe_data.wipe_targets(data_dir, "memory.db")) == []
    assert wipe_data.remove_if_empty(data_dir)
    assert not data_dir.exists()
