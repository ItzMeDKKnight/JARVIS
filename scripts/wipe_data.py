r"""Delete everything Jarvis has stored about you: memory, logs, debug recordings, HUD data.

    .venv/Scripts/python scripts/wipe_data.py          # shows what will go, asks to confirm
    .venv/Scripts/python scripts/wipe_data.py --yes    # no question (for scripts)

Only Jarvis's own files inside the user data folder (%LOCALAPPDATA%\Jarvis by
default, or paths.data_dir) are touched, by name. The folder itself is removed
only if nothing else is left in it, so a mis-set data_dir can never take other
files with it.

Quit Jarvis first: Windows will not delete a log file that is still open.
To keep a copy first, run ``python -m jarvis --export-memory memory.json``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from jarvis.config import ConfigError, load_config
from jarvis.os_adapters.base import user_data_dir

CONFIRM_WORD = "wipe"
# SQLite may leave these next to the database after a crash.
_DB_SIDE_FILES = ("-journal", "-wal", "-shm")
# "hud" holds the HUD window settings and Chromium cache (ui/electron/main.js).
_DIRS = ("logs", "debug-audio", "hud")


def wipe_targets(data_dir: Path, db_file: str) -> list[Path]:
    """The files and folders Jarvis creates, in the order they are deleted (existing ones only)."""
    db = data_dir / db_file
    candidates = [db, *(db.with_name(db.name + suffix) for suffix in _DB_SIDE_FILES)]
    candidates += [data_dir / name for name in _DIRS]
    return [path for path in candidates if path.exists()]


def wipe(paths: list[Path]) -> list[tuple[Path, str]]:
    """Delete ``paths``; return the ones that failed, with the reason."""
    failures: list[tuple[Path, str]] = []
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            failures.append((path, exc.strerror or str(exc)))
    return failures


def remove_if_empty(folder: Path) -> bool:
    try:
        folder.rmdir()  # fails unless the folder is empty, which is the point
    except OSError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    parser.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 2
    data_dir = user_data_dir(config.paths.data_dir)
    targets = wipe_targets(data_dir, config.memory.db_file)
    if not targets:
        print(f"Nothing to delete in {data_dir}.")
        return 0

    print("This permanently deletes:")
    for path in targets:
        print(f"  {path}")
    if not args.yes:
        answer = input(f"Type '{CONFIRM_WORD}' to confirm: ")
        if answer.strip().casefold() != CONFIRM_WORD:
            print("Cancelled. Nothing was deleted.")
            return 1

    failures = wipe(targets)
    for path, reason in failures:
        print(f"Could not delete {path}: {reason}. Is Jarvis still running?", file=sys.stderr)
    if failures:
        return 3
    remove_if_empty(data_dir)
    print("Done. Jarvis no longer remembers anything about you.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
