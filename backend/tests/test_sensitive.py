"""The sensitive-data guard: what must never reach the memory database."""

from __future__ import annotations

import pytest

from jarvis.memory.sensitive import drop_sensitive_sentences, find_sensitive, is_sensitive


def _categories(text: str) -> set[str]:
    return {m.category for m in find_sensitive(text)}


@pytest.mark.parametrize(
    "text",
    [
        "my wifi password is tiger123",
        "My Gmail passcode is hunter2",
        "the ATM PIN is 4821",
        "OTP 553201",
        "मेरा पासवर्ड abc123 है",
        "मेरा पिन 1234 है",
    ],
)
def test_passwords_and_pins_are_blocked(text: str) -> None:
    assert "password" in _categories(text)


@pytest.mark.parametrize(
    "text",
    [
        "my card number is 4111 1111 1111 1111",
        "card 4111-1111-1111-1111 expires soon",
        "5500005555555559",
    ],
)
def test_luhn_valid_card_numbers_are_blocked(text: str) -> None:
    assert "payment_card" in _categories(text)


def test_random_long_number_that_fails_luhn_is_not_a_card() -> None:
    assert "payment_card" not in _categories("order 4111 1111 1111 1112 shipped")


@pytest.mark.parametrize(
    "text",
    [
        "my aadhaar is 2345 6789 0123",
        "आधार नंबर २३४५ ६७८९ ०१२३ है",  # Devanagari digits
        "PAN ABCDE1234F",
        "pan abcde1234f",
        "passport number K1234567",
        "my SSN is 123-45-6789",
        "my voter id number is 88213",
    ],
)
def test_government_ids_are_blocked(text: str) -> None:
    assert "government_id" in _categories(text)


def test_api_keys_are_blocked() -> None:
    assert "api_key" in _categories("my key is sk-ant-abcdef123456")


@pytest.mark.parametrize(
    "text",
    [
        "Tony's sister is called Meera.",
        "Tony works as an architect.",
        "Tony's phone number ends with 42.",
        "Tony's birthday is on 2 June 1999.",
        "Tony's passport expires next year.",  # an ID word without a number is fine
        "Tony prefers dark mode in VS Code.",
        "टोनी को क्रिकेट पसंद है।",
    ],
)
def test_ordinary_facts_pass(text: str) -> None:
    assert not is_sensitive(text)


def test_drop_sensitive_sentences_keeps_the_rest() -> None:
    text = "Tony is building Jarvis. He said his PIN is 4821. He likes cricket."
    assert drop_sensitive_sentences(text) == "Tony is building Jarvis. He likes cricket."


def test_drop_sensitive_sentences_handles_hindi_full_stop() -> None:
    text = "टोनी को क्रिकेट पसंद है। मेरा पासवर्ड abc है।"
    assert drop_sensitive_sentences(text) == "टोनी को क्रिकेट पसंद है।"
