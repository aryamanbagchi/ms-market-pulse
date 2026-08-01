"""ClinicalTrials.gov API v2 fetcher.

Pulls MS studies whose LastUpdatePostDate falls in the lookback window. That single
filter satisfies both halves of "newly posted OR status-updated": a newly posted study
has a last-update date equal to its post date.

Field paths below were verified against a live response on 2026-08-01.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import config
from core.models import Item
from fetchers.base import FetchContext, clean_text, http_get, parse_iso_date

log = logging.getLogger(__name__)

NAME = "clinicaltrials"

FIELDS = ",".join([
    "NCTId",
    "BriefTitle",
    "OverallStatus",
    "Phase",
    "LeadSponsorName",
    "LastUpdatePostDate",
    "BriefSummary",
    "StudyType",
])


def fetch_raw(ctx: FetchContext) -> Dict[str, Any]:
    """Page through the API and return the combined list of studies."""
    date_filter = "AREA[LastUpdatePostDate]RANGE[{0},MAX]".format(
        ctx.window_start.strftime("%Y-%m-%d")
    )

    studies: List[Dict[str, Any]] = []
    page_token: Optional[str] = None

    for page in range(config.CLINICALTRIALS_MAX_PAGES):
        params = {
            "query.cond": "multiple sclerosis",
            "filter.advanced": date_filter,
            "fields": FIELDS,
            "pageSize": config.CLINICALTRIALS_PAGE_SIZE,
        }
        if page_token:
            params["pageToken"] = page_token

        payload = http_get(config.CLINICALTRIALS_URL, params=params).json()
        batch = payload.get("studies") or []
        studies.extend(batch)

        page_token = payload.get("nextPageToken")
        if not page_token or not batch:
            break

    return {"studies": studies}


def parse(payload: Dict[str, Any], ctx: FetchContext) -> List[Item]:
    items: List[Item] = []

    for study in (payload or {}).get("studies") or []:
        try:
            item = _parse_study(study, ctx)
        except Exception as exc:  # noqa: BLE001 - one bad record must not kill the feed
            log.debug("skipping malformed study: %s", exc)
            continue
        if item is not None:
            items.append(item)

    return items


def _parse_study(study: Dict[str, Any], ctx: FetchContext) -> Optional[Item]:
    protocol = study.get("protocolSection") or {}

    identification = protocol.get("identificationModule") or {}
    status_mod = protocol.get("statusModule") or {}
    sponsor_mod = protocol.get("sponsorCollaboratorsModule") or {}
    description = protocol.get("descriptionModule") or {}
    design = protocol.get("designModule") or {}

    nct_id = identification.get("nctId")
    title = clean_text(identification.get("briefTitle"))
    if not nct_id or not title:
        return None

    published = parse_iso_date(
        (status_mod.get("lastUpdatePostDateStruct") or {}).get("date")
    )
    if not ctx.in_window(published):
        return None

    # `phases` is absent for observational studies; surface the study type instead so
    # the item never renders with an empty phase slot.
    phases = design.get("phases") or []
    if phases:
        phase = ", ".join(_pretty_phase(p) for p in phases)
    else:
        phase = _pretty_phase(design.get("studyType") or "")

    return Item(
        source=NAME,
        title=title,
        url="https://clinicaltrials.gov/study/{0}".format(nct_id),
        published=published,
        raw_text=clean_text(description.get("briefSummary")),
        sponsor=clean_text((sponsor_mod.get("leadSponsor") or {}).get("name")) or None,
        phase=phase or None,
        status=_pretty_phase(status_mod.get("overallStatus") or "") or None,
        nct_id=nct_id,
    )


def _pretty_phase(value: str) -> str:
    """'PHASE2' -> 'Phase 2'; 'NOT_YET_RECRUITING' -> 'Not Yet Recruiting'."""
    if not value:
        return ""
    if value.upper().startswith("PHASE") and value[5:].strip():
        return "Phase {0}".format(value[5:].strip())
    if value.upper() == "NA":
        return "N/A"
    return value.replace("_", " ").title()
