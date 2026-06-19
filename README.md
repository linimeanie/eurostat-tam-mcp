# Eurostat TAM MCP server

An MCP server that pulls **enterprise counts by NACE Rev.2 activity and size
class** from Eurostat (dataset [`sbs_sc_ovw`](https://ec.europa.eu/eurostat/databrowser/view/sbs_sc_ovw))
and buckets them to match the DTM TAM model's columns:

| Sheet column | What this server returns |
|---|---|
| **SMEs** | `sme_count` — enterprises with 1–249 employees (optionally excl. micro 0–9) |
| **Corp: Mid-cap (250–4,999)** | `corp_250plus_count` — **all** 250+ firms* |

\* Eurostat's top size band is **250+** with no 5,000 split, so use ORBIS/ONS to
separate Mid-cap from Large downstream.

### What this does NOT cover (by design — the model sources these elsewhere)
- **Startups / Scaleups** → Dealroom / Harmonic (venture-backed, € raised)
- **Corp Large (5,000+)** → ORBIS / UK ONS
- **United Kingdom** → ORBIS / ONS (UK left Eurostat after Brexit)

Default geography is **EU27 + Norway + Switzerland**, summed (EU27 via Eurostat's
pre-aggregated `EU27_2020` geo, so one call covers all 27).

## Tools

### `get_enterprise_counts(nace_code, geo?, year?, exclude_micro?)`
One NACE code → SME and 250+ counts. Example: `get_enterprise_counts("C27")`.

### `get_segment_counts(nace_codes, geo?, year?, exclude_micro?)`
A list of codes for one value-chain segment → per-code results **plus a summed
segment total**. Example for "Equipment Suppliers & OEMs":
`get_segment_counts(["C27", "C28"])`.

### `fill_tam_sheet(input_path, output_path?, dry_run=True, geo?, year?, exclude_micro?)`
Reads NACE codes straight from the **Company Counts by Segment** sheet, pulls
counts for every segment, and writes the **SMEs (col F)** and **Corp Mid-cap
250+ (col G)** cells. Run with `dry_run=True` first to audit the per-row parse,
then `dry_run=False` to save (defaults to `<input>_FILLED.xlsx` — never
overwrites the original unless you pass the same path).

Only yellow input cells are written; blue subtotals, the grey public-sector row,
and demand-side rows are left untouched. Subtotal/Low/High formulas and cell
formatting are preserved.

## A critical data caveat: Eurostat has no 4-digit NACE detail

`sbs_sc_ovw` only goes down to **division (C27)** and **group (C279)** — there is
**no class-level data** (C26.30, D35.11, …). The CMO's sheet is mostly class
codes, so every code is **resolved down** to the nearest level that exists:

> class → group → division → section

Each row reports which codes it used and a `coarsened` list flagging where it
fell back to a whole division (e.g. `J63` = all information services). Those
numbers are upper bounds for the intended class — refine with ORBIS if needed.

NACE sub-classes use the dotted form in the sheet, e.g. `"C26.30"`, `"D35.11"`.

## Setup

```bash
cd /Users/lini/Documents/Claude/eurostat-mcp
uv venv --python 3.12
uv pip install "mcp[cli]"
```

Test the live logic without the MCP layer:

```bash
uv run python -c "import server, json; print(json.dumps(server.get_enterprise_counts('C27'), indent=2))"
```

## Register it with Claude Code

Copy the block in [`.mcp.json`](.mcp.json) into your Claude Code MCP config, or
from any project run:

```bash
claude mcp add eurostat-tam -- uv run --directory /Users/lini/Documents/Claude/eurostat-mcp server.py
```

Then in a Claude Code session you can just ask:
> "Use eurostat-tam to pull C27+C28 for the Equipment Suppliers segment, exclude micro."

## Notes & caveats
- Summing NACE codes can **double-count** a firm that reports under multiple
  activities. De-dup per the sheet's classification waterfall before trusting totals.
- Subtract venture-backed firms (counted as startups/scaleups) from the SME/Corp
  pulls so each company lands in exactly one bucket.
- Latest year auto-resolves (currently 2024) unless you pass `year`.
