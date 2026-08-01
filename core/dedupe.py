"""Cross-source deduplication by fuzzy title match.

The same story frequently surfaces in more than one source — an FDA approval gets a
press release and a dozen news write-ups. Rather than add a fuzzy-matching dependency,
this uses stdlib difflib over aggressively normalised titles, which is more than
adequate at this volume (tens of items per run).

When two items match, the higher-authority source wins and the loser is recorded in
`also_reported_by` so the newsletter can still show corroboration.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import List

import config
from core.models import Item

log = logging.getLogger(__name__)

_PUNCT = re.compile(r"[^a-z0-9\s]")
_WS = re.compile(r"\s+")

# Dropped before comparison so that phrasing differences between a press release and a
# news headline about the same event do not defeat the match.
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "with", "at", "by",
    "from", "as", "is", "are", "was", "were", "be", "been", "its", "it", "that", "this",
    "new", "study", "trial", "patients", "patient", "treatment", "therapy", "drug",
})


def normalize(title: str) -> str:
    """Case-fold, strip punctuation, drop stopwords, sort remaining tokens.

    Token sorting makes the comparison word-order independent, so "Biogen beats
    estimates on MS" and "MS estimates beaten by Biogen" still match.
    """
    lowered = _PUNCT.sub(" ", (title or "").lower())
    tokens = [t for t in _WS.sub(" ", lowered).split() if t and t not in _STOPWORDS]
    return " ".join(sorted(tokens))


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def deduplicate(items: List[Item], threshold: float = None) -> List[Item]:
    """Collapse near-duplicate items across sources.

    Items are processed highest-authority first so the surviving record is always the
    most authoritative one available.
    """
    if threshold is None:
        threshold = config.DEDUPE_THRESHOLD
    if not items:
        return []

    ordered = sorted(
        items,
        key=lambda i: (
            -config.SOURCE_AUTHORITY.get(i.source, 0),
            -(i.published.timestamp() if i.published else 0),
        ),
    )

    kept: List[Item] = []
    kept_keys: List[str] = []
    merged = 0

    for item in ordered:
        key = normalize(item.title)
        match_index = -1

        for index, existing_key in enumerate(kept_keys):
            # An identical URL is a duplicate regardless of how the titles read.
            if item.url and item.url == kept[index].url:
                match_index = index
                break
            if similarity(key, existing_key) >= threshold:
                match_index = index
                break

        if match_index >= 0:
            winner = kept[match_index]
            winner.also_reported_by.append(
                {"label": item.source_label, "url": item.url}
            )
            merged += 1
            log.debug("merged duplicate: %r -> %r", item.title[:60], winner.title[:60])
            continue

        kept.append(item)
        kept_keys.append(key)

    if merged:
        log.info("dedupe        merged %d duplicate(s), %d remain", merged, len(kept))
    else:
        log.info("dedupe        no duplicates found, %d item(s)", len(kept))

    return kept
