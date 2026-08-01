"""Issue manifest and the root archive page.

`newsletters/manifest.json` is the single source of truth for which issues exist. The
archive page is rendered from it rather than by scanning directories or re-parsing
generated HTML, which keeps issue numbering stable and the archive cheap to rebuild.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from render.html import (
    ACCENT, CANVAS, FONT_BODY, FONT_DISPLAY, INK, INK_SOFT, MUTED, PAPER, RULE, e,
)

log = logging.getLogger(__name__)


# ------------------------------------------------------------------------------------
# Manifest
# ------------------------------------------------------------------------------------


def load_manifest() -> Dict[str, Any]:
    if not config.MANIFEST_PATH.exists():
        return {"issues": []}
    try:
        with config.MANIFEST_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or "issues" not in data:
            return {"issues": []}
        return data
    except (OSError, ValueError) as exc:
        log.warning("manifest unreadable, starting a fresh one (%s)", exc)
        return {"issues": []}


def save_manifest(manifest: Dict[str, Any]) -> None:
    config.MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.MANIFEST_PATH.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)


def next_issue_number(manifest: Dict[str, Any], issue_date: str) -> int:
    """Reuse the number if this date was already published, else take the next one.

    Re-running for the same date must overwrite that issue rather than inflate the
    count — otherwise a re-run on a Monday would produce a duplicate issue.
    """
    for issue in manifest.get("issues", []):
        if issue.get("date") == issue_date:
            return int(issue.get("number", 1))
    numbers = [int(i.get("number", 0)) for i in manifest.get("issues", [])]
    return (max(numbers) + 1) if numbers else 1


def record_issue(
    manifest: Dict[str, Any],
    number: int,
    issue_date: str,
    item_count: int,
    category_counts: Dict[str, int],
    top_headline: str,
    ai_enabled: bool,
) -> Dict[str, Any]:
    entry = {
        "number": number,
        "date": issue_date,
        "path": "newsletters/{0}/index.html".format(issue_date),
        "text_path": "newsletters/{0}/newsletter.txt".format(issue_date),
        "item_count": item_count,
        "categories": category_counts,
        "top_headline": top_headline,
        "ai_enabled": ai_enabled,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    issues = [i for i in manifest.get("issues", []) if i.get("date") != issue_date]
    issues.append(entry)
    issues.sort(key=lambda i: i.get("date", ""), reverse=True)
    manifest["issues"] = issues
    return manifest


# ------------------------------------------------------------------------------------
# Archive page
# ------------------------------------------------------------------------------------


def _row(issue: Dict[str, Any], latest: bool) -> str:
    date_display = issue.get("date", "")
    try:
        date_display = datetime.strptime(issue["date"], "%Y-%m-%d").strftime("%d %B %Y")
    except (KeyError, ValueError):
        pass

    cats = issue.get("categories") or {}
    chips = "".join(
        '<span style="display:inline-block;background:{canvas};color:{soft};'
        'font-family:{font};font-size:11px;padding:3px 8px;border-radius:3px;'
        'margin:0 6px 0 0;">{name} {n}</span>'.format(
            canvas=CANVAS, soft=INK_SOFT, font=FONT_BODY, name=e(name), n=int(count)
        )
        for name, count in cats.items() if count
    )

    badge = (
        '<span style="display:inline-block;background:{a};color:#fff;font-family:{f};'
        'font-size:10px;font-weight:700;letter-spacing:.08em;padding:3px 7px;'
        'border-radius:3px;margin-left:8px;">LATEST</span>'.format(a=ACCENT, f=FONT_BODY)
        if latest else ""
    )

    headline = issue.get("top_headline") or ""
    headline_html = (
        '<div style="font-family:{f};font-size:13px;color:{m};margin:6px 0 8px;">'
        'Lead item: {h}</div>'.format(f=FONT_BODY, m=MUTED, h=e(headline))
        if headline else ""
    )

    return """
      <tr><td style="padding:20px 0;border-bottom:1px solid {rule};">
        <div style="margin-bottom:4px;">
          <a href="{path}" style="font-family:{fdisp};font-size:19px;font-weight:700;
             color:{ink};text-decoration:none;">Issue {num} &middot; {date}</a>{badge}
        </div>
        {headline}
        <div style="margin-bottom:10px;">{chips}</div>
        <div>
          <a href="{path}" style="font-family:{fbody};font-size:12.5px;font-weight:600;
             color:{accent};text-decoration:none;">Read issue &rarr;</a>
          <span style="color:{rule};"> &middot; </span>
          <a href="{tpath}" style="font-family:{fbody};font-size:12.5px;color:{muted};
             text-decoration:none;">Plain text</a>
          <span style="color:{rule};"> &middot; </span>
          <span style="font-family:{fbody};font-size:12.5px;color:{muted};">{count} item{plural}</span>
        </div>
      </td></tr>""".format(
        rule=RULE, path=e(issue.get("path", "#")), fdisp=FONT_DISPLAY, ink=INK,
        num=issue.get("number", "?"), date=e(date_display), badge=badge,
        headline=headline_html, chips=chips, fbody=FONT_BODY, accent=ACCENT,
        tpath=e(issue.get("text_path", "#")), muted=MUTED,
        count=issue.get("item_count", 0),
        plural="" if issue.get("item_count") == 1 else "s",
    )


def render_archive(manifest: Dict[str, Any]) -> str:
    issues = manifest.get("issues", [])
    total_items = sum(int(i.get("item_count", 0)) for i in issues)

    if issues:
        rows = "".join(_row(issue, latest=(idx == 0)) for idx, issue in enumerate(issues))
    else:
        rows = (
            '<tr><td style="padding:40px 0;text-align:center;font-family:{f};'
            'font-size:14px;color:{m};">No issues published yet. Run '
            '<code>python run.py</code> to generate the first edition.</td></tr>'
        ).format(f=FONT_BODY, m=MUTED)

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{name} &middot; Archive</title>
<style>
  body {{ margin:0 !important; padding:0 !important; }}
  @media only screen and (max-width:640px) {{
    .wrap {{ width:100% !important; padding-left:18px !important; padding-right:18px !important; }}
    .masthead-title {{ font-size:30px !important; }}
  }}
  @media (prefers-color-scheme: dark) {{
    body, .canvas {{ background:#0f1720 !important; }}
    .paper {{ background:#161f2a !important; }}
    .masthead-title, .t-ink {{ color:#e8eef5 !important; }}
  }}
</style>
</head>
<body class="canvas" style="margin:0;padding:0;background:{canvas};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{canvas};padding:28px 12px;">
  <tr><td align="center">
    <table role="presentation" class="wrap paper" width="680" cellpadding="0" cellspacing="0" border="0"
           style="width:680px;max-width:680px;background:{paper};border-radius:5px;
                  box-shadow:0 1px 3px rgba(18,38,63,.10);padding:0 40px 40px;">

      <tr><td style="padding:38px 0 0;">
        <div style="border-bottom:3px solid {ink};padding-bottom:16px;">
          <div style="font-family:{fbody};font-size:10.5px;font-weight:700;letter-spacing:.18em;
               text-transform:uppercase;color:{accent};margin-bottom:8px;">Archive</div>
          <div class="masthead-title" style="font-family:{fdisp};font-size:37px;line-height:1.1;
               font-weight:700;color:{ink};letter-spacing:-.5px;">{name}</div>
          <div style="font-family:{fbody};font-size:13.5px;color:{muted};margin-top:8px;">{tagline}</div>
        </div>
        <div style="font-family:{fbody};font-size:12.5px;color:{muted};padding-top:12px;">
          {n} issue{plural} published<span style="color:{rule};"> &middot; </span>{total} development{tplural} tracked
        </div>
      </td></tr>

      <tr><td style="padding:14px 0 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>
      </td></tr>

      <tr><td style="padding:30px 0 0;">
        <div style="border-top:1px solid {rule};padding-top:18px;font-family:{fbody};
             font-size:11.5px;line-height:1.7;color:{muted};">
          <strong style="color:{soft};">{name}</strong> is an automated competitive-intelligence
          briefing for the Multiple Sclerosis market. A new edition is compiled every Monday at
          08:00 IST from ClinicalTrials.gov, U.S. FDA RSS feeds and curated news, then published
          here automatically.<br>
          Summarises public information for competitive intelligence purposes; not medical,
          investment or regulatory advice.
        </div>
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>""".format(
        name=e(config.NEWSLETTER_NAME), canvas=CANVAS, paper=PAPER, ink=INK,
        fbody=FONT_BODY, fdisp=FONT_DISPLAY, accent=ACCENT, muted=MUTED,
        tagline=e(config.NEWSLETTER_TAGLINE), rule=RULE, rows=rows,
        n=len(issues), plural="" if len(issues) == 1 else "s",
        total=total_items, tplural="" if total_items == 1 else "s", soft=INK_SOFT,
    )
