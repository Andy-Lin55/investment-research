# Research completion: usable models and continuity

Parent: `2026-10-27-holdings-research-upgrade.md`. Starting revision: `706862a`.
Existing daily/weekly production paths, requirements, credentials and schedules are out of scope.

1. Export reproducible forecast workbooks from the already validated industrial model: sourced baseline, explicit scenario assumptions, linked three statements, valuation, funding gaps, checks and assumption changes. Incomplete assumptions remain blank/pending. Import human-edited assumptions only against the exact original model identity; formulas or modified baselines are rejected. Deliver Excel through its own verified immutable checkpoint and link it in Notion.
2. Persist unresolved questions and conditional research actions in a dedicated pilot Notion source. Idempotent identities, creation-response reconciliation, human status preservation and issuer scoping. Carry unresolved items into subsequent context as prior analysis, use due dates for weekly priority, never execute trades or automatically close items.
3. Verify combined model/report/follow-up delivery, interruption and replay. Full offline regression and fresh independent review; exact-revision GitHub checks. Real isolated-service smoke checks where credentials allow. Keep production default-off and distinguish synthetic proof from the required real-company/two-week trial.

## Decisions

- Ruling: the workbook exporter is application code executed on GitHub, which cannot depend on Codex's desktop artifact runtime. Use a research-only XlsxWriter dependency, preserving the production dependency file. Cached values come from the validated Python model; formulas remain editable and Excel recalculates on opening. Cost if wrong: desktop and cloud workbook results diverge; cross-check every exported result against the validated model and document the lack of native Excel acceptance.
- Ruling: the 23 unconfirmed company-folder mappings, financial assumptions, cash-scope bridges, deferred API rates, separate cloud credentials and real trial remain rollout prerequisites. They are not inferred or bypassed to declare completion. Cost: production activation waits for explicit evidence.

## Review focus

Formula references across multiple years and scenarios, missing-input behavior, cached/model agreement, hostile cell strings, edited-workbook identity, immutable retry semantics, late publication recovery, human follow-up state preservation, source-vs-prior-analysis labeling, default-off and production isolation.

## Verification ledger

- Start: clean isolated branch; existing base has 303 passing tests and unmerged draft PRs. No live production mutation authorized by this plan.
- Task 1: complete — 11 workbook contracts and one delivery-recovery contract observed RED, then GREEN; full offline suite 315 passed (existing SWIG deprecation warnings). Workbooks and model JSON require verified upload receipts. No production file changed; native Excel acceptance is not claimed.
- Task 1 supplement: private CLI export/import RED→GREEN; formula/cache agreement checked independently for every statement formula across distinct cases and multiple years. A reproduced comparison-history collision now stores each immutable snapshot under its own content digest.
- Task 2: complete — follow-up creation/reconciliation, manual state preservation, issuer scope, context carry-forward and weekly due priority contracts RED→GREEN. Full offline suite 327 passed. Current-task follow-ups are excluded from its own input fingerprint. Live combined acceptance and final independent review remain pending.
- Final review: fresh independent reviewer found 0 Critical, 4 Important, 0 Minor. One fix pass: context/journal preservation, Excel missing-input guards, human Summary context and canonical missing-input comparison each observed RED→GREEN. No findings deferred. Full suite 333 passed after the fixes.
- Task 3: offline combined durable-adapter delivery/replay passed, without model calls. Real isolated Notion follow-up acceptance: 5 passed. Combined online Graph/Notion acceptance: incomplete after local ProxyError on initial fixture upload, including bounded immutable-write retries; no private workbook/report falsely marked delivered. No production activation, paid calls, portfolio changes or normal mail. Native Excel, real-company financial acceptance and the real 14-day trial remain rollout gates.
- Follow-up acceptance: the earlier transport failure did not recur; diagnostic upload/read-back and seven combined online Graph/Notion synthetic checks passed without network-setting changes or paid model calls. No persistent root cause or long-term stability is claimed.
- Native Excel: five actual edit/recalculate/save cases passed with baseline preservation; restored inputs also passed importer roundtrip. The discovered integral-double serialization defect was reproduced RED→GREEN in commit `c5888e0`; independent focused review: 0 Critical, 0 Important, 0 Minor. Full model branch321 tests and full research branch334 tests passed.
- Ruling: preserve authoritative numeric representations only under exact numeric equality; do not add rounding tolerances or revise general model hashing. This prevents an unchanged Excel save from becoming a new model version. Cost: rounded values remain changes requiring review rather than being silently treated as equal. Prior whole-branch review and repeated live checks were outside the narrow serialization review; their independent evidence remains recorded above.
