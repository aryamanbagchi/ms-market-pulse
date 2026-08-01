"""Central configuration for the MS Market Pulse pipeline.

Every tunable lives here so the rest of the codebase reads as logic, not constants.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------

NEWSLETTER_NAME = "MS Market Pulse"
NEWSLETTER_TAGLINE = "Weekly competitive intelligence for the Multiple Sclerosis market"

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
SAMPLE_DIR = DATA_DIR / "sample"
NEWSLETTERS_DIR = ROOT / "newsletters"
MANIFEST_PATH = NEWSLETTERS_DIR / "manifest.json"
ARCHIVE_PATH = ROOT / "index.html"

# --------------------------------------------------------------------------------------
# Fetch window and network behaviour
# --------------------------------------------------------------------------------------

LOOKBACK_DAYS = 7
HTTP_TIMEOUT = 30
HTTP_RETRIES = 2
HTTP_BACKOFF = 1.5
USER_AGENT = "MS-Market-Pulse/1.0 (competitive intelligence newsletter; +https://github.com)"

# --------------------------------------------------------------------------------------
# Source endpoints
# --------------------------------------------------------------------------------------

CLINICALTRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
CLINICALTRIALS_PAGE_SIZE = 100
CLINICALTRIALS_MAX_PAGES = 5

# The URL in most docs is the RSS *index* page, not a feed. These are the real feeds.
# `fda-newsroom` is a 404 and is deliberately excluded.
FDA_FEEDS = {
    "press-releases": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml",
    "drugs": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml",
    "medwatch": "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medwatch/rss.xml",
}

GOOGLE_NEWS_URL = "https://news.google.com/rss/search?q=%22multiple+sclerosis%22+drug"

# --------------------------------------------------------------------------------------
# MS relevance keywords
#
# Used to filter the general-purpose RSS feeds (FDA, Google News) down to MS-relevant
# items. Matched case-insensitively against title + description. The bare token "MS" is
# handled separately in fetchers.base (uppercase, word-boundary only) because lowercase
# "ms" appears inside far too many unrelated words.
# --------------------------------------------------------------------------------------

MS_CONDITION_TERMS = [
    "multiple sclerosis",
    "relapsing-remitting",
    "relapsing remitting",
    "primary progressive ms",
    "secondary progressive ms",
    "clinically isolated syndrome",
    "neuromyelitis optica",
    "demyelinating",
    "remyelination",
    "rrms",
    "ppms",
    "spms",
]

# Marketed MS therapies, brand and generic.
MS_DRUG_TERMS = [
    "ocrevus", "ocrelizumab",
    "kesimpta", "ofatumumab",
    "briumvi", "ublituximab",
    "tysabri", "natalizumab", "tyruko",
    "lemtrada", "alemtuzumab",
    "mavenclad", "cladribine",
    "zeposia", "ozanimod",
    "mayzent", "siponimod",
    "gilenya", "fingolimod", "tascenso",
    "ponvory", "ponesimod",
    "tecfidera", "dimethyl fumarate",
    "vumerity", "diroximel",
    "bafiertam", "monomethyl fumarate",
    "aubagio", "teriflunomide",
    "copaxone", "glatiramer", "glatopa",
    "plegridy", "avonex", "rebif", "betaseron", "extavia", "interferon beta",
    "ampyra", "dalfampridine",
    "novantrone", "mitoxantrone",
]

# Live pipeline assets worth tracking ahead of approval.
MS_PIPELINE_TERMS = [
    "tolebrutinib",
    "fenebrutinib",
    "remibrutinib",
    "orelabrutinib",
    "frexalimab",
    "evobrutinib",
    "ibudilast",
    "clemastine",
    "bexarotene",
    "masitinib",
    "foralumab",
    "obexelimab",
]

MS_KEYWORDS = MS_CONDITION_TERMS + MS_DRUG_TERMS + MS_PIPELINE_TERMS

# --------------------------------------------------------------------------------------
# Categories and scoring
# --------------------------------------------------------------------------------------

CATEGORY_REGULATORY = "Regulatory"
CATEGORY_CLINICAL = "Clinical"
CATEGORY_COMMERCIAL = "Commercial/Corporate"

CATEGORIES = [CATEGORY_REGULATORY, CATEGORY_CLINICAL, CATEGORY_COMMERCIAL]

MIN_IMPORTANCE = 1
MAX_IMPORTANCE = 5
DEFAULT_IMPORTANCE = 3

# Source authority, used to pick a winner when two sources report the same story.
SOURCE_AUTHORITY = {
    "clinicaltrials": 3,
    "fda": 2,
    "news": 1,
}

# Fuzzy title-match threshold for cross-source deduplication.
DEDUPE_THRESHOLD = 0.85

# --------------------------------------------------------------------------------------
# AI layer (Google Gemini, called over plain REST)
#
# The google-genai SDK >= 2.0 requires Python >= 3.10, which would force a version split
# between local (3.9) and CI (3.11). Calling the REST endpoint directly keeps one code
# path and holds total dependencies to `requests` + `feedparser`.
# --------------------------------------------------------------------------------------

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

# Pinned to an explicit version rather than a floating alias such as
# `gemini-flash-latest`, so a model change is a deliberate commit rather than a silent
# shift in output quality between two runs of the same code.
#
# Note: the Gemini 2.x line (gemini-2.5-flash, gemini-2.0-flash, and their -lite
# variants) still appears in ListModels but returns HTTP 404 "no longer available to
# new users" for API keys issued recently. Do not treat presence in ListModels as
# proof of access — verify with an actual generateContent call.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

GEMINI_TIMEOUT = 90
GEMINI_MAX_WORKERS = 4
GEMINI_TEMPERATURE = 0.3

# maxOutputTokens must cover BOTH thinking tokens and the visible answer on Gemini 3.x
# reasoning models. At 1024 the model spent ~800 tokens thinking and got truncated
# mid-JSON, which surfaced as "Unterminated string" for half the items. The visible
# answer here is only ~150 tokens; the headroom is entirely for thinking.
GEMINI_MAX_OUTPUT_TOKENS = 8192
GEMINI_EDITOR_MAX_OUTPUT_TOKENS = 8192

# Summarising a supplied source text is extraction, not deep reasoning, so thinking is
# capped. Cuts latency and token spend with no measurable quality loss on this task.
# Set to None to let the model decide. Gemini 2.x used `thinkingBudget` instead and will
# reject this field; the client drops it automatically if the API returns 400.
GEMINI_THINKING_LEVEL = "low"

# Cap items sent for enrichment. Bounds cost and keeps the newsletter readable.
MAX_ITEMS_PER_ISSUE = 40

# How much source text to hand the model per item.
MAX_SOURCE_CHARS = 2500
