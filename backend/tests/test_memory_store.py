"""SQLite memory store: persistence, deduplication, blocking, deletion, export."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.memory.store import SCHEMA_VERSION, MemoryStore, MemoryStoreError


def _open(path: Path, max_facts: int = 10) -> MemoryStore:
    return MemoryStore(path, max_facts=max_facts, max_fact_chars=120)


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = _open(tmp_path / "memory.db")
    yield s
    s.close()


def _add(store: MemoryStore, text: str, keywords: tuple[str, ...] = ()) -> str:
    return store.add_fact(text, category="other", keywords=keywords, source="explicit").status


def test_fact_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    first = _open(path)
    assert _add(first, "Tony's sister is called Meera.", ("sister", "बहन")) == "added"
    first.set_summary("Tony is building Jarvis.", max_chars=100)
    first.close()

    second = _open(path)
    try:
        facts = second.facts()
        assert [f.text for f in facts] == ["Tony's sister is called Meera."]
        assert facts[0].keywords == ("sister", "बहन")
        assert second.summary() == "Tony is building Jarvis."
    finally:
        second.close()


def test_duplicates_are_detected_after_normalisation(store: MemoryStore) -> None:
    assert _add(store, "Tony likes cricket.") == "added"
    assert _add(store, "  tony LIKES cricket ") == "duplicate"
    assert len(store.facts()) == 1


def test_sensitive_facts_are_never_written(store: MemoryStore, tmp_path: Path) -> None:
    result = store.add_fact(
        "Tony's wifi password is tiger123", category="other", keywords=(), source="explicit"
    )
    assert result.status == "blocked"
    assert result.blocked_categories == ("password",)
    # Keywords are checked too.
    assert _add(store, "Tony has a bank card.", ("4111 1111 1111 1111",)) == "blocked"
    assert store.facts() == []
    assert b"tiger123" not in (tmp_path / "memory.db").read_bytes()


def test_summary_drops_sensitive_sentences(store: MemoryStore) -> None:
    store.set_summary("Tony is building Jarvis. His PIN is 4821. He likes cricket.", max_chars=500)
    assert store.summary() == "Tony is building Jarvis. He likes cricket."


def test_summary_is_truncated_and_empty_summary_clears(store: MemoryStore) -> None:
    store.set_summary("x" * 50, max_chars=10)
    assert store.summary() == "x" * 10
    store.set_summary("   ", max_chars=10)
    assert store.summary() is None


def test_store_refuses_new_facts_when_full(tmp_path: Path) -> None:
    store = _open(tmp_path / "m.db", max_facts=1)
    try:
        assert _add(store, "Fact one.") == "added"
        assert _add(store, "Fact two.") == "full"
    finally:
        store.close()


def test_long_facts_are_truncated(store: MemoryStore) -> None:
    _add(store, "a" * 500)
    assert len(store.facts()[0].text) == 120


def test_delete_and_forget_everything(store: MemoryStore, tmp_path: Path) -> None:
    _add(store, "Tony likes cricket.")
    _add(store, "Tony's favourite colour is blue.")
    store.set_summary("Chatted about cricket.", max_chars=100)
    cricket = next(f for f in store.facts() if "cricket" in f.text)
    assert store.delete_facts([cricket.id]) == 1
    assert [f.text for f in store.facts()] == ["Tony's favourite colour is blue."]

    store.forget_everything()
    assert store.facts() == []
    assert store.summary() is None
    # secure_delete: the forgotten text is gone from the file, not just unlinked.
    assert b"cricket" not in (tmp_path / "memory.db").read_bytes()


def test_export_contains_everything_in_insertion_order(store: MemoryStore) -> None:
    _add(store, "First fact.")
    _add(store, "Second fact.")
    store.set_summary("A summary.", max_chars=100)
    data = store.export()
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["summary"]["text"] == "A summary."  # type: ignore[index]
    assert [f["text"] for f in data["facts"]] == ["First fact.", "Second fact."]  # type: ignore[union-attr]


def test_database_from_a_newer_version_is_not_touched(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(MemoryStoreError, match="newer"):
        _open(path)
