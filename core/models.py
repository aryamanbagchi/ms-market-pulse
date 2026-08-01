"""The single data structure that flows through the whole pipeline.

Fetchers produce `Item`s with the source fields populated. The AI layer fills in the
enrichment fields. The renderers read both halves.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import config


@dataclass
class Item:
    """One piece of competitive intelligence, from any source."""

    # --- Source fields, set by fetchers -------------------------------------------
    source: str  # "clinicaltrials" | "fda" | "news"
    title: str
    url: str
    published: Optional[datetime] = None
    raw_text: str = ""

    # Source-specific extras. Any of these may be absent depending on the source.
    sponsor: Optional[str] = None
    phase: Optional[str] = None
    status: Optional[str] = None
    nct_id: Optional[str] = None
    publisher: Optional[str] = None
    feed: Optional[str] = None

    # --- Enrichment fields, set by the AI layer (or its deterministic fallback) ----
    summary: str = ""
    category: str = config.CATEGORY_CLINICAL
    importance: int = config.DEFAULT_IMPORTANCE
    why_it_matters: str = ""
    enriched_by: str = "none"  # "gemini" | "fallback" | "none"

    # --- Set by the dedupe pass ---------------------------------------------------
    also_reported_by: List[Dict[str, str]] = field(default_factory=list)

    # ------------------------------------------------------------------------------

    @property
    def item_id(self) -> str:
        """Stable identifier, used for cache keys and de-dup bookkeeping."""
        if self.nct_id:
            return self.nct_id
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]

    @property
    def published_display(self) -> str:
        if self.published is None:
            return "Date unknown"
        return self.published.strftime("%d %b %Y")

    @property
    def source_label(self) -> str:
        """Human-readable attribution shown next to each item."""
        if self.source == "clinicaltrials":
            return "ClinicalTrials.gov"
        if self.source == "fda":
            return "FDA"
        if self.source == "news":
            return self.publisher or "News"
        return self.source

    def clamp(self) -> "Item":
        """Force enrichment fields into legal ranges. Cheap insurance against a model
        returning an out-of-range score or an invented category."""
        try:
            self.importance = int(self.importance)
        except (TypeError, ValueError):
            self.importance = config.DEFAULT_IMPORTANCE
        self.importance = max(
            config.MIN_IMPORTANCE, min(config.MAX_IMPORTANCE, self.importance)
        )
        if self.category not in config.CATEGORIES:
            self.category = config.CATEGORY_CLINICAL
        return self

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["published"] = self.published.isoformat() if self.published else None
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Item":
        d = dict(d)
        published = d.get("published")
        if isinstance(published, str) and published:
            try:
                d["published"] = datetime.fromisoformat(published)
            except ValueError:
                d["published"] = None
        return cls(**d)
