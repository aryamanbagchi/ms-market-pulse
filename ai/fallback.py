"""Deterministic, offline enrichment.

Used in three situations:
  * `--dry-run` and `--no-ai`
  * no GEMINI_API_KEY is present
  * a Gemini call failed twice for a given item

It produces a usable summary, category, importance score and "why it matters" line with
no network access, so the newsletter is always complete rather than partially blank.
The output is intentionally plainer than the model's — it is a floor, not a substitute.
"""

from __future__ import annotations

import re
from typing import List, Tuple

import config
from core.models import Item

# ------------------------------------------------------------------------------------
# Category rules. First matching group wins; order matters.
# ------------------------------------------------------------------------------------

_REGULATORY_TERMS = [
    "fda", "ema", "approval", "approved", "approves", "authorisation", "authorization",
    "label", "labelling", "labeling", "submission", "nda", "bla", "sbla", "anda",
    "regulatory", "chmp", "pdufa", "breakthrough", "orphan drug", "fast track",
    "priority review", "recall", "safety communication", "warning letter", "medwatch",
    "committee", "advisory", "marketing authorisation", "pbs", "reimbursement",
    "nice", "health canada", "mhra",
]

_COMMERCIAL_TERMS = [
    "acquisition", "acquires", "merger", "deal", "licensing", "license agreement",
    "partnership", "collaboration", "revenue", "sales", "earnings", "quarter", "q1",
    "q2", "q3", "q4", "guidance", "forecast", "market share", "launch", "pricing",
    "price", "biosimilar", "generic", "patent", "litigation", "layoff", "restructuring",
    "investor", "stock", "shares", "funding", "financing", "ipo", "beats estimates",
    "delisted", "delisting", "cost", "payer", "formulary",
]

_CLINICAL_TERMS = [
    "phase", "trial", "study", "enrolment", "enrollment", "recruiting", "endpoint",
    "efficacy", "topline", "readout", "cohort", "randomized", "randomised",
    "placebo", "open-label", "double-blind", "interim", "biomarker", "remyelination",
]

# ------------------------------------------------------------------------------------
# Importance signals
# ------------------------------------------------------------------------------------

_HIGH_IMPACT = [
    "approval", "approved", "approves", "rejection", "rejected", "complete response",
    "phase 3", "phase iii", "pivotal", "topline", "met primary endpoint",
    "failed", "discontinued", "halted", "terminated", "recall", "safety communication",
    "acquisition", "acquires", "merger", "breakthrough",
]

_MEDIUM_IMPACT = [
    "phase 2", "phase ii", "submission", "filed", "launch", "partnership",
    "collaboration", "licensing", "earnings", "revenue", "guidance", "biosimilar",
    "pricing", "reimbursement", "interim",
]

_LOW_IMPACT = [
    "phase 1", "phase i", "observational", "registry", "natural history",
    "feasibility", "pilot", "survey", "questionnaire", "n/a",
]

_BIG_SPONSORS = [
    "roche", "genentech", "novartis", "biogen", "sanofi", "merck", "bristol-myers",
    "bristol myers", "johnson", "janssen", "pfizer", "astrazeneca", "glaxosmithkline",
    "gsk", "teva", "eli lilly", "amgen", "abbvie", "takeda", "ucb", "bayer",
    "immunic", "tg therapeutics", "alexion", "horizon",
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _contains(haystack: str, needles: List[str]) -> bool:
    return any(n in haystack for n in needles)


def classify(item: Item) -> str:
    """Assign a category from keyword evidence in title, body, and source."""
    blob = " ".join([item.title or "", item.raw_text or "", item.status or ""]).lower()

    # ClinicalTrials.gov records are clinical unless they are plainly regulatory news.
    if item.source == "clinicaltrials":
        return config.CATEGORY_REGULATORY if _contains(blob, [
            "expanded access", "fda", "regulatory", "post-marketing", "postmarketing",
        ]) else config.CATEGORY_CLINICAL

    if _contains(blob, _REGULATORY_TERMS):
        return config.CATEGORY_REGULATORY
    if _contains(blob, _COMMERCIAL_TERMS):
        return config.CATEGORY_COMMERCIAL
    if _contains(blob, _CLINICAL_TERMS):
        return config.CATEGORY_CLINICAL

    return config.CATEGORY_COMMERCIAL if item.source == "news" else config.CATEGORY_CLINICAL


def score(item: Item, category: str) -> int:
    """Heuristic 1-5 importance."""
    blob = " ".join([item.title or "", item.raw_text or "", item.phase or ""]).lower()
    value = config.DEFAULT_IMPORTANCE

    if _contains(blob, _HIGH_IMPACT):
        value += 1
    if _contains(blob, _MEDIUM_IMPACT):
        value += 0
    if _contains(blob, _LOW_IMPACT):
        value -= 1

    # Regulatory action is the highest-signal category for a CI newsletter.
    if category == config.CATEGORY_REGULATORY:
        value += 1

    # Movement by a major MS player matters more than the same news from a small centre.
    if item.sponsor and _contains(item.sponsor.lower(), _BIG_SPONSORS):
        value += 1
    if item.publisher and _contains(item.publisher.lower(), _BIG_SPONSORS):
        value += 1

    # An academic single-centre study is rarely market-moving.
    if item.sponsor and _contains(
        item.sponsor.lower(), ["university", "hospital", "institute", "college", "school"]
    ):
        value -= 1

    return max(config.MIN_IMPORTANCE, min(config.MAX_IMPORTANCE, value))


def summarize(item: Item, max_sentences: int = 2, max_chars: int = 320) -> str:
    """First couple of sentences of the source text, trimmed to a clean boundary."""
    text = (item.raw_text or "").strip()
    if not text or text == item.title:
        # News items carry no usable body, so describe the item instead of echoing it.
        who = item.sponsor or item.publisher
        if who:
            return "{0} — reported by {1}.".format(item.title.rstrip("."), who)
        return item.title.rstrip(".") + "."

    sentences = _SENTENCE_SPLIT.split(text)
    summary = " ".join(sentences[:max_sentences]).strip()

    if len(summary) > max_chars:
        cut = summary[:max_chars].rsplit(" ", 1)[0]
        summary = cut.rstrip(",;:") + "…"

    return summary


def why_it_matters(item: Item, category: str, importance: int) -> str:
    """One line of analyst framing, assembled from what the record actually states."""
    actor = item.sponsor or item.publisher

    if category == config.CATEGORY_REGULATORY:
        base = "Regulatory movement can reset the competitive order in MS"
        return "{0}{1}; worth tracking for label and access implications.".format(
            base, " for {0}".format(actor) if actor else ""
        )

    if category == config.CATEGORY_COMMERCIAL:
        base = "Commercial signal"
        return "{0}{1}; relevant to share shift, pricing pressure and portfolio strategy.".format(
            base, " involving {0}".format(actor) if actor else ""
        )

    phase = item.phase or "This study"
    status = (item.status or "").lower()
    stage = "{0} activity".format(phase) if item.phase else "Clinical activity"
    sponsor_clause = " from {0}".format(actor) if actor else ""
    status_clause = " (currently {0})".format(status) if status else ""
    return "{0}{1}{2} indicates where the MS pipeline is being directed.".format(
        stage, sponsor_clause, status_clause
    )


def enrich(item: Item) -> Item:
    """Populate every enrichment field without touching the network."""
    category = classify(item)
    importance = score(item, category)

    item.category = category
    item.importance = importance
    item.summary = summarize(item)
    item.why_it_matters = why_it_matters(item, category, importance)
    item.enriched_by = "fallback"

    return item.clamp()


def enrich_all(items: List[Item]) -> List[Item]:
    return [enrich(i) for i in items]


def editors_take(items: List[Item]) -> str:
    """Deterministic stand-in for the model-written Editor's Take."""
    if not items:
        return (
            "No qualifying activity was detected across ClinicalTrials.gov, the FDA "
            "feeds or curated news in this reporting window. Quiet weeks are normal in "
            "a single indication; the pipeline re-checks every source on the next run."
        )

    by_cat = {c: 0 for c in config.CATEGORIES}
    for i in items:
        by_cat[i.category] = by_cat.get(i.category, 0) + 1

    parts: List[str] = []
    parts.append(
        "This week's scan surfaced {0} item{1} across the MS landscape.".format(
            len(items), "" if len(items) == 1 else "s"
        )
    )

    breakdown = ", ".join(
        "{0} {1}".format(count, name.lower())
        for name, count in by_cat.items() if count
    )
    if breakdown:
        parts.append("The split was {0}.".format(breakdown))

    top = sorted(items, key=lambda i: -i.importance)[:2]
    if top:
        parts.append(
            "Highest-priority developments: {0}.".format(
                "; ".join(t.title.rstrip(".") for t in top)
            )
        )

    # Draw the named organisations from the highest-scoring items, not raw list order,
    # so the line highlights market movers rather than whichever record came back first.
    sponsors: List[str] = []
    for i in sorted(items, key=lambda x: -x.importance):
        name = i.sponsor or i.publisher
        if name and name not in sponsors:
            sponsors.append(name)
    if sponsors:
        parts.append(
            "Organisations behind the most significant items: {0}.".format(
                ", ".join(sponsors[:4])
            )
        )

    parts.append(
        "This edition was generated without model commentary; scores and categories "
        "come from deterministic rules."
    )

    return " ".join(parts)
