"""Resolves spoken website names (English, Hindi, Hinglish) to allowlisted addresses.

"Open GitHub" and "जीमेल खोलो" should work without saying an address. The names
and addresses live in the ``websites:`` section of config/apps.yaml, so what
Jarvis can open by name is something you can read and edit, not a guess.
"""

from __future__ import annotations

from collections.abc import Iterable

from jarvis.config import WebsiteEntry
from jarvis.text import normalize_name

# Words people wrap around a site name ("open the GitHub website", "youtube kholo").
_FILLER_WORDS = frozenset(
    normalize_name(word)
    for word in {
        "open",
        "go",
        "to",
        "the",
        "website",
        "site",
        "web",
        "page",
        "homepage",
        "home",
        "please",
        "jarvis",
        "kholo",
        "khol",
        "do",
        "de",
        "karo",
        "zara",
        "खोलो",
        "खोल",
        "दो",
        "ज़रा",
        "वेबसाइट",
        "साइट",
        "पेज",
        "जार्विस",
    }
)


class DuplicateSiteAliasError(ValueError):
    """Two websites in apps.yaml claim the same alias."""


class SiteCatalog:
    """Lookup table from normalised alias to website entry."""

    def __init__(self, websites: Iterable[WebsiteEntry]) -> None:
        self._by_alias: dict[str, WebsiteEntry] = {}
        for site in websites:
            for alias in {site.id, site.display_name, *site.aliases}:
                key = normalize_name(alias)
                existing = self._by_alias.get(key)
                if existing is not None and existing.id != site.id:
                    raise DuplicateSiteAliasError(
                        f"alias '{key}' is used by both {existing.id} and {site.id}"
                    )
                self._by_alias[key] = site

    def resolve(self, spoken_name: str) -> WebsiteEntry | None:
        """Return the allowlisted site for ``spoken_name``, or None if no alias matches."""
        key = normalize_name(spoken_name)
        if key in self._by_alias:
            return self._by_alias[key]
        stripped = " ".join(word for word in key.split() if word not in _FILLER_WORDS)
        return self._by_alias.get(stripped)
