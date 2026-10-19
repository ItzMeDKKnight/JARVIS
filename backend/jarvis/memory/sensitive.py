"""Detects data that must never be stored in long-term memory.

The brief forbids storing passwords, government ID numbers and payment card
numbers "even if I say them". This is enforced here, in code, rather than by
asking the model nicely: every write to the memory store and every transcript
sent for summarisation passes through these checks.

The rules are deliberately conservative. A false positive costs one politely
refused note ("remember I use a password manager" is refused too); a false
negative would put a secret on disk.

Patterns cover what speech-to-text and typing realistically produce: digits
with spaces or dashes, Devanagari digits, lower-case ID letters, and the Hindi
words for "password", "PIN" and friends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis.text import normalize_name

# Devanagari digits ०-९ count the same as 0-9.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Secrets: the mere mention is enough to refuse (the value may follow in any form).
# "pin" also matches postal PIN codes; refusing those is the safe kind of mistake.
_SECRET_WORDS = (
    "password",
    "passwords",
    "passcode",
    "passwd",
    "pass word",
    "pin",
    "pin code",
    "pincode",
    "upi pin",
    "atm pin",
    "otp",
    "cvv",
    "cvc",
    "security code",
    "secret key",
    "api key",
    "private key",
    "recovery code",
    "seed phrase",
    "पासवर्ड",
    "पिन",
    "ओटीपी",
    "सीवीवी",
)

# Government IDs: refused when mentioned together with something that looks like a number.
_ID_WORDS = (
    "aadhaar",
    "aadhar",
    "adhaar",
    "pan card",
    "pan number",
    "passport",
    "voter id",
    "driving licence",
    "driving license",
    "licence number",
    "license number",
    "social security",
    "ssn",
    "आधार",
    "पैन",
    "पासपोर्ट",
    "वोटर",
    "लाइसेंस",
)

_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)")
_PAN = re.compile(r"\b[a-z]{5}\d{4}[a-z]\b", re.IGNORECASE)
_PASSPORT_IN = re.compile(r"\b[a-z]\d{7}\b", re.IGNORECASE)
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_API_KEY = re.compile(r"\bsk-[a-z0-9_\-]{8,}", re.IGNORECASE)
_NUMBERISH = re.compile(r"\d{4,}|\d(?:[ -]?\d){3,}")
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


@dataclass(frozen=True)
class SensitiveMatch:
    category: str  # "password", "government_id", "payment_card" or "api_key"


def _luhn_ok(digits: str) -> bool:
    """The checksum every payment card number satisfies; rules out most random numbers."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _has_phrase(normalized: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {normalized} "
    return any(f" {normalize_name(phrase)} " in padded for phrase in phrases)


def find_sensitive(text: str) -> list[SensitiveMatch]:
    """Return every sensitive category found in ``text`` (empty when it is safe to store)."""
    plain = text.translate(_DEVANAGARI_DIGITS)
    normalized = normalize_name(plain)
    found: list[str] = []

    if _has_phrase(normalized, _SECRET_WORDS):
        found.append("password")
    if _API_KEY.search(plain):
        found.append("api_key")
    for candidate in _CARD_CANDIDATE.findall(plain):
        digits = re.sub(r"\D", "", candidate)
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found.append("payment_card")
            break
    id_number = any(p.search(plain) for p in (_AADHAAR, _PAN, _PASSPORT_IN, _SSN))
    id_word_with_number = _has_phrase(normalized, _ID_WORDS) and bool(_NUMBERISH.search(plain))
    if id_number or id_word_with_number:
        found.append("government_id")

    return [SensitiveMatch(category) for category in dict.fromkeys(found)]


def is_sensitive(text: str) -> bool:
    return bool(find_sensitive(text))


def drop_sensitive_sentences(text: str) -> str:
    """Remove every sentence that trips a check; used for summaries and transcripts.

    Dropping the whole sentence (rather than masking the number) also removes
    the context around a secret, such as "my bank PIN is", which is itself
    something nobody wants in a file.
    """
    sentences = _SENTENCE_END.split(text)
    return " ".join(s for s in sentences if s.strip() and not is_sensitive(s)).strip()
