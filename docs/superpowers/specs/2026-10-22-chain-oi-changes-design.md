# Chain OI Changes — Design

## Goal

Add a dashboard tab named **Chain OI Changes** for BOVA11. It compares option open interest from D-1 and D, with one row per option contract.

## Integration

- Add a small standalone Python module that writes `output/bova11_chain_oi_changes.html`.
- Run it through `bova11_runner.py` with the already discovered data directory, reference date, and D/D-1 tags.
- Add the generated page to `build_dashboard_modules` as the **13th entry** in the current menu. Do not reactivate modules missing from the current runner.
- Reuse `bova11_shared.py` for B3 close parsing and `bova11_expiry_family.py` for expiry-family validation. Join snapshots by option ticker, keeping call and put contracts distinct.

## Data and display

The table contains underlying (`BOVA11`), option ticker, side, expiry, strike, D-1/D OI, absolute and percentage OI change, D/D-1 volume, current bid/ask, IV, Greeks, trades, and DTE. Add expiry/side/search filters, sortable columns, and CSV download. Sort initially by absolute OI change.

Calculate percentage change only when prior OI is positive. Keep missing inputs blank; observed zero remains zero. Derive expiry and DTE from the contract/session dates.

Current B3 closing files do not include last trade price or average trade price, and volume files do not classify aggressor, neutral, or multi-leg flow. Do not synthesize these fields. Earnings date and sector are not relevant to the BOVA11 underlying.

## Failure behavior

- If a prior snapshot or OI value is missing, show the contract and available fields but leave the OI delta blank.
- If an expiration has no valid contracts, skip it without failing the report.
- Keep dates and expiration families explicit to avoid mixing weekly and monthly chains.

## Acceptance criteria

- Runner creates the new HTML and the unified dashboard lists **Chain OI Changes** as entry 13.
- Each option contract appears once; calls and puts remain separate and current/prior records match by ticker.
- OI deltas reconcile to current OI minus D-1 OI; missing values are not treated as zero.
- Filters and CSV download work in the generated page.
- No unsupported trade-price or flow-classification fields are fabricated.
