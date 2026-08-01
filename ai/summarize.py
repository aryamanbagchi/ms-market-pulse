"""Per-item enrichment: analyst summary, category, importance score, why-it-matters.

One call per item, executed through a small thread pool so a full week's items finish
in seconds rather than minutes. Any item whose call fails falls back to deterministic
enrichment, so a partial API outage degrades the copy rather than breaking the issue.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List

import config
from ai import fallback, gemini
from core.models import Item

log = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = (
    "You are a senior competitive-intelligence analyst covering the Multiple Sclerosis "
    "therapeutic market for a pharmaceutical strategy consultancy. Your readers are "
    "brand, pipeline and market-access leads who already know the disease area, the "
    "major players (Roche/Genentech, Novartis, Biogen, Sanofi, Merck KGaA, Bristol "
    "Myers Squibb, TG Therapeutics) and the marketed products. "
    "Write densely and factually. Never pad, never hedge, never restate the headline. "
    "Only state facts present in the supplied source text; if something is not stated, "
    "leave it out rather than inferring it."
)

# Constrains the model's output shape at the API level rather than by prompt alone.
ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "2-3 sentences in analyst tone describing what happened.",
        },
        "category": {
            "type": "string",
            "enum": config.CATEGORIES,
        },
        "importance": {
            "type": "integer",
            "description": "Market impact from 1 (routine) to 5 (market-moving).",
        },
        "why_it_matters": {
            "type": "string",
            "description": "One sentence on the competitive or commercial implication.",
        },
    },
    "required": ["summary", "category", "importance", "why_it_matters"],
}

_PROMPT = """Analyse this Multiple Sclerosis market development.

SOURCE: {source}
HEADLINE: {title}
DATE: {date}{extras}

SOURCE TEXT:
{body}

Produce:
1. summary — 2-3 sentences, analyst tone, stating what actually happened and the
   specifics that matter (asset, sponsor, phase, population, endpoint, decision).
   Do not repeat the headline verbatim. Do not editorialise.
2. category — exactly one of: {categories}
3. importance — integer 1-5 for impact on the MS competitive landscape:
   5 = market-moving (approval or rejection, pivotal readout, major M&A)
   4 = significant (phase 3 progress, launch, pricing or access decision, safety signal)
   3 = notable (phase 2 activity, meaningful partnership, earnings detail)
   2 = minor (early-phase or single-centre studies, incremental updates)
   1 = routine (administrative record changes, tangential coverage)
4. why_it_matters — one sentence naming the concrete commercial or competitive
   implication for companies operating in MS. Be specific about who is affected and how."""


def _prompt_for(item: Item) -> str:
    extras: List[str] = []
    if item.sponsor:
        extras.append("SPONSOR: {0}".format(item.sponsor))
    if item.phase:
        extras.append("PHASE/TYPE: {0}".format(item.phase))
    if item.status:
        extras.append("STATUS: {0}".format(item.status))
    if item.nct_id:
        extras.append("TRIAL ID: {0}".format(item.nct_id))

    body = (item.raw_text or item.title)[: config.MAX_SOURCE_CHARS]

    return _PROMPT.format(
        source=item.source_label,
        title=item.title,
        date=item.published_display,
        extras=("\n" + "\n".join(extras)) if extras else "",
        body=body,
        categories=", ".join(config.CATEGORIES),
    )


def enrich_one(item: Item) -> Item:
    """Enrich a single item, falling back to deterministic rules on any failure."""
    try:
        data = gemini.generate_json(
            prompt=_prompt_for(item),
            schema=ITEM_SCHEMA,
            system_instruction=SYSTEM_INSTRUCTION,
        )
    except gemini.GeminiError as exc:
        log.warning("gemini failed for %r (%s) — using fallback", item.title[:52], exc)
        return fallback.enrich(item)

    summary = (data.get("summary") or "").strip()
    why = (data.get("why_it_matters") or "").strip()

    # A schema-valid but empty response is still unusable; treat it as a failure.
    if not summary or not why:
        log.warning("gemini returned empty fields for %r — using fallback", item.title[:52])
        return fallback.enrich(item)

    item.summary = summary
    item.why_it_matters = why
    item.category = (data.get("category") or "").strip()
    item.importance = data.get("importance", config.DEFAULT_IMPORTANCE)
    item.enriched_by = "gemini"

    return item.clamp()


def enrich_all(items: List[Item]) -> List[Item]:
    """Enrich every item concurrently, preserving input order."""
    if not items:
        return []

    if not gemini.is_available():
        log.warning(
            "%s not set — enriching %d item(s) with deterministic rules",
            config.GEMINI_API_KEY_ENV, len(items),
        )
        return fallback.enrich_all(items)

    workers = min(config.GEMINI_MAX_WORKERS, len(items))
    log.info("enrich        %d item(s) via %s (%d workers)",
             len(items), config.GEMINI_MODEL, workers)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        enriched = list(pool.map(enrich_one, items))

    by_model = sum(1 for i in enriched if i.enriched_by == "gemini")
    if by_model < len(enriched):
        log.warning("enrich        %d/%d item(s) fell back to deterministic rules",
                    len(enriched) - by_model, len(enriched))

    return enriched
