"""Cutting streamed text into speakable pieces, and splitting pieces by script.

Two jobs, both needed for low latency and correct pronunciation:

1. :class:`SentenceChunker` turns a stream of LLM text fragments into chunks
   that can be synthesised immediately. Waiting for the whole reply would add
   seconds of silence; cutting at sentence ends (and, for the very first
   chunk, at a comma) lets audio start after the first few words.
2. :func:`split_by_script` splits a chunk into Latin and Devanagari runs, so
   each run goes to the voice and phonemiser for its language. Hindi text
   read by an English phonemiser (or the reverse) is unintelligible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis.config import SegmenterConfig
from jarvis.utils.language import Language, is_devanagari

# Sentence end: . ! ? or Hindi danda/double danda, followed by whitespace.
# Requiring whitespace afterwards avoids cutting "3.14" or "e.g.x".
_SENTENCE_END = re.compile(r"[.!?।॥]+[\"')\]]*\s")
_CLAUSE_END = re.compile(r"[,;:—]\s")
_URL = re.compile(r"https?://\S+")
# Markdown the persona forbids but a model might still emit; never read aloud.
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MARKDOWN_SYMBOLS = re.compile(r"[*_#`>|]+")


def clean_for_speech(text: str) -> str:
    """Remove URLs and markdown symbols (keeping link text), and collapse whitespace."""
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _MARKDOWN_SYMBOLS.sub(" ", text)
    return " ".join(text.split())


class SentenceChunker:
    """Accumulates streamed fragments and releases speakable chunks."""

    def __init__(self, config: SegmenterConfig) -> None:
        self._first_min = config.first_chunk_min_chars
        self._max = config.max_chunk_chars
        self._buffer = ""
        self._emitted_any = False

    def feed(self, fragment: str) -> list[str]:
        """Add a fragment; return the chunks that are now complete (possibly none)."""
        self._buffer += fragment
        chunks: list[str] = []
        while (cut := self._next_cut()) is not None:
            chunk, self._buffer = self._buffer[:cut], self._buffer[cut:]
            self._emit(chunk, chunks)
        return chunks

    def flush(self) -> list[str]:
        """Release whatever is left at the end of the reply."""
        chunks: list[str] = []
        self._emit(self._buffer, chunks)
        self._buffer = ""
        return chunks

    def _emit(self, raw: str, out: list[str]) -> None:
        cleaned = clean_for_speech(raw)
        if any(char.isalnum() for char in cleaned):
            out.append(cleaned)
            self._emitted_any = True

    def _next_cut(self) -> int | None:
        """Index to cut the buffer at, or None to keep waiting for more text."""
        sentence = _SENTENCE_END.search(self._buffer)
        if sentence is not None:
            return sentence.end()
        if not self._emitted_any and len(self._buffer) >= self._first_min:
            clause = _last_match(_CLAUSE_END, self._buffer, start=self._first_min // 2)
            if clause is not None:
                return clause
        if len(self._buffer) >= self._max:
            # A run-on sentence: cut at the last clause or word boundary to bound latency.
            clause = _last_match(_CLAUSE_END, self._buffer)
            if clause is not None:
                return clause
            space = self._buffer.rfind(" ", 0, self._max)
            return space + 1 if space > 0 else self._max
        return None


def _last_match(pattern: re.Pattern[str], text: str, start: int = 0) -> int | None:
    ends = [m.end() for m in pattern.finditer(text, start)]
    return ends[-1] if ends else None


@dataclass(frozen=True)
class ScriptSegment:
    language: Language
    text: str


def _script_of(char: str) -> Language | None:
    """Language of a letter, or None for neutral characters (digits, spaces, punctuation)."""
    if is_devanagari(char):
        # Danda marks are punctuation shared by both runs, not Hindi letters.
        return None if char in "।॥" else "hi"
    if char.isascii() and char.isalpha():
        return "en"
    return None


def split_by_script(text: str, default: Language = "en") -> list[ScriptSegment]:
    """Split text into runs of Latin (en) and Devanagari (hi) script.

    Neutral characters (digits, spaces, punctuation) stay with the run they
    follow, so "तापमान 31 डिग्री है।" stays one Hindi segment. Neutral text
    before the first letter joins the first run. Text with no letters at all
    returns a single segment in ``default``.
    """
    segments: list[ScriptSegment] = []
    current: Language | None = None
    pending = ""
    for char in text:
        script = _script_of(char)
        if script is None or script == current:
            pending += char
            continue
        if current is not None:
            # Move trailing spaces of the previous run onto the new one, for clean edges.
            stripped = pending.rstrip()
            segments.append(ScriptSegment(current, stripped))
            pending = pending[len(stripped) :]
        current = script
        pending += char
    if pending.strip():
        segments.append(ScriptSegment(current or default, pending))
    return [ScriptSegment(s.language, s.text.strip()) for s in segments if s.text.strip()]
