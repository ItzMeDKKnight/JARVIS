"""Understanding yes/no answers in English, Hindi and Hinglish.

Shared by the typed and the spoken confirmation flows. "No" words win over
"yes" words ("no, don't open it, okay?" is a no), and anything unclear counts
as no: a confirmation that guards an app launch should fail closed.
"""

from __future__ import annotations

from jarvis.text import normalize_name

_NO_PHRASES = (
    "no",
    "nope",
    "nah",
    "don't",
    "dont",
    "do not",
    "cancel",
    "stop",
    "never mind",
    "nahi",
    "nahin",
    "mat",
    "नहीं",
    "नही",
    "मत",
    "रहने दो",
)
_YES_PHRASES = (
    "yes",
    "yeah",
    "yep",
    "yup",
    "sure",
    "ok",
    "okay",
    "go ahead",
    "do it",
    "open it",
    "please",
    "haan",
    "han",
    "ha",
    "haanji",
    "ji",
    "theek hai",
    "thik hai",
    "हाँ",
    "हां",
    "हा",
    "जी",
    "हाँजी",
    "ठीक है",
    "खोल दो",
    "कर दो",
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return f" {normalize_name(phrase)} " in f" {text} "


def is_yes(answer: str | None) -> bool:
    """True only for a clear yes with no no-word in it."""
    if not answer:
        return False
    text = normalize_name(answer.replace("'", ""))
    if any(_contains_phrase(text, p.replace("'", "")) for p in _NO_PHRASES):
        return False
    return any(_contains_phrase(text, p) for p in _YES_PHRASES)
