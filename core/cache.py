"""Day-scoped cache for raw source payloads.

Re-running the pipeline on the same day reads from disk and makes zero network calls,
so iterating on the template or the AI prompts costs nothing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

import config

log = logging.getLogger(__name__)


def _path(source: str, run_date: str, directory: Optional[Path] = None) -> Path:
    base = directory if directory is not None else config.CACHE_DIR
    return base / "{0}-{1}.json".format(source, run_date)


def read(source: str, run_date: str, directory: Optional[Path] = None) -> Optional[Any]:
    """Return the cached payload, or None on any miss or corruption."""
    path = _path(source, run_date, directory)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        log.info("cache hit    source=%s file=%s", source, path.name)
        return payload
    except (OSError, ValueError) as exc:
        # A corrupt cache file must never break a run; treat it as a miss.
        log.warning("cache unreadable source=%s file=%s (%s)", source, path.name, exc)
        return None


def write(source: str, run_date: str, payload: Any, directory: Optional[Path] = None) -> None:
    """Persist a payload. Failure to cache is logged but never fatal."""
    path = _path(source, run_date, directory)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        log.debug("cache write  source=%s file=%s", source, path.name)
    except (OSError, TypeError) as exc:
        log.warning("cache write failed source=%s (%s)", source, exc)


def read_sample(source: str) -> Optional[Any]:
    """Read a committed fixture from data/sample/, used by --dry-run."""
    path = config.SAMPLE_DIR / "{0}.json".format(source)
    if not path.exists():
        log.warning("no sample fixture for source=%s (expected %s)", source, path)
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("sample fixture unreadable source=%s (%s)", source, exc)
        return None
