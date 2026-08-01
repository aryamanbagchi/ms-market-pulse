"""The Editor's Take: one executive overview of the week, written from the enriched set.

A single second call, made after per-item enrichment so the model reasons over the
scored and categorised items rather than raw source text.
"""

from __future__ import annotations

import logging
from typing import List

import config
from ai import fallback, gemini
from core.models import Item

log = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = (
    "You are the editor of a weekly competitive-intelligence briefing on the Multiple "
    "Sclerosis market, read by pharmaceutical strategy consultants and their clients. "
    "You write the opening executive overview. Your job is synthesis, not enumeration: "
    "identify the through-line of the week, name the companies and assets that moved, "
    "and say what it signals for competitive positioning. "
    "Never list every item. Never use filler openers such as 'This week in MS'. "
    "Only reference developments present in the supplied digest."
)

TAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "editors_take": {
            "type": "string",
            "description": "4-6 sentence executive overview of the week in the MS market.",
        }
    },
    "required": ["editors_take"],
}

_PROMPT = """Here is this week's digest of Multiple Sclerosis market developments,
already summarised, categorised and scored for impact (5 = market-moving).

{digest}

Write the Editor's Take: 4-6 sentences giving an executive overview of the week in the
MS market.

Requirements:
- Open with the single most consequential development and why it matters.
- Draw out the pattern connecting items where one genuinely exists (a shared mechanism,
  a competitive response, a pricing or access theme). If no pattern exists, say the week
  was diffuse rather than inventing a narrative.
- Name specific companies and assets.
- Close with what to watch next, grounded in what the digest actually shows.
- Analyst register: direct, concrete, no hedging, no bullet points, no headings."""


def _digest(items: List[Item], limit: int = 25) -> str:
    ordered = sorted(items, key=lambda i: -i.importance)[:limit]
    lines: List[str] = []

    for item in ordered:
        meta = [item.category, "impact {0}/5".format(item.importance)]
        if item.sponsor:
            meta.append(item.sponsor)
        if item.phase:
            meta.append(item.phase)
        lines.append(
            "- [{meta}] {title}\n  {summary}".format(
                meta=" | ".join(meta), title=item.title, summary=item.summary
            )
        )

    return "\n".join(lines)


def write(items: List[Item]) -> str:
    """Return the Editor's Take, falling back to a deterministic version on failure."""
    if not items:
        return fallback.editors_take(items)

    if not gemini.is_available():
        return fallback.editors_take(items)

    try:
        data = gemini.generate_json(
            prompt=_PROMPT.format(digest=_digest(items)),
            schema=TAKE_SCHEMA,
            system_instruction=SYSTEM_INSTRUCTION,
            max_output_tokens=config.GEMINI_EDITOR_MAX_OUTPUT_TOKENS,
        )
    except gemini.GeminiError as exc:
        log.warning("editor's take failed (%s) — using deterministic version", exc)
        return fallback.editors_take(items)

    take = (data.get("editors_take") or "").strip()
    if not take:
        log.warning("editor's take came back empty — using deterministic version")
        return fallback.editors_take(items)

    log.info("editor        wrote Editor's Take (%d chars)", len(take))
    return take
