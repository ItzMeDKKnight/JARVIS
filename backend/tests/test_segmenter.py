from __future__ import annotations

import pytest

from jarvis.config import SegmenterConfig
from jarvis.tts.segmenter import (
    ScriptSegment,
    SentenceChunker,
    clean_for_speech,
    split_by_script,
)

CONFIG = SegmenterConfig(first_chunk_min_chars=25, max_chunk_chars=120)


def _stream(text: str, step: int = 3) -> list[str]:
    """Feed text in small fragments, like a token stream, and collect all chunks."""
    chunker = SentenceChunker(CONFIG)
    chunks: list[str] = []
    for i in range(0, len(text), step):
        chunks += chunker.feed(text[i : i + step])
    return chunks + chunker.flush()


def test_cuts_at_sentence_ends() -> None:
    assert _stream("Good evening, Tony. It is 31 degrees in Mumbai. Sunny all day!") == [
        "Good evening, Tony.",
        "It is 31 degrees in Mumbai.",
        "Sunny all day!",
    ]


def test_hindi_danda_ends_a_sentence() -> None:
    assert _stream("आज भोपाल में धूप है। तापमान 31 डिग्री है।") == [
        "आज भोपाल में धूप है।",
        "तापमान 31 डिग्री है।",
    ]


def test_first_chunk_may_end_at_a_comma_for_faster_first_audio() -> None:
    chunks = _stream("This function halves the range each step, so it runs in logarithmic time.")
    assert chunks[0] == "This function halves the range each step,"


def test_later_chunks_do_not_cut_at_commas() -> None:
    text = "Done. Then, after that, and also this, it finishes."
    assert _stream(text) == ["Done.", "Then, after that, and also this, it finishes."]


def test_decimals_and_versions_are_not_sentence_ends() -> None:
    assert _stream("Pi is 3.14 and Python 3.12 is out.") == ["Pi is 3.14 and Python 3.12 is out."]


def test_run_on_text_is_bounded() -> None:
    text = "word " * 80
    chunks = _stream(text)
    assert len(chunks) > 1
    assert all(len(c) <= CONFIG.max_chunk_chars for c in chunks)


def test_punctuation_only_chunks_are_dropped() -> None:
    assert _stream("... !!") == []


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("See **this** `code`.", "See this code ."),
        ("Docs: https://docs.python.org/3/ here.", "Docs: here."),
        ("## Title", "Title"),
        ("[the docs](https://x.y)", "the docs"),
    ],
)
def test_clean_for_speech(raw: str, spoken: str) -> None:
    assert clean_for_speech(raw) == spoken


def test_split_pure_languages() -> None:
    assert split_by_script("Opening VS Code.") == [ScriptSegment("en", "Opening VS Code.")]
    assert split_by_script("तापमान 31 डिग्री है।") == [ScriptSegment("hi", "तापमान 31 डिग्री है।")]


def test_split_hinglish_routes_each_script_to_its_voice() -> None:
    assert split_by_script("मैंने VS Code खोल दिया है।") == [
        ScriptSegment("hi", "मैंने"),
        ScriptSegment("en", "VS Code"),
        ScriptSegment("hi", "खोल दिया है।"),
    ]


def test_split_neutral_only_uses_default() -> None:
    assert split_by_script("42!", default="hi") == [ScriptSegment("hi", "42!")]
    assert split_by_script("   ") == []
