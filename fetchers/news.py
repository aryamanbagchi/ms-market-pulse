"""Google News RSS fetcher for company and commercial MS coverage.

The feed returns ~100 items spanning several months, so the lookback window does most
of the filtering here. Titles arrive with the publisher appended (" - WSJ"), which is
stripped into the `publisher` field so the newsletter can attribute cleanly.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

import feedparser

import config
from core.models import Item
from fetchers.base import (
    FetchContext,
    clean_text,
    http_get,
    is_ms_relevant,
    parse_rfc822,
)

log = logging.getLogger(__name__)

NAME = "news"

# Google News appends " - Publisher" to every headline.
_TRAILING_PUBLISHER = re.compile(r"\s+[-–—]\s+([^-–—]{2,60})$")


def fetch_raw(ctx: FetchContext) -> Dict[str, Any]:
    return {"xml": http_get(config.GOOGLE_NEWS_URL).text}


def parse(payload: Dict[str, Any], ctx: FetchContext) -> List[Item]:
    xml = (payload or {}).get("xml") or ""
    parsed = feedparser.parse(xml)

    items: List[Item] = []
    seen_urls = set()

    for entry in parsed.entries:
        try:
            item = _parse_entry(entry, ctx)
        except Exception as exc:  # noqa: BLE001 - skip one bad entry, keep the feed
            log.debug("skipping malformed news entry: %s", exc)
            continue

        if item is not None and item.url not in seen_urls:
            seen_urls.add(item.url)
            items.append(item)

    log.debug("source=news           scanned %d entries, kept %d", len(parsed.entries), len(items))
    return items


def _parse_entry(entry: Any, ctx: FetchContext):
    raw_title = clean_text(entry.get("title"))
    url = (entry.get("link") or "").strip()
    if not raw_title or not url:
        return None

    published = parse_rfc822(entry.get("published") or entry.get("updated"))
    if not ctx.in_window(published):
        return None

    title, publisher = _split_publisher(raw_title, entry)

    description = clean_text(entry.get("description") or entry.get("summary"))

    # The feed query is already MS-scoped, but it still returns loosely related items;
    # re-check so the newsletter stays on-topic.
    if not is_ms_relevant(title, description):
        return None

    return Item(
        source=NAME,
        title=title,
        url=url,
        published=published,
        # Google News descriptions are link-farm HTML with no real content; the headline
        # is the only reliable signal, so it doubles as the text handed to the model.
        raw_text=title,
        publisher=publisher,
    )


def _split_publisher(raw_title: str, entry: Any):
    """Prefer the feed's <source> element; fall back to the ' - Publisher' suffix."""
    source_el = entry.get("source")
    publisher = None

    if isinstance(source_el, dict):
        publisher = clean_text(source_el.get("title") or source_el.get("value"))
    elif isinstance(source_el, str):
        publisher = clean_text(source_el)

    match = _TRAILING_PUBLISHER.search(raw_title)
    if match:
        suffix = match.group(1).strip()
        if not publisher:
            publisher = suffix
        # Only strip the suffix when it really is the publisher, not part of the headline.
        if publisher and suffix.lower() == publisher.lower():
            raw_title = raw_title[: match.start()].strip()

    return raw_title, (publisher or None)
