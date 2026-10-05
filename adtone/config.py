"""Constants for fashion-adtone.

Anything here that changes a measurement (rubric, model, embedder, analysis
parameters) is part of an instrument's identity and is written into every output row.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ADS_DIR = DATA / "ads"
MEDIA_DIR = DATA / "media"
OBS_DIR = DATA / "obs"
VEC_DIR = DATA / "vectors"
STATE_DIR = DATA / "state"
PROV_DIR = DATA / "provenance"
RESULTS_DIR = DATA / "results"
CANDIDATES_FILE = DATA / "registry" / "page_candidates.json"
HUMAN_DIR = DATA / "human_check"
REGISTRY_FILE = ROOT / "registry" / "houses.yml"
PREREG_FILE = ROOT / "PREREGISTRATION.md"
RUBRIC_DIR = ROOT / "rubric"

# Meta Graph API. The version moves quarterly; override with GRAPH_API_VERSION.
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v26.0")
GRAPH_BASE = "https://graph.facebook.com"

# EU27 plus GB. Meta keeps every ad delivered to these in the Ad Library for a year
# after its last impression, with reach and targeting attached (DSA Article 39).
EU_UK_COUNTRIES = [
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
    "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK", "GB",
]

# Fields documented for every ad and for EU/UK ads. If the API rejects the optional
# set (a field renamed in a new version), collection falls back to the core set and
# says so in provenance rather than failing the run.
CORE_FIELDS = [
    "id", "ad_creation_time", "ad_delivery_start_time", "ad_delivery_stop_time",
    "ad_creative_bodies", "ad_creative_link_titles", "ad_creative_link_captions",
    "ad_creative_link_descriptions", "page_id", "page_name",
]
OPTIONAL_FIELDS = [
    "publisher_platforms", "languages", "eu_total_reach", "target_ages", "target_gender",
    "target_locations", "age_country_gender_reach_breakdown", "beneficiary_payers",
]
# ad_snapshot_url is deliberately never requested or stored: the URL the API returns
# can carry the caller's access token. The render URL is rebuilt from the ad id at
# processing time and lives only in memory.
SNAPSHOT_URL = "https://www.facebook.com/ads/archive/render_ad/?id={ad_id}&access_token={token}"
LIBRARY_URL = "https://www.facebook.com/ads/library/?id={ad_id}"

BACKFILL_LOOKBACK_DAYS = 400   # longer than the one-year retention, so nothing retained is missed
INCREMENTAL_LOOKBACK_DAYS = 35  # overlap, so a skipped weekly run loses nothing

# Instruments.
RUBRIC_VERSION = "tone-v1"
CLAUDE_MODEL = os.environ.get("ADTONE_CLAUDE_MODEL", "claude-sonnet-5-5")
EMBED_MODEL = "ViT-B-32"
EMBED_PRETRAINED = "laion2b_s34b_b79k"
EMBED_TAG = "openclip-vitb32-laion2b"

# Media resolution.
MIN_IMAGE_SIDE = 320       # page avatars and icons fall below this; creatives do not
MAX_IMAGE_BYTES = 15_000_000
MAX_CANDIDATES = 8
KEEP_IMAGES_PER_AD = 5     # carousel cards beyond five add cost, not information
MODEL_MAX_EDGE = 1568

# Analysis (frozen with PREREGISTRATION.md).
PHASH_MAX_DIST = 6           # of 64 bits: near-duplicate crops and resizes of one creative
BLOCK_RULE = "hybrid"        # Amendment 1: gap blocks, long runs cut into calendar months; "gap21" is the sensitivity rule
MAX_BLOCK_SPAN_DAYS = 35     # hybrid rule: a gap block spanning longer than this is cut at month boundaries
BLOCK_GAP_DAYS = 21          # gap rule: a gap this long between concepts starts a new campaign block
POST_LAG_DAYS = 90           # post-debut window starts this long after the debut show
MIN_BLOCKS_SIDE = 3
MIN_CONCEPTS_SIDE = 8
PRIMARY_TYPES = ("brand_image",)
MOVER_RIDGE = 0.1           # ridge penalty for the H2 specificity regression on unit-normalised references
SENSITIVITY_TYPES = ("brand_image", "product_on_model")
EXCLUDED_CATEGORIES = ("fragrance_beauty", "jewellery_watches", "eyewear", "home")
N_PERM = int(os.environ.get("ADTONE_N_PERM", "1999"))


def run_id() -> str:
    """GitHub run id in Actions, a UTC timestamp locally."""
    rid = os.environ.get("GITHUB_RUN_ID")
    if rid:
        return f"gh{rid}"
    from datetime import datetime, timezone
    return "local" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
