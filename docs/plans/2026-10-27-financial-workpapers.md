# Phase P1 — historical three-statement workpapers

Parent plan: `2026-10-27-holdings-research-upgrade.md`. Base: `3470cb8482815eb51b15b49b8c2e49bda96b3465`. Develop on `codex/research-financial-workpapers`; PR stacks on the unmerged safety foundation. PR regression runs on all target branches so stacked development gets the same secret-free checks.

1. Confirm an Active holding and inventory existing company materials without changing originals. Deduplicate issuer identity separately from listing codes; do not merge companies merely because their display names resemble one another.
2. Accept supplied financial provider payloads with all raw fields. Enforce consolidation, actual publication dates, periods, currency, industry scope, missing-versus-zero and conflicting revisions. Tests first.
3. Preserve original cumulative statements and derive only explicitly comparable additive single-quarter flows. Retain source lineage. Reconciliation checks cannot invent absent data or equate monetary funds to cash equivalents without a bridge.
4. Implement default-dry-run, exclusive-version exports and metadata-only source inventory. Never overwrite an existing file/model or formal version pointer. Preserve incomplete-export markers.
5. Build one real sample in the existing OneDrive company folder, retain source JSON and primary report, produce an editable historical workbook and identify missing source/model assumptions. Keep private holdings, inventory, notes and files out of GitHub logs/artifacts.
6. Run full offline regressions, inspect original PDF key numbers, test workbook recalculation and render all sheets, obtain an independent code review and verify the exact cloud revision.

Acceptance: reusable P1 code, a reviewable local sample with five annual/eight available quarter-end periods, explicit missing/reconciliation states, preserved originals, clean local and cloud regressions, draft PR. This does not activate production or complete linked forecasts/Notion/OneDrive API delivery. Those remain on the parent plan.

Review focus: quarter derivation from nonadditive fields; hidden period/revision/scope mismatches; NaN/zero/infinity; fake complete coverage; data validation bypasses; overwrite/symlink paths; partial writes; claims that local writes prove cloud sync; changes to current scheduled jobs.
