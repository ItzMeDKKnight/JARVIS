"""SQLite access for long-term memory: facts about the user and one rolling summary.

Why plain SQLite: it ships with Python, makes no network calls, keeps
everything in one file under the user data folder, and is trivially exported
or wiped. A personal assistant holds at most a few hundred facts, so recall
does not need a vector index.

Every write passes the sensitive-data guard (:mod:`jarvis.memory.sensitive`)
here, at the lowest layer, so no caller (tool, consolidation, a future
feature) can store a password or ID number by forgetting to check.

The methods are synchronous and fast; async callers run them in a worker
thread. One connection is shared behind a lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from jarvis.memory.sensitive import drop_sensitive_sentences, find_sensitive
from jarvis.text import normalize_name

SCHEMA_VERSION = 1
FactSource = Literal["explicit", "extracted"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    normalized TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    keywords TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS summary (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class MemoryStoreError(Exception):
    """The database could not be opened or is from an unknown future version."""


@dataclass(frozen=True)
class Fact:
    id: int
    text: str
    category: str
    keywords: tuple[str, ...]
    source: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AddResult:
    """Outcome of :meth:`MemoryStore.add_fact`. Exactly one of the fields is meaningful."""

    status: Literal["added", "duplicate", "blocked", "full"]
    fact: Fact | None = None
    blocked_categories: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        text=row["text"],
        category=row["category"],
        keywords=tuple(json.loads(row["keywords"])),
        source=row["source"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class MemoryStore:
    def __init__(self, path: Path, *, max_facts: int, max_fact_chars: int) -> None:
        self.path = path
        self._max_facts = max_facts
        self._max_fact_chars = max_fact_chars
        self._lock = threading.Lock()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(path, check_same_thread=False)
        except (OSError, sqlite3.Error) as exc:
            raise MemoryStoreError(f"cannot open memory database {path}: {exc}") from exc
        self._db.row_factory = sqlite3.Row
        # Overwrite deleted rows with zeros, so "forget" really removes the text from the file.
        self._db.execute("PRAGMA secure_delete = ON")
        self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise MemoryStoreError(
                f"{self.path} was written by a newer Jarvis (schema {version}); "
                "refusing to touch it"
            )
        with self._db:
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- facts ----------------------------------------------------------------------

    def add_fact(
        self, text: str, *, category: str, keywords: tuple[str, ...], source: FactSource
    ) -> AddResult:
        text = " ".join(text.split())[: self._max_fact_chars]
        blocked = find_sensitive(" ".join((text, *keywords)))
        if blocked:
            return AddResult("blocked", blocked_categories=tuple(m.category for m in blocked))
        normalized = normalize_name(text)
        clean_keywords = tuple(dict.fromkeys(k.strip() for k in keywords if k.strip()))
        with self._lock:
            existing = self._db.execute(
                "SELECT * FROM facts WHERE normalized = ?", (normalized,)
            ).fetchone()
            if existing is not None:
                return AddResult("duplicate", fact=_row_to_fact(existing))
            count = self._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            if count >= self._max_facts:
                return AddResult("full")
            now = _now()
            with self._db:
                cursor = self._db.execute(
                    "INSERT INTO facts (text, normalized, category, keywords, source, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        text,
                        normalized,
                        category,
                        json.dumps(clean_keywords, ensure_ascii=False),
                        source,
                        now,
                        now,
                    ),
                )
            row = self._db.execute(
                "SELECT * FROM facts WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return AddResult("added", fact=_row_to_fact(row))

    def facts(self) -> list[Fact]:
        """All facts, newest first."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM facts ORDER BY id DESC").fetchall()
        return [_row_to_fact(row) for row in rows]

    def delete_facts(self, ids: list[int]) -> int:
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._lock, self._db:
            cursor = self._db.execute(f"DELETE FROM facts WHERE id IN ({placeholders})", ids)  # noqa: S608
        return cursor.rowcount

    # -- rolling summary ------------------------------------------------------------

    def summary(self) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT text FROM summary WHERE id = 1").fetchone()
        return None if row is None else str(row["text"])

    def set_summary(self, text: str, *, max_chars: int) -> None:
        clean = drop_sensitive_sentences(" ".join(text.split()))[:max_chars]
        with self._lock, self._db:
            if not clean:
                self._db.execute("DELETE FROM summary")
                return
            self._db.execute(
                "INSERT INTO summary (id, text, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET text = excluded.text, "
                "updated_at = excluded.updated_at",
                (clean, _now()),
            )

    def clear_summary(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM summary")

    # -- whole store ----------------------------------------------------------------

    def forget_everything(self) -> None:
        """Delete every fact and the summary (secure_delete zeroes the freed pages)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM facts")
            self._db.execute("DELETE FROM summary")

    def export(self) -> dict[str, object]:
        """Everything stored, as plain JSON-ready data (for ``--export-memory``)."""
        with self._lock:
            row = self._db.execute("SELECT text, updated_at FROM summary WHERE id = 1").fetchone()
        return {
            "schema_version": SCHEMA_VERSION,
            "exported_at": _now(),
            "summary": None if row is None else {"text": row[0], "updated_at": row[1]},
            "facts": [asdict(fact) for fact in reversed(self.facts())],
        }
