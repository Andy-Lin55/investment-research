# Historical financial workpapers (phase P1)

This independent, opt-in path accepts supplied Tushare `income`, `balancesheet`, and `cashflow` payloads. Each file may contain a `rows` envelope or an array. It has no automatic data-fetch, AI, email, Notion or OneDrive API call, and no imports/callers in current production jobs.

```sh
python -m financials.prepare /path/to/source-json /path/to/existing-company \
  --ticker 600519.SH --as-of 2026-10-27 --currency CNY
```

Default execution is a dry-run summary. Add `--write` only after checking the company destination. The first adapter supports calendar-year industrial A-shares (`comp_type=1`) with explicitly confirmed CNY source amounts. Banks, insurance and securities require separate templates and are rejected. Foreign-currency/fiscal-year adapters are not silently substituted.

## Source selection and data meaning

- Accept consolidated cumulative report types 1/4, excluding parent/single-quarter source rows. The cutoff excludes both future reporting periods and rows whose announcement/actual-announcement date exceeds it. It does **not** certify historical point-in-time availability of today's provider revisions.
- Choose the latest disclosed row, then adjusted consolidated report type and update flag. Equal-rank conflicting facts fail at every vintage, independent of source ordering; null and NaN are missing, zero remains zero, infinity is rejected. Source row and revision identity stay available.
- Retain all numeric fields, with CNY amounts, share counts and CNY/share EPS separate. Historical defaults are five available annual periods plus eight recent reported quarter-end periods, deduplicated. Missing statements, absent core financial inputs and shorter coverage remain visible; all-null rows cannot establish readiness. Core fields cover revenue/net profit, assets/liabilities/equity and the three cash-flow subtotals. Other missing inputs remain visible in the checks.
- IS/CF source values are cumulative from January 1. BS values are instants. Explicit adjacent-period subtraction can derive additive quarter flows, never BS balances, EPS or cash opening/closing balances. Noncomparable currency, scope, report type, statement, units or periods are rejected. `derive_quarter(..., comparability=...)` additionally requires `basis_confirmed=true`, a nonempty `source_locator` and the exact `current_revision_id` / `previous_revision_id`. A prior-period revision published after the current report is rejected. Dates/report types alone do not establish compatible accounting bases; no automatic caller asserts this evidence.
- Reconcile balance sheet, tax/net income, minority interest, cash components, cash opening/closing and CF starting net profit where available. Missing inputs are not treated as zero or a pass.
- Monetary funds versus cash equivalents requires a source-backed cash-definition bridge, even when the amounts happen to match. A reconciliation for one period is not applied to other periods.

## Local export and inventory

`02_财务数据/<asof>_<content-hash>/` contains complete source payloads, normalized JSON, facts CSV with units and source row, reconciliation CSV and a checksummed manifest. `04_研究底稿/<same-version>/` contains the working-paper index. These are candidate versions, `human_review=pending`, `cloud_sync_verified=false`; no formal model/report or current pointer is updated.

Version identity includes both normalized data and all retained source payloads, so changes to excluded or superseded source rows remain separately archivable.

Default dry-run writes nothing. Explicit export requires an existing company folder, rejects managed-path symlinks and never overwrites an existing version. Each destination is exclusively reserved. Failed exports retain `INCOMPLETE`; automatic recovery/resume is not yet implemented. Successful local writes are not proof of cloud synchronization.

`research.inventory.scan_company()` inventories file metadata only, excludes exporter-named version directories (`YYYYMMDD_<12 lowercase hex digits>`) directly under `02_财务数据` / `04_研究底稿` and symlinks, and marks truncation. Pre-existing company materials in those categories, financial models and archives stay in the inventory. It does not read/hydrate source file contents or claim that local models are readable, current or authoritative. Private inventory and holdings metadata belong in company workpapers, not in the public repository.

## Scope of the first real sample

The first sample builds historical statements and a local review workbook; it is not an approved forward forecast, investment conclusion, full historical original-filing audit or cloud delivery. Excel outputs must preserve formulas for scale conversion and checks, be recalculated and visually reviewed. Actuals and future editable assumptions remain separate. OneDrive/Notion integration, scheduled research and forecast scenarios are subsequent phases.
