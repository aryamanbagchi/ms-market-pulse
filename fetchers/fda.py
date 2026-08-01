"""FDA RSS fetcher.

Polls three FDA feeds and keeps only MS-relevant items from the lookback window.

Why three feeds: the FDA "press releases" feed carries roughly 20 items covering the
whole agency, so MS-specific announcements appear in it only a few times a year. The
"drugs" and "medwatch" feeds are where CDER approvals, label changes, and safety
communications actually surface. Polling all three is what makes this source useful
rather than almost always empty.
"""

from __future__ import annotations

import logging
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

NAME = "fda"


def fetch_raw(ctx: FetchContext) -> Dict[str, Any]:
    """Fetch every configured feed. A single dead feed does not fail the source."""
    feeds: Dict[str, str] = {}

    for feed_name, url in config.FDA_FEEDS.items():
        try:
            feeds[feed_name] = http_get(url).text
        except Exception as exc:  # noqa: BLE001 - degrade to the feeds that do work
            log.warning("source=fda            feed '%s' unavailable: %s", feed_name, exc)

    if not feeds:
        raise RuntimeError("all FDA feeds unavailable")

    return {"feeds": feeds}


def parse(payload: Dict[str, Any], ctx: FetchContext) -> List[Item]:
    items: List[Item] = []
    seen_urls = set()
    scanned = 0

    for feed_name, xml in ((payload or {}).get("feeds") or {}).items():
        parsed = feedparser.parse(xml)

        for entry in parsed.entries:
            scanned += 1
            try:
                item = _parse_entry(entry, feed_name, ctx)
            except Exception as exc:  # noqa: BLE001 - skip one bad entry, keep the feed
                log.debug("skipping malformed FDA entry: %s", exc)
                continue

            # The same announcement is often syndicated to several FDA feeds.
            if item is not None and item.url not in seen_urls:
                seen_urls.add(item.url)
                items.append(item)

    log.debug("source=fda            scanned %d entries, kept %d", scanned, len(items))
    return items


def _parse_entry(entry: Any, feed_name: str, ctx: FetchContext):
    title = clean_text(entry.get("title"))
    url = (entry.get("link") or "").strip()
    if not title or not url:
        return None

    description = clean_text(entry.get("description") or entry.get("summary"))

    # This is the filter that matters: FDA feeds are agency-wide, not MS-specific.
    if not is_ms_relevant(title, description):
        return None

    published = parse_rfc822(entry.get("published") or entry.get("updated"))
    if not ctx.in_window(published):
        return None

    return Item(
        source=NAME,
        title=title,
        url=url,
        published=published,
        raw_text=description,
        publisher="U.S. Food & Drug Administration",
        feed=feed_name,
    )
