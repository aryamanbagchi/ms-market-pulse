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
    "Condition",
    "OverallStatus",
    "Phase",
    "LeadSponsorName",
    "LastUpdatePostDate",
    "BriefSummary",
    "StudyType",
])


def _is_ms_study(conditions: List[str]) -> bool:
    """True if the study's own Condition list names an MS indication.

    `query.cond` is a relevance search, not a filter — it returns studies that merely
    mention MS anywhere in the record, which is how an epilepsy gene-therapy trial and a
    paediatric anxiety study reached issue 1. The study's declared conditions are the
    only field that states what is actually being treated.
    """
    blob = " ".join(c.lower() for c in conditions if c)
    return any(term in blob for term in config.MS_TRIAL_CONDITIONS)


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
    off_indication = 0

    studies = (payload or {}).get("studies") or []

    # Fail open, loudly. If not one study in the payload carries a Condition list, the
    # field was not returned — a renamed path, a stale cache, an API change — and the
    # gate would silently drop every study and publish an empty issue. A gate that can
    # zero out the newsletter on a schema change is worse than no gate.
    gate = any((s.get("protocolSection") or {}).get("conditionsModule", {}).get("conditions")
               for s in studies)
    if studies and not gate:
        log.warning("source=clinicaltrials  no Condition data in payload — "
                    "condition filter disabled for this run")

    for study in studies:
        try:
            item = _parse_study(study, ctx, gate)
        except _OffIndication as drop:
            # Logged individually and at INFO, because "what did the filter throw away"
            # is the first question anyone asks of a filter.
            off_indication += 1
            log.info("filtered      %s not an MS study — conditions: %s",
                     drop.nct_id, drop.conditions or "(none listed)")
            continue
        except Exception as exc:  # noqa: BLE001 - one bad record must not kill the feed
            log.debug("skipping malformed study: %s", exc)
            continue
        if item is not None:
            items.append(item)

    if off_indication:
        log.info("filtered      %d study/studies dropped as off-indication", off_indication)

    return items


class _OffIndication(Exception):
    """Raised for a study whose declared conditions are not MS. Carries the evidence."""

    def __init__(self, nct_id: str, conditions: List[str]):
        super().__init__(nct_id)
        self.nct_id = nct_id
        self.conditions = ", ".join(conditions)


def _parse_study(
    study: Dict[str, Any], ctx: FetchContext, gate: bool = True
) -> Optional[Item]:
    protocol = study.get("protocolSection") or {}

    identification = protocol.get("identificationModule") or {}
    status_mod = protocol.get("statusModule") or {}
    sponsor_mod = protocol.get("sponsorCollaboratorsModule") or {}
    description = protocol.get("descriptionModule") or {}
    design = protocol.get("designModule") or {}
    conditions_mod = protocol.get("conditionsModule") or {}

    nct_id = identification.get("nctId")
    title = clean_text(identification.get("briefTitle"))
    if not nct_id or not title:
        return None

    conditions = [c for c in (conditions_mod.get("conditions") or []) if c]
    if gate and not _is_ms_study(conditions):
        raise _OffIndication(nct_id, conditions)

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
