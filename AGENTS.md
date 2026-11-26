# AGENTS.md

Guidance for coding agents working in this repository.

## What This Project Does

BOVA11 Options Analysis — a Python system that reads daily B3 (Brazilian stock exchange) options data from CSV files and generates the interactive HTML dashboard published at **https://leocarneiroo.github.io/bova11-dashboard/**.

Twelve modules run in sequence from a single runner and produce 13 pages (module 1 writes two). Every page supports a dark/light toggle (button fixed top-right, persisted in `localStorage('bova11-theme')`).

## Repository Layout

```
bova11_runner.py            entry point: date discovery, spot prompts, module orchestration, publish
scripts/
  bova11_shared.py          parsers, Black-Scholes greeks, GEX/DEX/VEX/TEX components, date tags
  bova11_expiry_family.py   expiration-family filtering (monthly vs W1..W5)
  bova11_prune_site.py      prunes the published site before pushing gh-pages
  bova11_auto.py …          one file per module (12 modules)
tests/                      unittest suite
data/                       B3 CSV files (versioned)
history/                    cumulative JSON state — never delete
docs/                       calculation audits and notes
output/                     git worktree of the gh-pages branch (generated HTML)
```

## Running the System

**Full run (recommended):**

```bash
python3 bova11_runner.py
```

The runner auto-detects D-1 and D from the CSV filenames in `data/`, then prompts for:

1. Spot price D-1
2. Spot price D
3. Strike range for Skew History (default **140–180**, Enter to accept)

It persists the manual spots to `history/bova11_spot_history.json`, runs the 12 modules, builds `output/index.html`, and offers to publish.

Every module also receives all inputs via CLI args (`--data-dir`, `--output`, `--ref-date`, `--ref-tag`, `--spot`, …) and runs standalone:

```bash
python3 scripts/bova11_max_pain.py --help
```

## The 12 Modules

| # | Script | Output | Inputs |
|---|--------|--------|--------|
| 1 | `bova11_auto.py` | `bova11_rankings_pro_<D1>_vs_<D>.html`, `bova11_gex_pro_<D1>_vs_<D>.html` | stdin (spot D) |
| 2 | `bova11_insights_auto.py` | `bova11_insights_<D1>_vs_<D>.html` | stdin (spot D) |
| 3 | `bova11_skew_history.py` | `bova11_skew_history.html` + `history/bova11_skew_history.json` | stdin (strike range, spot) |
| 4 | `bova11_trade_score_james.py` | `bova11_trade_score_<D1>_vs_<D>.html` | `--ref-date`, `--ref-tag`, `--spot-d`, `--spot-d1` |
| 5 | `bova11_quant_institucional.py` | `bova11_quant_institucional.html` | `--spot`, `--spot-history-file` |
| 6 | `bova11_tex_dex_vex.py` | `bova11_tex_dex_vex_<D1>_vs_<D>.html` | `--data-dir`, `--output`, `--ref-date`, `--ref-tag` |
| 7 | `bova11_oi_stats_light.py` | `bova11_oi_stats_light.html` | `--data-dir`, `--output`, `--ref-date`, `--ref-tag` |
| 8 | `bova11_max_pain.py` | `bova11_max_pain.html` | same as 7 |
| 9 | `bova11_gravity.py` | `bova11_gravity_map.html` | same as 7 |
| 10 | `bova11_market_gamma.py` | `bova11_market_gamma.html` | same as 7 + `--spot`, `--spot-history-file` |
| 11 | `bova11_diagnostico_4d.py` | `bova11_diagnostico_4d.html` | `--ref-date`, `--ref-tag`, `--spot-d`, `--spot-d1` |
| 12 | `bova11_chain_oi_changes.py` | `bova11_chain_oi_changes.html` | `--data-dir`, `--output`, `--ref-date`, `--ref-tag` |

Modules 6–12 use `argparse` and need no stdin.

**Dependencies:** Python 3 stdlib for most modules. `pandas`/`numpy` are used by `bova11_expiry_family.py`, `bova11_gravity.py`, `bova11_market_gamma.py`, `bova11_max_pain.py`, `bova11_oi_stats_light.py` and `bova11_quant_institucional.py`; the last one also uses `yfinance` for the BOVA11 history.

## Publication

`output/` is a git worktree of the `gh-pages` branch. Publishing:

1. `scripts/bova11_prune_site.py` keeps the 5 most recent sessions of each dated series and removes `_debug_*`/`*_test*` artifacts;
2. the runner commits with `git add -A` (so deletions propagate);
3. the `Publish dashboard` workflow validates the 1 GB GitHub Pages limit and deploys.

The site branch holds only generated HTML/CSS/JS — never commit code or data there.

## Architecture

**Data flow:** place the CSVs for D-1 and D in `data/` → run `bova11_runner.py` → HTMLs land in `output/`, `output/index.html` is the unified sidebar dashboard.

**Date auto-discovery:** scripts scan `data/` for files matching `venc * fechamento (*).csv`, extract date tags (e.g. `5mar`, `25posmar`), sort by file modification time, and use the two most recent as D-1 and D. `pos`/`pre` suffixes are normalized before date conversion.

**Resilience against empty data:** when an expiration is discovered but its CSV has no valid rows, the module skips that expiration instead of raising `KeyError: 'strike'`.

**Resilience against mixed maturities in one CSV:** some B3 closes mix expirations (a file labeled `W2` can carry the `D/W4` family). Filtering uses the real `exp_date` to derive the expected ticker family — monthly = same month code without `W1..W5`, weekly = month code plus the weekly suffix from `ceil(day/7)`. Modules 7–9 filter through `scripts/bova11_expiry_family.py` and consolidate repeated rows by numeric `strike` before computing.

**Volume CSV column alignment:** the volume files carry `strike` in column **5**, not column 4 (`call_ask`). Reading column 4 collapses volume to zero.

## Key Configuration Points

`scripts/bova11_auto.py`:

```python
ANO            = str(datetime.now().year)
GEX_STRIKE_MIN = 160
GEX_STRIKE_MAX = 200
```

Skew strike range (`scripts/bova11_skew_history.py` and the runner prompt):

```python
STRIKE_MIN_DEFAULT = 140
STRIKE_MAX_DEFAULT = 180
```

## CSV Input Format

```
venc 27 mar W4 fechamento (25mar).csv
venc 27 mar W4 fechamento (25mar Volume).csv
venc 27 mar W4 fechamento (25posmar).csv        ← pós-mercado suffix supported
venc 27 mar W4 fechamento (25posmar Volume).csv
```

Keep filenames strictly in this pattern: date tags drive D-1/D selection and the historical alignment. Copies or extra suffixes are treated as out-of-spec and emit `Nome fora do padrão esperado`.

## Dark/Light Mode

- Default: light (`:root` palette)
- Dark: `[data-theme="dark"]` on `<html>`
- Toggle: `#theme-toggle`, fixed top-right
- Persistence: `localStorage` key `bova11-theme`
- Chart.js colors are hardcoded and intentionally unaffected by the theme

## Calculation Integrity

- Unknown OI/IV/greeks stay null; observed zero is valid. Complete totals require full contract/expiration coverage; partial contributions carry explicit labels.
- Gamma flip is a sign-changing root of GEX(spot), not a strike crossing. Static GEX and modeled regimes are displayed separately.
- IV Rank uses comparable ATM IV over 365 days with ≥20 prior sessions; constant or insufficient series stay unavailable.
- `scripts/bova11_shared.py` parses both close schemas; `parse_b3_close_csv` accepts `str` or `Path` and merges the companion ` Volume).csv` when present.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
