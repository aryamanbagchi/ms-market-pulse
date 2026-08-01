"""Plain-text rendering of the same issue.

Kept deliberately simple and hard-wrapped at 78 columns so it reads correctly in a
terminal, a plain-text email part, or a diff.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from typing import Dict, List, Optional

import config
from core.models import Item
from render.html import CATEGORY_SOURCES, provenance_line

WIDTH = 78


def _wrap(text: str, indent: str = "") -> str:
    if not text:
        return ""
    return textwrap.fill(
        text, width=WIDTH, initial_indent=indent, subsequent_indent=indent
    )


def _url_line(url: str, indent: str = "   ") -> str:
    """Emit a URL on its own unwrapped line.

    Google News links are very long base64 redirects. Wrapping them to the column
    limit would split them across lines and break copy-paste, so line length is
    sacrificed to keep the link usable.
    """
    return "{0}{1}".format(indent, url) if url else ""


def _empty_note(category: str, screened_below: int) -> str:
    """Text counterpart of render.html._empty_category — same wording, same reasoning."""
    if screened_below:
        return (
            "{0} item{1} screened in this category, none clearing the impact threshold. "
            "Listed under Also tracked below."
        ).format(screened_below, "" if screened_below == 1 else "s")
    return CATEGORY_SOURCES.get(category, "Nothing qualifying in this window.")


def _also_tracked_tag(item: Item) -> str:
    bits = [item.source_label]
    if item.sponsor and item.sponsor != item.source_label:
        bits.append(item.sponsor)
    elif item.phase:
        bits.append(item.phase)
    return " | ".join(bits)


def _item_block(item: Item, index: int) -> List[str]:
    lines: List[str] = []
    lines.append("")
    lines.append(_wrap("{0}. {1}".format(index, item.title)))

    meta = [item.source_label, item.published_display]
    for extra in (item.phase, item.status, item.sponsor, item.nct_id):
        if extra:
            meta.append(extra)
    lines.append(_wrap("Impact {0}/5 | {1}".format(item.importance, " | ".join(meta)), "   "))

    lines.append("")
    lines.append(_wrap(item.summary, "   "))
    lines.append("")
    lines.append(_wrap("WHY IT MATTERS: {0}".format(item.why_it_matters), "   "))
    lines.append(_url_line(item.url))

    for also in item.also_reported_by:
        lines.append(_wrap("Also reported by {0}:".format(also.get("label")), "   "))
        lines.append(_url_line(also.get("url", "")))

    return lines


def render(
    featured: List[Item],
    also_tracked: List[Item],
    editors_take: str,
    issue_number: int,
    issue_date: datetime,
    window_start: datetime,
    generated_at: Optional[datetime] = None,
    ai_enabled: bool = True,
) -> str:
    generated_at = generated_at or datetime.utcnow()
    also_tracked = also_tracked or []
    screened = len(featured) + len(also_tracked)
    out: List[str] = []

    out.append("=" * WIDTH)
    out.append(config.NEWSLETTER_NAME.upper())
    out.append(config.NEWSLETTER_TAGLINE)
    out.append("=" * WIDTH)
    out.append(
        "Issue {0}  |  {1} - {2}".format(
            issue_number,
            window_start.strftime("%d %b"),
            issue_date.strftime("%d %b %Y"),
        )
    )
    out.append(
        "{0} item{1} screened  |  {2} featured".format(
            screened, "" if screened == 1 else "s", len(featured)
        )
    )

    if editors_take:
        out.append("")
        out.append("-" * WIDTH)
        out.append("EDITOR'S TAKE")
        out.append("-" * WIDTH)
        out.append(_wrap(editors_take))

    if not screened:
        out.append("")
        out.append("-" * WIDTH)
        out.append("A QUIET WEEK IN MS")
        out.append("-" * WIDTH)
        out.append(_wrap(
            "No qualifying activity cleared our relevance and recency filters in this "
            "reporting window. All sources were polled successfully - ClinicalTrials.gov, "
            "FDA (3 feeds) and curated news - and returned nothing new for Multiple "
            "Sclerosis. Quiet weeks are normal in a single indication."
        ))
    else:
        grouped: Dict[str, List[Item]] = {c: [] for c in config.CATEGORIES}
        for item in featured:
            grouped.setdefault(item.category, []).append(item)

        below: Dict[str, int] = {c: 0 for c in config.CATEGORIES}
        for item in also_tracked:
            below[item.category] = below.get(item.category, 0) + 1

        # Every category is printed, empty or not — see render/html.py for why.
        for category in config.CATEGORIES:
            bucket = grouped.get(category) or []
            bucket.sort(
                key=lambda i: (-i.importance, -(i.published.timestamp() if i.published else 0))
            )
            out.append("")
            out.append("-" * WIDTH)
            out.append("{0}  ({1})".format(
                category.upper(),
                "{0} item{1}".format(len(bucket), "" if len(bucket) == 1 else "s")
                if bucket else "nothing featured",
            ))
            out.append("-" * WIDTH)

            if not bucket:
                out.append(_wrap(_empty_note(category, below.get(category, 0))))
                continue

            for index, item in enumerate(bucket, start=1):
                out.extend(_item_block(item, index))

        if also_tracked:
            out.append("")
            out.append("-" * WIDTH)
            out.append("ALSO TRACKED THIS WEEK  ({0} item{1})".format(
                len(also_tracked), "" if len(also_tracked) == 1 else "s"
            ))
            out.append("-" * WIDTH)
            out.append(_wrap(
                "Screened and scored below the impact threshold. Listed for completeness."
            ))
            ordered = sorted(
                also_tracked,
                key=lambda i: (-i.importance, -(i.published.timestamp() if i.published else 0)),
            )
            for item in ordered:
                out.append("")
                out.append(_wrap("- {0}".format(item.title)))
                out.append(_wrap(_also_tracked_tag(item), "  "))
                out.append(_url_line(item.url, "  "))

    out.append("")
    out.append("=" * WIDTH)
    provenance = provenance_line(featured + also_tracked, ai_enabled)
    out.append(_wrap(
        "Generated automatically from ClinicalTrials.gov API v2, three U.S. FDA RSS "
        "feeds (press releases, drugs, MedWatch), and curated news. " + provenance
    ))
    out.append(_wrap("Compiled {0} UTC.".format(generated_at.strftime("%Y-%m-%d %H:%M"))))
    out.append(_wrap(
        "This briefing summarises public information for competitive intelligence "
        "purposes and is not medical, investment or regulatory advice."
    ))
    out.append("=" * WIDTH)

    return "\n".join(out) + "\n"
