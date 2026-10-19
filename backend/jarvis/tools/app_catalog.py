"""Resolves spoken app names (English, Hindi, Hinglish) to allowlisted apps."""

from __future__ import annotations

from collections.abc import Iterable

from jarvis.config import AppEntry
from jarvis.text import normalize_name

# Words people wrap around an app name ("open the calculator app", "calculator kholo").
# Stripped only as a second attempt, so an alias that genuinely contains one still matches first.
# Normalised like the input, so nukta spellings (ज़रा / जरा) both count.
_FILLER_WORDS = frozenset(
    normalize_name(word)
    for word in {
        "open",
        "launch",
        "start",
        "run",
        "the",
        "app",
        "application",
        "program",
        "please",
        "jarvis",
        "kholo",
        "khol",
        "kholiye",
        "chalao",
        "chalu",
        "do",
        "de",
        "dijiye",
        "karo",
        "kar",
        "zara",
        "mera",
        "खोलो",
        "खोल",
        "दो",
        "करो",
        "खोलिए",
        "चलाओ",
        "चालू",
        "दीजिए",
        "ज़रा",
        "मेरा",
        "ऐप",
        "एप",
        "एप्लिकेशन",
        "जार्विस",
    }
)


class DuplicateAliasError(ValueError):
    """Two apps in apps.yaml claim the same alias, which would make resolution ambiguous."""


class AppCatalog:
    """Lookup table from normalised alias to app entry."""

    def __init__(self, apps: Iterable[AppEntry]) -> None:
        self._by_alias: dict[str, AppEntry] = {}
        for app in apps:
            for alias in {app.id, app.display_name, *app.aliases}:
                self._add(normalize_name(alias), app)

    def _add(self, key: str, app: AppEntry) -> None:
        existing = self._by_alias.get(key)
        if existing is not None and existing.id != app.id:
            raise DuplicateAliasError(f"alias '{key}' is used by both {existing.id} and {app.id}")
        self._by_alias[key] = app

    def resolve(self, spoken_name: str) -> AppEntry | None:
        """Return the allowlisted app for ``spoken_name``, or None if there is no exact alias."""
        key = normalize_name(spoken_name)
        if key in self._by_alias:
            return self._by_alias[key]
        stripped = " ".join(word for word in key.split() if word not in _FILLER_WORDS)
        return self._by_alias.get(stripped)
