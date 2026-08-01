"""Self-contained HTML newsletter renderer.

Constraints that drive every decision here:

  * No external assets of any kind — no CDN, no webfonts, no images, no JS. The file
    must render identically from disk, from GitHub Pages, or pasted into an email.
  * Table-based layout with styles applied inline on each element, because Gmail and
    Outlook strip <style> blocks entirely. The <style> block is used only for
    progressive enhancement (responsive tweaks, dark mode) that email clients ignore
    and browsers honour.
  * Web-safe fonts only, for the same reason.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Dict, List, Optional

import config
from core.models import Item

# ------------------------------------------------------------------------------------
# Palette — a restrained consulting-report look: deep navy, warm neutrals, one accent.
# ------------------------------------------------------------------------------------

INK = "#12263f"
INK_SOFT = "#48607d"
MUTED = "#7a8ba0"
RULE = "#dfe5ec"
PAPER = "#ffffff"
CANVAS = "#eef1f5"
ACCENT = "#0b6b63"

# Importance badge ramp: low scores recede, high scores demand attention.
BADGE_COLORS = {
    1: ("#eef1f5", "#66788d"),
    2: ("#e3edf3", "#3f6d88"),
    3: ("#dcecea", "#0b6b63"),
    4: ("#fdeede", "#a85b12"),
    5: ("#fbe4e2", "#a3271c"),
}

CATEGORY_BLURB = {
    config.CATEGORY_REGULATORY: "Approvals, submissions, labelling and access decisions",
    config.CATEGORY_CLINICAL: "Trial starts, status changes, readouts and pipeline movement",
    config.CATEGORY_COMMERCIAL: "Deals, earnings, pricing, launches and competitive positioning",
}

# Shown when a category has nothing to feature. An absent section reads as a broken
# pipeline; a section that names the sources it checked reads as an editorial finding.
# MS regulatory activity in particular is infrequent, so an empty Regulatory section is
# the normal case rather than the exception.
CATEGORY_SOURCES = {
    config.CATEGORY_REGULATORY:
        "No FDA actions affecting the MS market in this window. "
        "Sources checked: FDA press releases, drugs and MedWatch feeds.",
    config.CATEGORY_CLINICAL:
        "No trial activity met the threshold in this window. "
        "Source checked: ClinicalTrials.gov API v2, studies posted or updated in the "
        "last 7 days.",
    config.CATEGORY_COMMERCIAL:
        "No qualifying deal, earnings or pricing coverage in this window. "
        "Source checked: curated industry news.",
}

FONT_DISPLAY = "Georgia, 'Times New Roman', Times, serif"
FONT_BODY = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

# --------------------------------------------------------------------------------------
# Dark mode
#
# The inline styles carry the light design and are what email clients see. Dark mode is a
# browser-only enhancement layered on top, and it can only reach an element that carries
# one of these classes — an inline `color:` beats a stylesheet rule unless the rule is
# `!important` AND selects the element.
#
# That is why every colour below is paired with a class applied at the point of use.
# Earlier revisions declared `.t-ink` and `.t-soft` here but never put those classes on
# any element, so headings and body copy stayed near-black on a near-black background.
# If you introduce a new coloured element, give it the matching class or it will be
# invisible to half your readers.
# --------------------------------------------------------------------------------------

DARK_CSS = """
  @media (prefers-color-scheme: dark) {
    body, .canvas { background:#0f1720 !important; }
    .paper        { background:#161f2a !important; }
    .card         { background:#1e2937 !important; }
    .card-ink     { background:#1b2b3d !important; }
    .masthead-title, .t-ink, .t-ink a { color:#e8eef5 !important; }
    .t-soft, .t-soft a { color:#c3d0de !important; }
    .t-muted      { color:#94a5b8 !important; }
    .t-accent, .t-accent a { color:#5fbfb2 !important; }
    .b-ink        { border-color:#46566b !important; }
    .b-rule       { border-color:#2b3746 !important; }
  }"""


def e(value: Optional[str]) -> str:
    """Escape for HTML. Every dynamic value passes through here."""
    return html.escape(value or "", quote=True)


# ------------------------------------------------------------------------------------
# Components
# ------------------------------------------------------------------------------------


def _badge(importance: int) -> str:
    bg, fg = BADGE_COLORS.get(importance, BADGE_COLORS[3])
    return (
        '<span style="display:inline-block;background:{bg};color:{fg};'
        'font-family:{font};font-size:11px;font-weight:700;letter-spacing:.06em;'
        'padding:4px 9px;border-radius:3px;white-space:nowrap;">'
        'IMPACT {n}/5</span>'
    ).format(bg=bg, fg=fg, font=FONT_BODY, n=importance)


def _meta_line(item: Item) -> str:
    """The small grey line of provenance under each headline."""
    bits: List[str] = [e(item.source_label), e(item.published_display)]

    for extra in (item.phase, item.status, item.sponsor):
        if extra:
            bits.append(e(extra))
    if item.nct_id:
        bits.append(e(item.nct_id))

    sep = '<span style="color:{0};"> &middot; </span>'.format(RULE)
    return sep.join(bits)


def _corroboration(item: Item) -> str:
    if not item.also_reported_by:
        return ""
    links = ", ".join(
        '<a href="{url}" style="color:{c};text-decoration:none;">{label}</a>'.format(
            url=e(r.get("url")), label=e(r.get("label")), c=INK_SOFT
        )
        for r in item.also_reported_by
    )
    return (
        '<div class="t-muted" style="margin:8px 0 0;font-family:{font};font-size:12px;'
        'color:{muted};">Also reported by: {links}</div>'
    ).format(font=FONT_BODY, muted=MUTED, links=links)


def _item_block(item: Item, last: bool = False) -> str:
    """One development. `last` drops the separator rule on the final item of a section,
    so it never doubles up with the rule that opens whatever follows."""
    return """
      <tr>
        <td class="b-rule" style="padding:22px 0 {pad};{border}">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="padding:0 14px 8px 0;vertical-align:top;">
                <a class="t-ink" href="{url}" style="font-family:{fdisp};font-size:18px;line-height:1.35;
                   font-weight:700;color:{ink};text-decoration:none;">{title}</a>
              </td>
              <td style="vertical-align:top;text-align:right;white-space:nowrap;padding-bottom:8px;">{badge}</td>
            </tr>
          </table>

          <div class="t-muted" style="font-family:{fbody};font-size:12px;color:{muted};margin:0 0 12px;">{meta}</div>

          <p class="t-soft" style="font-family:{fbody};font-size:14.5px;line-height:1.62;color:{inksoft};margin:0 0 12px;">{summary}</p>

          <table role="presentation" class="card" width="100%" cellpadding="0" cellspacing="0" border="0"
                 style="background:{canvas};border-left:3px solid {accent};border-radius:0 3px 3px 0;">
            <tr>
              <td style="padding:11px 14px;">
                <div class="t-accent" style="font-family:{fbody};font-size:10.5px;font-weight:700;letter-spacing:.1em;
                     text-transform:uppercase;color:{accent};margin-bottom:4px;">Why it matters</div>
                <div class="t-ink" style="font-family:{fbody};font-size:13.5px;line-height:1.55;color:{ink};">{why}</div>
              </td>
            </tr>
          </table>
          {corroboration}
          <div style="margin:12px 0 0;">
            <a class="t-accent" href="{url}" style="font-family:{fbody};font-size:12.5px;font-weight:600;
               color:{accent};text-decoration:none;">Read the source &rarr;</a>
          </div>
        </td>
      </tr>""".format(
        border="" if last else "border-bottom:1px solid {0};".format(RULE),
        pad="4px" if last else "22px",
        url=e(item.url), fdisp=FONT_DISPLAY, ink=INK,
        title=e(item.title), badge=_badge(item.importance), fbody=FONT_BODY,
        muted=MUTED, meta=_meta_line(item), inksoft=INK_SOFT,
        summary=e(item.summary), canvas=CANVAS, accent=ACCENT,
        why=e(item.why_it_matters), corroboration=_corroboration(item),
    )


def _empty_category(category: str, screened: int) -> str:
    """The muted note that stands in for a section with nothing to feature.

    Distinguishes the two reasons a section can be empty, because they mean different
    things: nothing was found at all, or things were found and judged not worth the
    reader's time. The second is a stronger statement about the filtering.
    """
    if screened:
        note = (
            "{0} item{1} screened in this category, none clearing the impact threshold. "
            "Listed under Also tracked below."
        ).format(screened, "" if screened == 1 else "s")
    else:
        note = CATEGORY_SOURCES.get(category, "Nothing qualifying in this window.")

    return """
      <tr><td style="padding:18px 0 4px;">
        <div class="t-muted" style="font-family:{fbody};font-size:13.5px;line-height:1.6;
             color:{muted};font-style:italic;">{note}</div>
      </td></tr>""".format(fbody=FONT_BODY, muted=MUTED, note=e(note))


def _section(category: str, items: List[Item], screened_below: int = 0) -> str:
    """Render one category, including when it has nothing to feature."""
    if items:
        # The last item drops its rule; the next section's own top border supplies the
        # separation. Two rules a whitespace-gap apart read as an empty container.
        rows = "".join(
            _item_block(i, last=(n == len(items) - 1)) for n, i in enumerate(items)
        )
        count_label = "{0} item{1}".format(len(items), "" if len(items) == 1 else "s")
    else:
        rows = _empty_category(category, screened_below)
        count_label = "nothing featured"

    return """
      <tr><td style="padding:34px 0 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr><td class="b-ink" style="border-top:2px solid {ink};padding-top:10px;">
            <div class="t-ink" style="font-family:{fdisp};font-size:21px;font-weight:700;color:{ink};">{name}</div>
            <div class="t-muted" style="font-family:{fbody};font-size:12.5px;color:{muted};margin-top:3px;">{blurb}
              <span style="color:{rule};"> &middot; </span>{count}</div>
          </td></tr>
        </table>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>
      </td></tr>""".format(
        ink=INK, fdisp=FONT_DISPLAY, name=e(category), fbody=FONT_BODY, muted=MUTED,
        blurb=e(CATEGORY_BLURB.get(category, "")), rule=RULE,
        count=count_label, rows=rows,
    )


def _also_tracked_tag(item: Item) -> str:
    """The short provenance tag beside an also-tracked title: who, and from where."""
    bits = [item.source_label]
    if item.sponsor and item.sponsor != item.source_label:
        bits.append(item.sponsor)
    elif item.phase:
        bits.append(item.phase)
    return " · ".join(bits)


def _also_tracked(items: List[Item]) -> str:
    """Titles only, no summaries, no badges — the evidence that the sweep was wide.

    Deliberately austere. The whole point of the section is that these items did not
    earn the reader's attention, so giving them visual weight would undo the filtering.
    """
    if not items:
        return ""

    ordered = sorted(
        items,
        key=lambda i: (-i.importance, -(i.published.timestamp() if i.published else 0)),
    )

    rows = "".join("""
            <tr><td class="b-rule" style="padding:7px 0;{border}">
              <a class="t-soft" href="{url}" style="font-family:{fbody};font-size:12.5px;line-height:1.5;
                 color:{soft};text-decoration:none;">{title}</a>
              <span class="t-muted" style="font-family:{fbody};font-size:11px;color:{muted};"> &nbsp;{tag}</span>
            </td></tr>""".format(
        border="" if n == len(ordered) - 1 else "border-bottom:1px solid {0};".format(RULE),
        url=e(i.url), fbody=FONT_BODY, soft=INK_SOFT,
        title=e(i.title), muted=MUTED, tag=e(_also_tracked_tag(i)),
    ) for n, i in enumerate(ordered))

    return """
      <tr><td style="padding:34px 0 0;">
        <table role="presentation" class="card" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="background:{canvas};border-radius:4px;">
          <tr><td style="padding:20px 22px;">
            <div class="t-muted" style="font-family:{fbody};font-size:10.5px;font-weight:700;letter-spacing:.12em;
                 text-transform:uppercase;color:{muted};margin-bottom:4px;">Also tracked this week</div>
            <div class="t-muted" style="font-family:{fbody};font-size:11.5px;color:{muted};margin-bottom:10px;">
              Screened and scored below the impact threshold. Listed for completeness.</div>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>
          </td></tr>
        </table>
      </td></tr>""".format(
        canvas=CANVAS, fbody=FONT_BODY, muted=MUTED, rows=rows,
    )


def _quiet_week() -> str:
    """Shown when a run yields nothing. Reads as an editorial note, not a failure."""
    sources = ", ".join(["ClinicalTrials.gov", "FDA (3 feeds)", "curated news"])
    return """
      <tr><td style="padding:38px 0 0;">
        <table role="presentation" class="card" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="background:{canvas};border-radius:4px;">
          <tr><td style="padding:30px 26px;text-align:center;">
            <div class="t-ink" style="font-family:{fdisp};font-size:19px;font-weight:700;color:{ink};margin-bottom:8px;">
              A quiet week in MS</div>
            <p class="t-soft" style="font-family:{fbody};font-size:14px;line-height:1.65;color:{inksoft};margin:0;">
              No qualifying activity cleared our relevance and recency filters in this reporting
              window. All sources were polled successfully &mdash; {sources} &mdash; and returned
              nothing new for Multiple Sclerosis. Quiet weeks are normal in a single indication.
            </p>
          </td></tr>
        </table>
      </td></tr>""".format(
        canvas=CANVAS, fdisp=FONT_DISPLAY, ink=INK, fbody=FONT_BODY,
        inksoft=INK_SOFT, sources=e(sources),
    )


def _editors_take(text: str) -> str:
    return """
      <tr><td style="padding:30px 0 0;">
        <table role="presentation" class="card-ink" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="background:{ink};border-radius:4px;">
          <tr><td style="padding:26px 26px 24px;">
            <div style="font-family:{fbody};font-size:10.5px;font-weight:700;letter-spacing:.14em;
                 text-transform:uppercase;color:#8fb8b3;margin-bottom:10px;">Editor's Take</div>
            <p style="font-family:{fdisp};font-size:16px;line-height:1.68;color:#f2f5f8;margin:0;">{text}</p>
          </td></tr>
        </table>
      </td></tr>""".format(ink=INK, fbody=FONT_BODY, fdisp=FONT_DISPLAY, text=e(text))


def provenance_line(items: List[Item], ai_enabled: bool) -> str:
    """State who wrote the analysis, counting what the model actually produced.

    Returns plain text; the HTML caller escapes it, and render/text.py reuses it as-is.

    Crediting Gemini because a run *intended* to use it would misattribute an issue in
    which every call failed and deterministic rules wrote the whole thing. The footer
    names the model that was called and reports the shortfall when there is one.
    """
    offline = (
        "Generated in offline mode: summaries, categories and impact scores come from "
        "deterministic rules rather than a language model."
    )
    if not ai_enabled:
        return offline

    by_model = sum(1 for i in items if i.enriched_by == "gemini")
    if not by_model:
        return offline

    line = (
        "Summaries, categories and impact scores generated by Google Gemini ({0})."
        .format(config.GEMINI_MODEL)
    )
    shortfall = len(items) - by_model
    if shortfall:
        line += (
            " {0} item{1} used the deterministic fallback after the model call failed."
            .format(shortfall, "" if shortfall == 1 else "s")
        )
    return line


# ------------------------------------------------------------------------------------
# Page
# ------------------------------------------------------------------------------------


def render(
    featured: List[Item],
    also_tracked: List[Item],
    editors_take: str,
    issue_number: int,
    issue_date: datetime,
    window_start: datetime,
    generated_at: Optional[datetime] = None,
    ai_enabled: bool = True,
) -> str:
    generated_at = generated_at or datetime.utcnow()
    also_tracked = also_tracked or []
    screened = len(featured) + len(also_tracked)

    grouped: Dict[str, List[Item]] = {c: [] for c in config.CATEGORIES}
    for item in featured:
        grouped.setdefault(item.category, []).append(item)

    for bucket in grouped.values():
        # Importance first, then recency — the reading order an analyst expects.
        bucket.sort(
            key=lambda i: (-i.importance, -(i.published.timestamp() if i.published else 0))
        )

    below: Dict[str, int] = {c: 0 for c in config.CATEGORIES}
    for item in also_tracked:
        below[item.category] = below.get(item.category, 0) + 1

    if screened:
        # Every category is rendered, empty or not. A section that disappears reads as a
        # missing feature; a section that reports what it checked reads as editorial.
        body = "".join(
            _section(c, grouped.get(c) or [], below.get(c, 0)) for c in config.CATEGORIES
        )
        body += _also_tracked(also_tracked)
    else:
        body = _quiet_week()

    date_range = "{0} &ndash; {1}".format(
        window_start.strftime("%d %b"), issue_date.strftime("%d %b %Y")
    )

    screening_line = "{0} item{1} screened &middot; {2} featured".format(
        screened, "" if screened == 1 else "s", len(featured)
    )

    provenance = e(provenance_line(featured + also_tracked, ai_enabled))

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{name} &middot; Issue {issue} &middot; {date}</title>
<style>
  /* Progressive enhancement only. Email clients strip this block; the inline styles
     above carry the full design on their own. */
  body {{ margin:0 !important; padding:0 !important; }}
  a {{ text-decoration:none; }}
  @media only screen and (max-width:640px) {{
    .wrap {{ width:100% !important; padding-left:18px !important; padding-right:18px !important; }}
    .masthead-title {{ font-size:30px !important; }}
  }}{dark}
</style>
</head>
<body class="canvas" style="margin:0;padding:0;background:{canvas};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">
  {name} Issue {issue} &mdash; {count} development{plural} across the MS market, {date}.
  {screening}.
</div>

<table role="presentation" class="canvas" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{canvas};padding:28px 12px;">
  <tr><td align="center">
    <table role="presentation" class="wrap paper" width="680" cellpadding="0" cellspacing="0" border="0"
           style="width:680px;max-width:680px;background:{paper};border-radius:5px;
                  box-shadow:0 1px 3px rgba(18,38,63,.10);padding:0 40px 40px;">

      <!-- Masthead -->
      <tr><td style="padding:38px 0 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr><td class="b-ink" style="border-bottom:3px solid {ink};padding-bottom:16px;">
            <div class="t-accent" style="font-family:{fbody};font-size:10.5px;font-weight:700;letter-spacing:.18em;
                 text-transform:uppercase;color:{accent};margin-bottom:8px;">
              Competitive Intelligence Briefing</div>
            <div class="masthead-title" style="font-family:{fdisp};font-size:37px;line-height:1.1;
                 font-weight:700;color:{ink};letter-spacing:-.5px;">{name}</div>
            <div class="t-muted" style="font-family:{fbody};font-size:13.5px;color:{muted};margin-top:8px;">{tagline}</div>
          </td></tr>
          <tr><td style="padding-top:12px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td class="t-muted" style="font-family:{fbody};font-size:12.5px;color:{muted};">
                  Issue {issue}<span style="color:{rule};"> &middot; </span>{range}</td>
                <td class="t-muted" style="font-family:{fbody};font-size:12.5px;color:{muted};text-align:right;">
                  {screening}</td>
              </tr>
            </table>
          </td></tr>
        </table>
      </td></tr>

      {editors}
      {body}

      <!-- Footer -->
      <tr><td style="padding:26px 0 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr><td class="b-rule" style="border-top:1px solid {rule};padding-top:18px;">
            <div class="t-muted" style="font-family:{fbody};font-size:11.5px;line-height:1.7;color:{muted};">
              <strong class="t-soft" style="color:{inksoft};">{name}</strong> is generated automatically each Monday
              from public sources: ClinicalTrials.gov API v2, three U.S. FDA RSS feeds
              (press releases, drugs, MedWatch), and curated news.<br>
              {provenance}<br>
              Compiled {generated} UTC. This briefing summarises public information for competitive
              intelligence purposes and is not medical, investment or regulatory advice.
            </div>
          </td></tr>
        </table>
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>""".format(
        name=e(config.NEWSLETTER_NAME), issue=issue_number, dark=DARK_CSS,
        date=e(issue_date.strftime("%d %B %Y")), canvas=CANVAS, paper=PAPER,
        ink=INK, fbody=FONT_BODY, fdisp=FONT_DISPLAY, accent=ACCENT, muted=MUTED,
        tagline=e(config.NEWSLETTER_TAGLINE), rule=RULE, range=date_range,
        count=len(featured), plural="" if len(featured) == 1 else "s",
        screening=screening_line,
        editors=_editors_take(editors_take) if editors_take else "",
        body=body, inksoft=INK_SOFT, provenance=provenance,
        generated=e(generated_at.strftime("%Y-%m-%d %H:%M")),
    )
