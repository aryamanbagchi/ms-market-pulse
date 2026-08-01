#!/usr/bin/env python3
"""MS Market Pulse — entry point.

Fetch -> deduplicate -> enrich -> render -> archive.

    python run.py                 normal weekly run
    python run.py --dry-run       frozen fixtures, no network, no AI calls
    python run.py --no-cache      ignore today's cache and refetch
    python run.py --no-ai         fetch live but skip Gemini
    python run.py --date 2026-07-27
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime
from typing import List

import config
from ai import editor as ai_editor
from ai import fallback
from ai import gemini
from ai import summarize as ai_summarize
from core.dedupe import deduplicate
from core.models import Item
from fetchers import clinicaltrials, fda, news
from fetchers.base import FetchContext, safe_fetch
from render import archive as render_archive
from render import html as render_html
from render import text as render_text

log = logging.getLogger("mspulse")

SOURCES = [clinicaltrials, fda, news]


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Generate the MS Market Pulse competitive-intelligence newsletter.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="use frozen sample fixtures; makes no network and no AI calls",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="ignore today's cached payloads and refetch from source",
    )
    parser.add_argument(
        "--no-ai", action="store_true",
        help="fetch live but skip Gemini; use deterministic enrichment",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="issue date; the lookback window ends here (default: today)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="enable debug logging",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
        stream=sys.stdout,
    )
    # Third-party noise drowns out the pipeline's own progress reporting.
    for noisy in ("urllib3", "requests", "charset_normalizer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def resolve_run_date(raw) -> datetime:
    if not raw:
        return datetime.utcnow()
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise SystemExit("error: --date must be YYYY-MM-DD (got {0!r})".format(raw))


def split_by_impact(items: List[Item]):
    """Split scored items into what gets written up and what gets listed.

    Filtering is the product: screening 40 items and publishing 9 is the service a CI
    briefing performs. Publishing all 40 — including the ones the model itself scored 1
    and described as having no commercial impact — is just a feed with extra steps.

    Returns (featured, also_tracked, threshold_used).
    """
    threshold = config.MIN_IMPACT_TO_FEATURE
    featured = [i for i in items if i.importance >= threshold]

    # A briefing with two items looks broken however honest the filtering was, so a thin
    # week relaxes the bar for that issue rather than publishing something that reads as
    # a failed run. Logged, because a silent change of editorial standard is worse than
    # a visible one.
    if items and len(featured) < config.MIN_FEATURED_ITEMS:
        relaxed = config.RELAXED_IMPACT_TO_FEATURE
        widened = [i for i in items if i.importance >= relaxed]
        if len(widened) > len(featured):
            log.info(
                "threshold     only %d item(s) at impact >=%d; lowering to >=%d "
                "for this issue (%d item(s))",
                len(featured), threshold, relaxed, len(widened),
            )
            threshold, featured = relaxed, widened

    featured_ids = {id(i) for i in featured}
    also_tracked = [i for i in items if id(i) not in featured_ids]
    return featured, also_tracked, threshold


def collect(ctx: FetchContext) -> List[Item]:
    """Fetch every source. A failing source contributes nothing and is logged."""
    items: List[Item] = []
    for module in SOURCES:
        items.extend(safe_fetch(module.NAME, module.fetch_raw, module.parse, ctx))
    return items


def main(argv=None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    run_date = resolve_run_date(args.date)
    ctx = FetchContext(
        run_date=run_date,
        use_cache=not args.no_cache,
        dry_run=args.dry_run,
    )

    use_ai = not (args.dry_run or args.no_ai)
    if use_ai and not gemini.is_available():
        log.warning(
            "%s not set — continuing with deterministic enrichment",
            config.GEMINI_API_KEY_ENV,
        )
        use_ai = False

    mode = "dry-run" if args.dry_run else ("live" if use_ai else "live, no-AI")
    log.info("%s", "=" * 62)
    log.info("%s  |  issue date %s  |  mode: %s",
             config.NEWSLETTER_NAME, ctx.run_date_str, mode)
    log.info("window        %s -> %s (%d days)",
             ctx.window_start.strftime("%Y-%m-%d"), ctx.run_date_str, config.LOOKBACK_DAYS)
    log.info("%s", "=" * 62)

    # ---- fetch -------------------------------------------------------------------
    items = collect(ctx)
    if not items:
        log.warning("no items from any source — rendering a quiet-week issue")

    # ---- deduplicate -------------------------------------------------------------
    items = deduplicate(items)

    # ---- cap ---------------------------------------------------------------------
    if len(items) > config.MAX_ITEMS_PER_ISSUE:
        items.sort(key=lambda i: -(i.published.timestamp() if i.published else 0))
        log.info("cap           trimming %d -> %d item(s) (newest first)",
                 len(items), config.MAX_ITEMS_PER_ISSUE)
        items = items[: config.MAX_ITEMS_PER_ISSUE]

    # ---- enrich ------------------------------------------------------------------
    if use_ai:
        items = ai_summarize.enrich_all(items)
    else:
        items = fallback.enrich_all(items)
        log.info("enrich        %d item(s) via deterministic rules", len(items))

    # ---- filter ------------------------------------------------------------------
    featured, also_tracked, threshold = split_by_impact(items)
    log.info("filter        %d screened -> %d featured (impact >=%d), %d also tracked",
             len(items), len(featured), threshold, len(also_tracked))

    # The Editor's Take reasons over the featured set only. Feeding it the long tail
    # invites it to build a narrative out of items the issue has judged not to matter.
    editors_take = (
        ai_editor.write(featured) if use_ai else fallback.editors_take(featured)
    )

    # ---- render ------------------------------------------------------------------
    manifest = render_archive.load_manifest()
    issue_number = render_archive.next_issue_number(manifest, ctx.run_date_str)
    generated_at = datetime.utcnow()

    html_doc = render_html.render(
        featured, also_tracked, editors_take, issue_number,
        ctx.run_date, ctx.window_start,
        generated_at=generated_at, ai_enabled=use_ai,
    )
    text_doc = render_text.render(
        featured, also_tracked, editors_take, issue_number,
        ctx.run_date, ctx.window_start,
        generated_at=generated_at, ai_enabled=use_ai,
    )

    issue_dir = config.NEWSLETTERS_DIR / ctx.run_date_str
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "index.html").write_text(html_doc, encoding="utf-8")
    (issue_dir / "newsletter.txt").write_text(text_doc, encoding="utf-8")

    # ---- archive -----------------------------------------------------------------
    # The archive counts what the issue published, not what it read; `screened_count`
    # keeps the wider number available without inflating the headline figure.
    counts = Counter(i.category for i in featured)
    top = max(featured, key=lambda i: i.importance).title if featured else ""

    manifest = render_archive.record_issue(
        manifest,
        number=issue_number,
        issue_date=ctx.run_date_str,
        item_count=len(featured),
        category_counts={c: counts.get(c, 0) for c in config.CATEGORIES if counts.get(c)},
        top_headline=top,
        ai_enabled=use_ai,
        screened_count=len(items),
    )
    render_archive.save_manifest(manifest)
    config.ARCHIVE_PATH.write_text(
        render_archive.render_archive(manifest), encoding="utf-8"
    )

    # ---- report ------------------------------------------------------------------
    log.info("%s", "-" * 62)
    log.info("issue %-3d     %d featured of %d screened: %s",
             issue_number, len(featured), len(items),
             ", ".join("{0} {1}".format(n, c.lower()) for c, n in counts.items()) or "none")
    log.info("wrote         %s", (issue_dir / "index.html").relative_to(config.ROOT))
    log.info("wrote         %s", (issue_dir / "newsletter.txt").relative_to(config.ROOT))
    log.info("wrote         %s", config.ARCHIVE_PATH.relative_to(config.ROOT))
    log.info("%s", "-" * 62)

    return 0


if __name__ == "__main__":
    sys.exit(main())
