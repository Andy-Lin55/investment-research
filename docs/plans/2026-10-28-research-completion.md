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
