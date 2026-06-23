"""
Harmonic connector — Startup/Scaleup company counts for the TAM model (cols D/E).

Calls Harmonic's structured search directly (POST /search/companies) with a
generator-based filter_group — the same filters your saved searches use — and
reads the exact `count` from the response. Returns only URNs (size=1), so it's
cheap: we want the count, not the records.

VERIFIED live (2026-06-22) with a valid key:
  - endpoint:  POST https://api.harmonic.ai/search/companies
  - auth:      header  apikey: <key>
  - body:      {"query": {"pagination": {...}, "filter_group": {...},
                           "controlled_filter_group": {... generators ...}}}
  - returns:   {"count": <int>, "results": [urn...]}
  - filters constrain correctly (Aerospace 35,836 vs Defense 19,808).

Startup vs Scaleup split is just a funding band (per the TAM sheet):
  Startup  = venture-backed, raised <= ~EUR 30M   -> raised_max=30_000_000
  Scaleup  = raised > ~EUR 30M and < 5,000 staff   -> raised_min, headcount_max=4999

CURRENCY NOTE: Harmonic funding figures appear to be USD. EUR 30M ~= USD 32-33M.
Pass the threshold you want; confirm the currency with Harmonic before trusting
the Startup/Scaleup boundary precisely.

Token: env HARMONIC_API_KEY (Render) or local .env.harmonic file (desktop).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SEARCH_URL = "https://api.harmonic.ai/search/companies"


def _key() -> str | None:
    import os
    if os.environ.get("HARMONIC_API_KEY"):
        return os.environ["HARMONIC_API_KEY"].strip()
    f = Path(__file__).with_name(".env.harmonic")
    if f.exists():
        raw = f.read_text().strip()
        m = re.match(r"^HARMONIC_API_KEY=(.+)$", raw)
        return m.group(1).strip() if m else raw.strip()  # tolerate a bare key
    return None


def _generators(industry_tags, technology_tags, exclude_business_tags, region,
                raised_min, raised_max, headcount_min, headcount_max,
                founded_after) -> list[dict[str, Any]]:
    g: list[dict[str, Any]] = []
    if region:
        g.append({"generator_id": "search_v2_company_list_and_more_location",
                  "arguments": {"region": [region]}})
    if raised_min is not None or raised_max is not None:
        g.append({"generator_id": "search_v2_company_funding_total_range",
                  "arguments": {"range": [raised_min if raised_min is not None else 0,
                                          raised_max]}})  # null max = open-ended
    if industry_tags:
        g.append({"generator_id": "search_v2_company_sector_include_industry_tags",
                  "arguments": {"industry_tags": industry_tags}})
    if technology_tags:
        g.append({"generator_id": "search_v2_company_sector_include_technology_tags",
                  "arguments": {"technology_tags": technology_tags}})
    if exclude_business_tags:
        g.append({"generator_id": "search_v2_company_sector_exclude_business_tags",
                  "arguments": {"business_tags": exclude_business_tags}})
    if headcount_min is not None or headcount_max is not None:
        g.append({"generator_id": "search_v2_company_team_headcount_range",
                  "arguments": {"range": [headcount_min, headcount_max]}})
    if founded_after:
        g.append({"generator_id": "search_v2_company_funding_foundation_date",
                  "arguments": {"range_value": "custom", "custom_range": [founded_after, None]}})
    return g


def _count(generators: list[dict[str, Any]], key: str) -> tuple[int | None, Any]:
    query = {
        "pagination": {"start": 0, "page_size": 1},
        "filter_group": {"join_operator": "and", "filters": [],
                         "filter_groups": [], "filter_group_generators": []},
        "controlled_filter_group": {"join_operator": "and", "filters": [],
                                    "filter_groups": [], "filter_group_generators": generators},
    }
    req = urllib.request.Request(
        SEARCH_URL, data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json", "apikey": key},
        method="POST")
    try:
        payload = json.loads(urllib.request.urlopen(req, timeout=90).read())
        return payload.get("count"), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def company_count(industry_tags: list[str] | None = None,
                  technology_tags: list[str] | None = None,
                  exclude_business_tags: list[str] | None = None,
                  region: str = "EUROPE",
                  raised_min: int | None = None,
                  raised_max: int | None = None,
                  headcount_min: int | None = None,
                  headcount_max: int | None = None,
                  founded_after: str | None = None) -> dict[str, Any]:
    """Exact count of Harmonic companies matching the given structured filters."""
    key = _key()
    if not key:
        return {"ok": False, "error": "no_key",
                "detail": "Set HARMONIC_API_KEY env var or create .env.harmonic."}
    gens = _generators(industry_tags or [], technology_tags or [],
                       exclude_business_tags or [], region, raised_min, raised_max,
                       headcount_min, headcount_max, founded_after)
    cnt, err = _count(gens, key)
    if err:
        return {"ok": False, "error": "request_failed", "detail": err, "generators": gens}
    return {"ok": True, "count": cnt, "generators": gens}


def startup_scaleup_split(industry_tags: list[str] | None = None,
                          technology_tags: list[str] | None = None,
                          region: str = "EUROPE",
                          split_at: int = 30_000_000,
                          scaleup_headcount_max: int = 4999,
                          exclude_business_tags: list[str] | None = None) -> dict[str, Any]:
    """
    Split one segment into Startups (raised <= split_at) and Scaleups
    (raised > split_at, headcount < 5,000), matching the TAM sheet's definitions.
    """
    common = dict(industry_tags=industry_tags, technology_tags=technology_tags,
                  region=region, exclude_business_tags=exclude_business_tags)
    startups = company_count(raised_max=split_at, **common)
    scaleups = company_count(raised_min=split_at, headcount_max=scaleup_headcount_max, **common)
    return {
        "industry_tags": industry_tags, "technology_tags": technology_tags,
        "region": region, "split_at": split_at,
        "startups": startups.get("count") if startups.get("ok") else startups,
        "scaleups": scaleups.get("count") if scaleups.get("ok") else scaleups,
    }
