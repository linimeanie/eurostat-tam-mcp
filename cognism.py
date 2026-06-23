"""
Cognism connector — company counts by NAICS x employee size band x country.

Fills the cells Eurostat/Nomis can't: Corp Large (5,000+), the precise
250-4,999 Mid-cap split, and UK corporates.

STATUS (2026-06-22):
  VERIFIED against the live API (with a valid token):
    - endpoint:  POST https://app.cognism.com/api/search/account/search
    - auth:      Authorization: Bearer <token>
    - WAF:       Cloudflare blocks non-browser user-agents (error 1010) -> we
                 send a browser UA.
    - the token authenticates, but the account currently returns
      403 "You have no entitlement set. Please contact CSM."  <-- the only blocker
  PROVISIONAL (needs one real response to confirm, marked below):
    - the request-body filter field names (_build_query)
    - the response field that holds the total match count (_extract_count)
  Both are isolated in single functions so they're a one-line fix once the
  entitlement is enabled and we can see a real payload.

Token resolution: env var COGNISM_API_TOKEN first (used on Render), else the
local gitignored .env.cognism file (used on desktop).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SEARCH_URL = "https://app.cognism.com/api/search/account/search"
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _token() -> str | None:
    tok = os.environ.get("COGNISM_API_TOKEN")
    if tok:
        return tok.strip()
    envfile = Path(__file__).with_name(".env.cognism")
    if envfile.exists():
        m = re.match(r"^COGNISM_API_TOKEN=(.+)$", envfile.read_text().strip())
        if m:
            return m.group(1).strip()
    return None


# --------------------------------------------------------------------------- #
# PROVISIONAL: request body. Confirm field names against the first live 200.
# --------------------------------------------------------------------------- #
def _build_query(naics: list[str], countries: list[str],
                 emp_min: int | None, emp_max: int | None, size: int) -> dict[str, Any]:
    employees: dict[str, int] = {}
    if emp_min is not None:
        employees["min"] = emp_min
    if emp_max is not None:
        employees["max"] = emp_max
    filters: dict[str, Any] = {}
    if naics:
        filters["naics"] = naics
    if countries:
        filters["countries"] = countries
    if employees:
        filters["employeeCount"] = employees
    # Cognism expects `size` as a pagination object {from, size}, not a bare int.
    return {"filters": filters, "size": {"from": 0, "size": size}}


# --------------------------------------------------------------------------- #
# PROVISIONAL: pull the total match count out of the response. We look across
# the field names these APIs commonly use, so it likely works as-is; confirm.
# --------------------------------------------------------------------------- #
def _extract_count(payload: dict[str, Any]) -> int | None:
    for key in ("totalResults", "totalCount", "total", "count", "hits", "numFound"):
        v = payload.get(key)
        if isinstance(v, int):
            return v
        if isinstance(v, dict) and isinstance(v.get("value"), int):
            return v["value"]
    return None


def _post(body: dict[str, Any], token: str) -> tuple[int, dict[str, Any] | str]:
    req = urllib.request.Request(
        SEARCH_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": BROWSER_UA,
        },
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def get_company_count(naics: list[str], countries: list[str],
                      emp_min: int | None = None,
                      emp_max: int | None = None) -> dict[str, Any]:
    """Return the count of companies matching NAICS x country x employee range."""
    token = _token()
    if not token:
        return {"ok": False, "error": "no_token",
                "detail": "Set COGNISM_API_TOKEN env var or create .env.cognism."}

    body = _build_query(naics, countries, emp_min, emp_max, size=1)
    status, payload = _post(body, token)

    if "entitlement" in str(payload).lower():
        return {"ok": False, "error": "entitlement_scope", "request_sent": body,
                "detail": ("Cognism rejected this search on entitlement grounds. If it says "
                           "'not supported by subscribed entitlement', your plan likely has "
                           "CONTACT search but not ACCOUNT (company) search — ask your CSM to "
                           "enable Account Search."), "raw": payload}
    if status != 200:
        return {"ok": False, "error": f"http_{status}", "request_sent": body,
                "detail": payload}

    count = _extract_count(payload) if isinstance(payload, dict) else None
    return {
        "ok": True,
        "count": count,
        "naics": naics, "countries": countries,
        "emp_min": emp_min, "emp_max": emp_max,
        "request_sent": body,
        # Surfaced so we can confirm the provisional schema on the first real call:
        "response_keys": list(payload.keys()) if isinstance(payload, dict) else None,
        "count_unresolved_note": None if count is not None else
            "Got 200 but couldn't find a count field — check _extract_count against response_keys.",
    }
