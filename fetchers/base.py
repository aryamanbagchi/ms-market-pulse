"""Shared plumbing for every fetcher: HTTP with retries, MS relevance filtering,
date handling, and the graceful-degradation wrapper.

The contract each fetcher implements is deliberately narrow:

    fetch_raw(ctx) -> JSON-serialisable payload   (network; skipped on cache/sample hit)
    parse(payload, ctx) -> List[Item]             (pure; never touches the network)

Splitting them this way is what makes the cache and `--dry-run` work without any
special-casing inside the individual fetchers.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional

import requests

import config
from core import cache
from core.models import Item

log = logging.getLogger(__name__)


class FetchContext:
    """Everything a fetcher needs to know about the current run."""

    def __init__(self, run_date: datetime, use_cache: bool = True, dry_run: bool = False):
        self.run_date = run_date
        self.use_cache = use_cache
        self.dry_run = dry_run

    @property
    def run_date_str(self) -> str:
        return self.run_date.strftime("%Y-%m-%d")

    @property
    def window_start(self) -> datetime:
        return self.run_date - timedelta(days=config.LOOKBACK_DAYS)

    def in_window(self, when: Optional[datetime]) -> bool:
        """True if `when` falls inside the lookback window.

        Items with no parseable date are kept — better a slightly stale item in the
        newsletter than silently dropping a real signal because a feed omitted a date.
        """
        if when is None:
            return True
        return self.window_start <= when <= self.run_date + timedelta(days=1)


# ------------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------------

_session: Optional[requests.Session] = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": config.USER_AGENT})
    return _session


def http_get(url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    """GET with bounded retries and exponential backoff.

    Retries transient failures (connection errors, timeouts, 429, 5xx) and fails fast
    on 4xx, which will not fix itself on a retry.
    """
    last_exc = None
    for attempt in range(config.HTTP_RETRIES + 1):
        try:
            resp = session().get(url, params=params, timeout=config.HTTP_TIMEOUT)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(
                    "retryable status {0}".format(resp.status_code), response=resp
                )
            resp.raise_for_status()
            return resp
        except (requests.RequestException, requests.HTTPError) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                raise  # client error; retrying is pointless
            if attempt < config.HTTP_RETRIES:
                delay = config.HTTP_BACKOFF ** attempt
                log.debug("retry %s/%s in %.1fs: %s", attempt + 1, config.HTTP_RETRIES, delay, url)
                time.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ------------------------------------------------------------------------------------
# MS relevance
# ------------------------------------------------------------------------------------

# "MS" as a standalone uppercase token. Lowercase "ms" appears inside hundreds of
# unrelated words (forms, systems, programs, milliseconds), so it is not matched.
_MS_TOKEN = re.compile(r"(?<![A-Za-z])MS(?![A-Za-z])")


def is_ms_relevant(*texts: Optional[str]) -> bool:
    """True if any of the supplied text fragments mentions MS, an MS therapy, or a
    tracked pipeline asset."""
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return False
    lowered = blob.lower()
    for keyword in config.MS_KEYWORDS:
        if keyword in lowered:
            return True
    return bool(_MS_TOKEN.search(blob))


# ------------------------------------------------------------------------------------
# Dates
# ------------------------------------------------------------------------------------


def parse_rfc822(value: Optional[str]) -> Optional[datetime]:
    """Parse an RSS pubDate ('Wed, 29 Jul 2026 12:10:31 EDT') to a naive UTC datetime."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    """Parse a ClinicalTrials.gov date, which may be 'YYYY-MM-DD' or 'YYYY-MM'."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------------------------
# Text cleanup
# ------------------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# ClinicalTrials.gov returns markdown-escaped punctuation ("\-", "\*") in brief summaries.
_MD_ESCAPE = re.compile(r"\\([-*_#\[\]()>`~+.!])")


def clean_text(value: Optional[str]) -> str:
    """Strip HTML tags, unescape entities, drop markdown escapes, collapse whitespace.

    RSS descriptions routinely contain markup and ClinicalTrials.gov escapes punctuation;
    neither should reach the model or the renderer.
    """
    if not value:
        return ""
    import html as _html

    text = _TAG.sub(" ", value)
    text = _html.unescape(text)
    text = _MD_ESCAPE.sub(r"\1", text)
    return _WS.sub(" ", text).strip()


# ------------------------------------------------------------------------------------
# Graceful degradation
# ------------------------------------------------------------------------------------


def safe_fetch(
    name: str,
    fetch_raw: Callable[[FetchContext], Any],
    parse: Callable[[Any, FetchContext], List[Item]],
    ctx: FetchContext,
) -> List[Item]:
    """Run one source end to end, absorbing any failure.

    A source that raises contributes an empty list and logs a warning. The pipeline
    continues with whatever the other sources returned.
    """
    try:
        payload = _load_payload(name, fetch_raw, ctx)
        if payload is None:
            log.warning("source=%-15s no payload available", name)
            return []
        items = parse(payload, ctx)
        log.info("source=%-15s %d item(s)", name, len(items))
        return items
    except Exception as exc:  # noqa: BLE001 - one bad source must not end the run
        log.warning("source=%-15s FAILED: %s", name, exc)
        return []


def _load_payload(name: str, fetch_raw: Callable[[FetchContext], Any], ctx: FetchContext) -> Any:
    """Resolve a payload from fixtures, cache, or the network, in that order."""
    if ctx.dry_run:
        return cache.read_sample(name)

    if ctx.use_cache:
        cached = cache.read(name, ctx.run_date_str)
        if cached is not None:
            return cached

    payload = fetch_raw(ctx)
    cache.write(name, ctx.run_date_str, payload)
    return payload
