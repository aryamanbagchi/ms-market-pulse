"""Resolve Google News redirect links to the publisher's real article URL.

Why this needs to exist at all: a Google News RSS <link> is a 200-600 character opaque
redirect (`news.google.com/rss/articles/CBMi...`). It works, but in a briefing it reads
as tracking spam and tells the reader nothing about who published the story.

Why it is not simply a redirect follow: `news.google.com` does not answer these with a
3xx. It returns a ~600 KB JavaScript page that resolves the destination client-side, so
`allow_redirects=True` lands back on news.google.com. The destination is only obtainable
from Google's own `batchexecute` RPC, which requires a per-article signature (`sg`) and
timestamp (`ts`) that appear as attributes on the article page. Hence two requests per
link: fetch the page for the signature, then call the RPC.

Everything here is best-effort by design. This is undocumented plumbing that Google can
change without notice, so every failure path returns the original redirect URL — which
still works, and still reaches the article. A resolution outage degrades the newsletter's
polish, never its correctness.

Verified against live responses on 2026-08-01.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import requests

import config

log = logging.getLogger(__name__)

# Attributes carried by the <c-wiz> element on an article page.
_SIGNATURE = re.compile(r'data-n-a-sg="([^"]+)"')
_TIMESTAMP = re.compile(r'data-n-a-ts="(\d+)"')

# The RPC reply is a JSON-in-JSON envelope; the payload arrives escaped.
_RESOLVED = re.compile(r'garturlres\\",\\"(https?://[^\\"]+)')

# Opaque request scaffolding the RPC expects alongside the article id. The literal "X"
# placeholders are what Google's own client sends for fields the call does not use.
_RPC_ID = "Fbv4je"
_RPC_CONFIG = [
    ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
     None, None, None, None, None, 0, 1],
    "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
]


def is_redirect(url: str) -> bool:
    return "news.google.com/rss/articles/" in (url or "")


def _article_id(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?")[0]


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": config.GNEWS_USER_AGENT})
    return session


def _resolve_one(session: requests.Session, url: str) -> Optional[str]:
    """Return the publisher URL for one redirect, or None if it cannot be obtained."""
    timeout = config.GNEWS_RESOLVE_TIMEOUT

    page = session.get(url, timeout=timeout)
    page.raise_for_status()

    signature = _SIGNATURE.search(page.text)
    timestamp = _TIMESTAMP.search(page.text)
    if not signature or not timestamp:
        raise ValueError("no signature on article page (page format changed?)")

    payload = json.dumps(
        ["garturlreq", _RPC_CONFIG, _article_id(url),
         int(timestamp.group(1)), signature.group(1)]
    )
    reply = session.post(
        config.GNEWS_BATCH_URL,
        data={"f.req": json.dumps([[[_RPC_ID, payload, None, "generic"]]])},
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=timeout,
    )
    reply.raise_for_status()

    match = _RESOLVED.search(reply.text)
    if not match:
        raise ValueError("RPC returned no destination URL")

    resolved = match.group(1)
    if is_redirect(resolved):
        raise ValueError("RPC returned another redirect")
    return resolved


# ------------------------------------------------------------------------------------
# Persistent resolution cache
# ------------------------------------------------------------------------------------


def _load_cache() -> Dict[str, str]:
    path = config.GNEWS_URL_CACHE
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        log.warning("gnews url cache unreadable, ignoring it (%s)", exc)
        return {}


def _save_cache(mapping: Dict[str, str]) -> None:
    try:
        config.GNEWS_URL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with config.GNEWS_URL_CACHE.open("w", encoding="utf-8") as fh:
            json.dump(mapping, fh, ensure_ascii=False, indent=2, sort_keys=True)
    except (OSError, TypeError) as exc:
        log.warning("could not persist gnews url cache (%s)", exc)


# ------------------------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------------------------


def resolve_all(urls: List[str]) -> Dict[str, str]:
    """Map each Google News redirect to a publisher URL.

    Only entries that actually resolved appear in the returned mapping, so callers can
    use `mapping.get(url, url)` and get the working redirect back for anything that
    failed. Previously resolved URLs are read from disk and never re-fetched.
    """
    targets = [u for u in dict.fromkeys(urls) if is_redirect(u)]
    if not targets or not config.GNEWS_RESOLVE:
        return {}

    cached = _load_cache()
    resolved = {u: cached[u] for u in targets if u in cached}
    pending = [u for u in targets if u not in resolved]

    if not pending:
        log.info("news links    %d/%d resolved from cache", len(resolved), len(targets))
        return resolved

    session = _session()

    def attempt(url):
        try:
            return url, _resolve_one(session, url)
        except Exception as exc:  # noqa: BLE001 - best-effort; keep the original URL
            log.debug("could not resolve %s: %s", url[:60], exc)
            return url, None

    workers = min(config.GNEWS_RESOLVE_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for url, destination in pool.map(attempt, pending):
            if destination:
                resolved[url] = destination

    failed = len(targets) - len(resolved)
    log.info(
        "news links    resolved %d/%d redirect(s)%s",
        len(resolved), len(targets),
        " ({0} kept as redirects)".format(failed) if failed else "",
    )

    cached.update(resolved)
    _save_cache(cached)
    return resolved
