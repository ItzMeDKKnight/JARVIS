"""Console encoding fix shared by the app and the scripts."""

from __future__ import annotations

import io
import sys


def use_utf8_console() -> None:
    """Make stdin/stdout/stderr UTF-8 so Devanagari works everywhere.

    A real Windows console already gets UTF-8, but when output is piped or
    run under Git Bash, Python falls back to the ANSI code page (cp1252),
    which cannot encode Hindi text and would crash on the first print.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
