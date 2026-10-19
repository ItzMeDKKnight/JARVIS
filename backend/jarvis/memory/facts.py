"""Fact recall: pick the stored facts that are relevant to what the user just said.

Recall is a local word-overlap score, not an extra model call or an embedding
model: it adds no latency before the first spoken word, needs no download and
no network, and is easy to test. Two tricks make it good enough for one
person's few hundred facts:

* every fact carries keywords in English *and* Hindi (written by Claude when
  the fact is stored), so "मेरी बहन" can find "Tony's sister is called Meera";
* a word also matches a longer word it is the start of (if it has at least
  :data:`MIN_PREFIX` letters), so "sister" finds "sisters" and "football"
  finds "footballer".

What this misses, the model can still look up with the recall_memory tool.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from jarvis.memory.store import Fact
from jarvis.text import normalize_name

# Shorter shared prefixes ("car" / "cart") match too much to be useful.
MIN_PREFIX = 4

# Words that carry no topic. Small on purpose: anything missing only adds noise
# to the score, while a wrongly listed word would make some facts unfindable.
_STOPWORD_TEXT = """
    a an the and or but if of to in on at for from by with about as is are was were be been
    am do does did have has had i me my mine you your yours he she it its we our they them
    their this that these those what which who whom whose when where why how can could would
    should will shall may might must not no yes please tell know remember say said just also
    hey hi hello ok okay so very really thing things something anything
    मैं मेरा मेरी मेरे मुझे मुझको तुम आप आपका आपकी हम है हैं था थी थे हो को का की के में से पर
    और या भी तो ही यह वह ये वो क्या कौन कब कहाँ कैसे क्यों कि ना नहीं हाँ जी बताओ बताइए याद
    kya hai hain mera meri mere mujhe tum aap ka ki ke ko se mein aur bhi toh batao yaad
"""
# Normalised the same way as the text they are compared with.
STOPWORDS = frozenset(normalize_name(_STOPWORD_TEXT).split())


def content_words(text: str) -> set[str]:
    """Normalised words of ``text`` that can carry a topic."""
    return {w for w in normalize_name(text).split() if w not in STOPWORDS and len(w) > 1}


def _words_match(a: str, b: str) -> bool:
    shorter, longer = sorted((a, b), key=len)
    return shorter == longer or (len(shorter) >= MIN_PREFIX and longer.startswith(shorter))


def score(fact: Fact, query_words: set[str]) -> float:
    """How many distinct query words this fact covers (text and keywords together)."""
    fact_words = content_words(fact.text)
    for keyword in fact.keywords:
        fact_words |= content_words(keyword)
    return float(sum(1 for q in query_words if any(_words_match(q, f) for f in fact_words)))


@dataclass(frozen=True)
class ScoredFact:
    fact: Fact
    score: float


def rank(
    facts: Iterable[Fact],
    query: str,
    *,
    min_score: float,
    limit: int,
    ignore: Iterable[str] = (),
) -> list[Fact]:
    """Facts scoring at least ``min_score`` for ``query``, best first (newest wins ties).

    ``ignore`` holds extra words to skip in the query, such as the user's and the
    assistant's names, which appear in almost every fact and request.
    """
    ignored = {w for name in ignore for w in content_words(name)}
    query_words = content_words(query) - ignored
    if not query_words:
        return []
    # facts() returns newest first, and sorted() is stable, so ties keep that order.
    scored = [ScoredFact(f, score(f, query_words)) for f in facts]
    relevant = [s for s in scored if s.score >= min_score]
    relevant.sort(key=lambda s: s.score, reverse=True)
    return [s.fact for s in relevant[:limit]]
