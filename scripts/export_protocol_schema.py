"""Regenerate protocol/events.schema.json from the pydantic models in jarvis.events.

Run after any change to the event protocol:

    .venv/Scripts/python scripts/export_protocol_schema.py

A test (test_events.py) fails if the committed file is out of date.
"""

from __future__ import annotations

import json
from pathlib import Path

from jarvis.events import protocol_json_schema

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "protocol" / "events.schema.json"


def render_schema() -> str:
    """The exact file contents, shared with the drift test."""
    return json.dumps(protocol_json_schema(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    SCHEMA_PATH.write_text(render_schema(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
