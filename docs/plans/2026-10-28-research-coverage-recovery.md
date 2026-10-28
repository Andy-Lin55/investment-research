# Holdings coverage and upload recovery implementation plan

Goal: Continue the approved P3–P5 plan without paid AI calls, production activation or portfolio changes.
Spec: docs/plans/2026-10-27-holdings-research-upgrade.md; docs/plans/2026-10-27-research-execution.md.
Base: c9d7c588870538aaae0c51bfe0b68c7cd3acef0d.

## Task 1 — private full-roster coverage inventory (PR: coverage)
- [ ] Write failing tests for all Active rows, exact confirmed mapping, candidate-only company folders, traversal/symlink bounds and no file hydration.
- [ ] Add research.coverage.build_inventory(records, registry, root, observed_at) and private JSON/CSV export; absence is an explicit gap, not proof no documents exist.
- [ ] Add read-only `research.cli inventory --source INVESTMENT_ROOT --output PRIVATE_JSON --env-file PRIVATE_ENV`; no enable flag, model calls, cloud writes, or private stdout details.
- [ ] Normalize supported US source symbols using explicit venue/class-share rules, preserving original listing identity and rejecting ambiguous SEC matches. Test first.
- [ ] Run whole suite and real read-only Active/local metadata inventory. Commit, draft PR, exact cloud CI.

## Task 2 — safe interrupted uploads (separate stacked PR: delivery recovery)
- [ ] Reproduce interrupted uploads leaving empty final placeholders and lost commit responses.
- [ ] Upload to an isolated staging namespace; verify bytes before a conditional move to final destination. Name conflicts must fail, never overwrite. Incomplete staging artifacts remain recoverable and cannot become accepted final files.
- [ ] Exercise existing-human-file conflicts, concurrent final creation, readback mismatch, lost move response, and default/off behavior. Verify real synthetic uploads only under 投资/_系统验收.
- [ ] Run whole suite, fresh independent final review, fix important findings through RED→GREEN, draft PR and exact cloud CI.

## Constraints and rulings
- DMXAPI pricing explicitly deferred by user 2026-10-28. Unknown prices continue to block new paid research calls; budgets remain 2/task,10/day,100/month CNY.
- Reuse the existing dedicated /private/tmp clone, separate from original OneDrive working copy and the unrelated chat checkout. Use codex/ branches; do not merge or activate production.
- Directory matches are review candidates only. Existing research metadata is not source verification. No automatic issuer confirmation or model assumptions.
- Private holdings, local paths, API keys and upload URLs never enter public GitHub source, PRs, CI artifacts or logs.

## Review focus
- Duplicate or venue-suffixed listings must not silently select a different issuer.
- Symlinks and ambiguous folder names must not widen corpus scope.
- Coverage reports must include unmapped Active rows and explicit missing feeds/models.
- Upload failures or lost responses must not overwrite existing human files or mark zero-byte content verified.
- No implementation may require DMXAPI pricing or enable production to inventory or run offline tests.
