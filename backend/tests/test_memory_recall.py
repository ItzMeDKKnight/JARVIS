"""Local fact recall: word overlap, bilingual keywords, prefix matching."""

from __future__ import annotations

from jarvis.memory.facts import content_words, rank
from jarvis.memory.store import Fact


def _fact(fact_id: int, text: str, *keywords: str) -> Fact:
    return Fact(fact_id, text, "other", tuple(keywords), "extracted", "t", "t")


FACTS = [
    _fact(3, "Tony's sister is called Meera.", "sister", "family", "बहन", "मीरा"),
    _fact(2, "Tony plays football on Sundays.", "football", "sport", "फ़ुटबॉल"),
    _fact(1, "Tony's favourite language is Rust.", "programming", "rust"),
]


def _texts(query: str, limit: int = 5) -> list[str]:
    return [f.text for f in rank(FACTS, query, min_score=1, limit=limit, ignore=("Tony",))]


def test_english_query_matches_fact_text() -> None:
    assert _texts("what is my sister's name?") == ["Tony's sister is called Meera."]


def test_hindi_query_matches_hindi_keywords() -> None:
    assert _texts("मेरी बहन का नाम क्या है?") == ["Tony's sister is called Meera."]


def test_nukta_spelling_does_not_matter() -> None:
    assert _texts("फुटबॉल कब खेलता हूँ") == ["Tony plays football on Sundays."]


def test_prefix_match_finds_longer_word() -> None:
    assert _texts("any sports plans?") == ["Tony plays football on Sundays."]


def test_stopwords_and_names_alone_match_nothing() -> None:
    assert _texts("hey Tony, what do you know?") == []
    assert content_words("what is the") == set()


def test_better_match_ranks_first_and_limit_applies() -> None:
    facts = [*FACTS, _fact(4, "Meera, Tony's sister, studies medicine.", "sister", "meera")]
    assert [f.id for f in rank(facts, "meera medicine", min_score=1, limit=5)] == [4, 3]
    assert [f.id for f in rank(facts, "meera medicine", min_score=1, limit=1)] == [4]
    assert [f.id for f in rank(facts, "meera medicine", min_score=2, limit=5)] == [4]


def test_short_words_do_not_prefix_match() -> None:
    # "car" must not match "cart"-style words: only words of 4+ letters prefix-match.
    facts = [_fact(1, "Tony's cartoon collection is huge.")]
    assert rank(facts, "my car", min_score=1, limit=5) == []
