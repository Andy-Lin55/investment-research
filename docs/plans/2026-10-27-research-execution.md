# Remaining implementation: P2–P5 execution and acceptance

Parent: `2026-10-27-holdings-research-upgrade.md`; base `3c60b2d4e4252910ff45ba3f27feaa01f364f402`.

Continue without stopping at another sample. Production integration remains default-off until real destination/authorization and acceptance checks pass. Existing daily/weekly business modules and workflows are unchanged.

1. P2: linked industrial three-statement forecast with explicit sourced assumptions, base/bull/bear scenarios, earnings valuation, funding shortfalls, financial checks and version conflicts. Structured research must cite supplied evidence, carry counterevidence, open questions and conditional actions; missing assumptions produce a pending notice, never invented forecasts.
2. P3: durable Notion task checkpoints, strict issuer identity and Active selection, duplicate/revised/late source handling, cloud single-publisher execution, OneDrive Graph refresh/upload/read-back with conditional version pointers, Notion candidate reports, per-channel checkpoints, replay and backup restore. No private artifacts or raw model outputs in public CI logs.
3. P4: weekly all-Active rules-based screening, priority/rotation and local subscription-only deep work; exhausted subscription defers optional work. Mandatory events always use the independent cloud API path. Record actual provider/model/usage, elapsed time, configured price rates and budget reservations. Authorized CNY limits: 2 per task, 10 per day, 100 per month; unknown pricing must block paid calls.
4. P5: roster-wide dry-run and configurable 3–5 issuer pilot, failure recovery and rollback tools, operating runbook and measured trial ledger. Do not call a two-week trial complete before two weeks elapse, retire servers, change holdings, or overwrite human models.

Verification: failing contract tests before new modules; full offline suite after each stage; one independent branch review; exact GitHub revision regression; local synthetic end-to-end failure/replay; private live service validation only after authorization exists. Publish remaining blockers explicitly rather than mark them passed.
