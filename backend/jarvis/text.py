"""Text normalisation shared by alias matching and shortcut search.

Speech-to-text and the language model both produce small spelling variations
("VS-Code", "vs code", "फ़ाइल" vs "फाइल"). Normalising both sides the same way
lets one alias in apps.yaml cover all of them.
"""

from __future__ import annotations

import re
import unicodedata

_NUKTA = "़"
# Keep letters, digits, Devanagari combining marks, and '+' / '#' (for "c++", "c#").
_SEPARATORS = re.compile(r"[^\wऀ-ॿ+#]+")


def normalize_name(text: str) -> str:
    """Casefold, unify Unicode forms, drop Hindi nukta, and collapse punctuation to spaces.

    Hindi letters with a nukta (फ़, ज़) exist both precomposed and as letter +
    nukta, and people often omit the nukta entirely. NFD splits the
    precomposed forms, so removing U+093C afterwards treats all three spellings
    as equal. Underscores count as separators so "vs_code" matches "vs code".
    """
    decomposed = unicodedata.normalize("NFD", text).replace(_NUKTA, "")
    cleaned = _SEPARATORS.sub(" ", unicodedata.normalize("NFC", decomposed).casefold())
    return " ".join(cleaned.replace("_", " ").split())
