"""
Eurostat MCP server for DTM's TAM model.

Exposes tools that pull enterprise counts from Eurostat's Structural Business
Statistics (dataset `sbs_sc_ovw`) by NACE Rev.2 activity x employee size class,
and bucket them into the columns the TAM sheet uses:

    SME            = enterprises with 1-249 persons employed
    Corp (250+)    = enterprises with 250 or more persons employed

What Eurostat does NOT give you (handled elsewhere in the model):
    - Startups / Scaleups  -> Dealroom / Harmonic (venture-backed, EUR raised)
    - Corp Large (5,000+)  -> ORBIS / ONS  (Eurostat's top band is 250+, no 5,000 split)
    - United Kingdom       -> ORBIS / UK ONS (UK left Eurostat after Brexit)

Geography: default scope is EU27 + Norway + Switzerland, summed. EU27 is pulled
via Eurostat's pre-aggregated `EU27_2020` geo, so a single call covers all 27.

Transport: stdio (the format Claude Code launches MCP servers with).
Dependencies: just `mcp`. HTTP is done with the standard library.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

API_BASE = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/sbs_sc_ovw"
)
DATASET = "sbs_sc_ovw"

# Default geography: EU27 (pre-aggregated) + Norway + Switzerland.
DEFAULT_GEO = ["EU27_2020", "NO", "CH"]

# Size-class codes in sbs_sc_ovw and how they map to the sheet's buckets.
SME_BANDS = ["0-9", "10-19", "20-49", "50-249"]   # 1-249 persons employed
SME_BANDS_NO_MICRO = ["10-19", "20-49", "50-249"]  # excludes micro (0-9)
CORP_BAND = "GE250"                                 # 250 or more

# When served remotely over HTTP (the web connector), the host has no access to
# the user's local files, so the sheet-filling tool is not exposed there.
import os as _os
_IS_HTTP = _os.environ.get("MCP_TRANSPORT", "stdio").lower() in ("http", "streamable-http")

if _IS_HTTP:
    # The SDK's DNS-rebinding protection only allows Host: localhost by default,
    # so any real domain (e.g. *.onrender.com) gets HTTP 421. That protection
    # guards localhost-bound servers from malicious browsers; it's irrelevant
    # for a public server that returns only public Eurostat data, so disable it.
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP(
        "eurostat-tam",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
else:
    mcp = FastMCP("eurostat-tam")


# --------------------------------------------------------------------------- #
# HTTP + JSON-stat decoding
# --------------------------------------------------------------------------- #
def _fetch(params: list[tuple[str, str]]) -> dict[str, Any]:
    """Call the Eurostat dissemination API and return parsed JSON-stat."""
    query = urllib.parse.urlencode(
        [("format", "JSON"), ("lang", "EN")] + params, doseq=True
    )
    url = f"{API_BASE}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "dtm-eurostat-mcp/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _decode(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Turn a JSON-stat response into a flat list of observation rows.

    JSON-stat stores values in a sparse dict keyed by a single flattened index.
    We invert that index back into one category key per dimension using the
    mixed-radix `size` vector, then attach human labels.
    """
    ids: list[str] = data["id"]
    sizes: list[int] = data["size"]
    dims = data["dimension"]

    # For each dimension: position -> (code, label)
    pos_lookup: dict[str, dict[int, tuple[str, str]]] = {}
    for dim in ids:
        cat = dims[dim]["category"]
        labels = cat.get("label", {})
        inv = {idx: code for code, idx in cat["index"].items()}
        pos_lookup[dim] = {
            idx: (code, labels.get(code, code)) for idx, code in inv.items()
        }

    rows: list[dict[str, Any]] = []
    for flat_str, value in data["value"].items():
        flat = int(flat_str)
        row: dict[str, Any] = {"value": value}
        # Decode mixed-radix index (last dimension varies fastest).
        for dim, size in zip(reversed(ids), reversed(sizes)):
            pos = flat % size
            flat //= size
            code, label = pos_lookup[dim][pos]
            row[dim] = code
            row[f"{dim}_label"] = label
        rows.append(row)
    return rows


def _latest_year(rows: list[dict[str, Any]]) -> str | None:
    years = {r["time"] for r in rows if "time" in r}
    return max(years) if years else None


# --------------------------------------------------------------------------- #
# Core bucketing
# --------------------------------------------------------------------------- #
def _counts_for_nace(
    nace_code: str,
    geo: list[str],
    year: str | None,
    exclude_micro: bool,
) -> dict[str, Any]:
    """Pull ENT_NR for one NACE code and bucket into SME / Corp(250+)."""
    sme_bands = SME_BANDS_NO_MICRO if exclude_micro else SME_BANDS
    wanted_sizes = sme_bands + [CORP_BAND]

    params: list[tuple[str, str]] = [("indic_sbs", "ENT_NR"), ("nace_r2", nace_code)]
    for g in geo:
        params.append(("geo", g))
    for s in wanted_sizes:
        params.append(("size_emp", s))
    if year:
        params.append(("time", year))

    try:
        data = _fetch(params)
    except urllib.error.HTTPError as e:
        return {"nace": nace_code, "error": f"HTTP {e.code}: {e.reason}", "available": False}
    except Exception as e:  # noqa: BLE001
        return {"nace": nace_code, "error": f"{type(e).__name__}: {e}", "available": False}

    rows = _decode(data)
    if not rows:
        return {"nace": nace_code, "available": False, "note": "no data returned"}

    # If no year requested, keep only the latest year present.
    resolved_year = year or _latest_year(rows)
    rows = [r for r in rows if r.get("time") == resolved_year]

    sme = sum(r["value"] for r in rows if r["size_emp"] in sme_bands)
    corp = sum(r["value"] for r in rows if r["size_emp"] == CORP_BAND)

    by_band = {}
    for r in rows:
        by_band.setdefault(r["size_emp"], 0)
        by_band[r["size_emp"]] += r["value"]

    nace_label = next((r.get("nace_r2_label") for r in rows), nace_code)

    return {
        "nace": nace_code,
        "nace_label": nace_label,
        "year": resolved_year,
        "geo": geo,
        "exclude_micro": exclude_micro,
        "available": True,
        "sme_count": sme,                 # -> sheet column "SMEs"
        "corp_250plus_count": corp,       # -> sheet column "Corp: Mid-cap (250-4,999)" *see note
        "by_size_band": by_band,
        "note": (
            "corp_250plus_count is ALL enterprises with 250+ employees. Eurostat "
            "has no 5,000 split, so split Mid-cap vs Large downstream via ORBIS/ONS. "
            "UK and Startups/Scaleups are not in this dataset."
        ),
    }


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_enterprise_counts(
    nace_code: str,
    geo: list[str] | None = None,
    year: str | None = None,
    exclude_micro: bool = False,
) -> dict[str, Any]:
    """
    Enterprise counts for one NACE Rev.2 code, bucketed into SME (1-249) and
    Corp (250+), summed across the given geographies.

    Args:
        nace_code: NACE Rev.2 code, e.g. "C27", "C28", "D35", "C26.30" (use the
            Eurostat dotted form for sub-classes, e.g. "C2630" is NOT valid; use "C26.30").
        geo: list of Eurostat geo codes. Defaults to EU27 + Norway + Switzerland
            (["EU27_2020", "NO", "CH"]). Pass individual countries (e.g. ["DE","FR"])
            to break it down. NOTE: UK ("UK"/"GB") is not available post-Brexit.
        year: a single year as a string, e.g. "2022". If omitted, the latest
            available year is used automatically.
        exclude_micro: if True, the SME bucket excludes micro firms (0-9 employees),
            i.e. SME = 10-249. The TAM sheet notes this as an option.

    Returns a dict with sme_count, corp_250plus_count, the per-band breakdown,
    the resolved year, and caveats.
    """
    return _counts_for_nace(nace_code, geo or DEFAULT_GEO, year, exclude_micro)


@mcp.tool()
def get_segment_counts(
    nace_codes: list[str],
    geo: list[str] | None = None,
    year: str | None = None,
    exclude_micro: bool = False,
) -> dict[str, Any]:
    """
    Enterprise counts for a list of NACE codes that make up one value-chain
    segment, returned per code AND summed for the whole segment. This maps
    directly onto a row of the "Company Counts by Segment" sheet.

    Args:
        nace_codes: the NACE Rev.2 codes for the segment, e.g.
            ["C27", "C28"] for "Equipment Suppliers & OEMs".
        geo: see get_enterprise_counts. Defaults to EU27 + NO + CH.
        year: single year string, or omit for latest.
        exclude_micro: exclude micro firms (0-9) from the SME bucket.

    Returns per-code results plus a segment_total with combined SME and 250+ counts.

    CAUTION: summing NACE codes can double-count a company that reports under
    more than one activity, and the same firm may also be counted as a
    startup/scaleup elsewhere. De-dup per the sheet's waterfall before trusting totals.
    """
    geo = geo or DEFAULT_GEO
    per_code = [
        _counts_for_nace(code, geo, year, exclude_micro) for code in nace_codes
    ]
    ok = [r for r in per_code if r.get("available")]
    total = {
        "sme_count": sum(r["sme_count"] for r in ok),
        "corp_250plus_count": sum(r["corp_250plus_count"] for r in ok),
        "codes_used": [r["nace"] for r in ok],
        "codes_missing": [r["nace"] for r in per_code if not r.get("available")],
    }
    return {
        "geo": geo,
        "year": year or (ok[0]["year"] if ok else None),
        "exclude_micro": exclude_micro,
        "per_code": per_code,
        "segment_total": total,
        "caveat": (
            "Summed across NACE codes; possible double-counting of multi-activity "
            "firms. Excludes UK, Startups/Scaleups, and the 5,000+ split."
        ),
    }


def fill_tam_sheet(
    input_path: str,
    output_path: str | None = None,
    dry_run: bool = True,
    geo: list[str] | None = None,
    year: str | None = None,
    exclude_micro: bool = False,
) -> dict[str, Any]:
    """
    Read NACE codes out of the TAM workbook's "Company Counts by Segment" sheet,
    pull Eurostat counts for every segment, and write the SMEs (col F) and
    Corp Mid-cap 250+ (col G) cells.

    Only yellow input cells on segment rows are written; blue subtotals, the
    grey public-sector row, and demand-side rows are left untouched. Class-level
    NACE codes (which Eurostat lacks) are resolved down to the nearest available
    group/division and flagged per row under "coarsened".

    Args:
        input_path: path to the .xlsx (e.g. the TAM_Model_with_NAICS.xlsx).
        output_path: where to save. Defaults to "<input>_FILLED.xlsx". Pass the
            same path as input ONLY if you intend to overwrite the original.
        dry_run: if True (default), nothing is written — returns the full plan
            (per-row resolved codes, levels, and the SME/250+ it WOULD write) so
            you can audit before committing. Set False to actually save.
        geo: Eurostat geos, default EU27 + Norway + Switzerland.
        year: single year, or omit for latest per code.
        exclude_micro: exclude micro firms (0-9) from the SME bucket.

    Returns the plan (dry_run) or a write summary plus the plan.
    """
    import fill_sheet  # lazy import avoids server <-> fill_sheet cycle

    if dry_run:
        return {"dry_run": True, **fill_sheet.compute_plan(
            input_path, geo, year, exclude_micro)}
    out = output_path or input_path.rsplit(".", 1)[0] + "_FILLED.xlsx"
    return {"dry_run": False, **fill_sheet.fill_workbook(
        input_path, out, geo, year, exclude_micro)}


# Local-file tool: register only for stdio (desktop/CLI), not the web connector.
if not _IS_HTTP:
    fill_tam_sheet = mcp.tool()(fill_tam_sheet)


if __name__ == "__main__":
    # Desktop/CLI launches us over stdio. For the web (remote) connector, set
    #   MCP_TRANSPORT=http   -> serves Streamable HTTP at http://HOST:PORT/mcp
    # HOST/PORT default to 0.0.0.0:8000 (override with MCP_HOST / MCP_PORT, and
    # most cloud hosts inject PORT automatically).
    import os

    if os.environ.get("MCP_TRANSPORT", "stdio").lower() in ("http", "streamable-http"):
        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8000")))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
