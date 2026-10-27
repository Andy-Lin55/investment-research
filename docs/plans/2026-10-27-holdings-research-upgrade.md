# Holdings research upgrade — approved development plan

Status: development authorized 2026-10-27; production rollout remains gated by validation.
Baseline: `c9ab92ce744ae969c54ef9257ada321ddd910489`.

## Binding requirements

- Universe `Active` means an actual holding. Do not change trading, portfolio weights or Universe statuses automatically.
- Preserve the existing daily briefing and weekly financial snapshot. Reports remain in the existing Notion research destination.
- Store source documents, complete historical statements, financial models and research working papers under company folders in OneDrive / 投资. Reuse existing folders after inventory; shared policy/industry originals may be referenced by multiple companies.
- Organize information into trading/fund flows, fundamentals (including prices/valuation), and policy. Preserve source, publication/observation/ingestion times, units, periods and review status. Facts, calculations and interpretations are separate.
- Earnings and material disclosures must create durable research tasks, even when local computers are off. Proposed execution: GitHub cloud plus budgeted API. Local subscription capacity is for optional/deferable deep review. Weekly review first checks all Active holdings cheaply, then prioritizes changes and due questions.
- No additional permanent research server/database/object store in the proposed MVP. OneDrive Graph access, renewal and upload/read-back require separate integration validation.
- Deliver linked historical and forecast income statement, balance sheet and cash flow, scenario valuation, changes in assumptions, investment conclusion, counterevidence and specific follow-up/action conditions. Do not fabricate undisclosed quarters or market consensus.

## Phase 0 — protect the existing service before extending it

### Task 1: Restore and isolate the regression baseline

1. Run the complete existing suite and record all baseline failures.
2. Update obsolete tests to exercise current public behavior; do not change production behavior just to satisfy old tests.
3. Prevent pytest collection from loading real `.env` credentials, block external sockets and real SMTP, and keep temporary files in pytest-managed directories.
4. Cover current announcement filtering, PDF handling, Notion pagination/deduplication and financial snapshots alongside existing briefing/model-routing tests.
5. Verification: `python -m pytest -q`, all tests green with no real network, AI calls, emails or Notion writes.

### Task 2: Establish GitHub gates and document release isolation

1. Add secret-free PR regression CI, scoped to read-only repository permissions; keep current scheduled workflows unchanged.
2. Tag the stable baseline. Create a draft PR; verify CI against the exact branch revision.
3. Protect `main`: PR required, regression status required, admins included, force pushes/deletion disabled. Solo-maintainer repository does not require an impossible second-person approval.
4. Create a `production` environment restricted to `main`. Existing jobs do not yet reference it; route production credentials/jobs through it only in a separately validated rollout.
5. New feature workflows start manual and disabled for production writes. Use synthetic fixtures first, then separate Notion/OneDrive test destinations and test-only credentials. Never run credentialed API-connectivity checks as PR regression checks.
6. Record intended workflow/secret isolation as incomplete until end-to-end tests prove it. Merging code does not itself authorize data migrations or production feature activation.

### Task 3: Implement the information-card foundation

1. Add a pure, network-free evidence model and deterministic classification hints for the three categories.
2. Represent source facts, calculations and interpretations separately, with pending human review and explicit missing/ambiguous status.
3. Preserve stable event identity and document revisions; planned disposal is not executed disposal, and block trades are not inferred net inflows.
4. Provide an offline preview entry point using supplied fixtures only. No imports or calls from current production workflows.
5. Verification: failing contract tests first, then full offline regression suite; inspect generated cards from synthetic inputs.

## Subsequent phases

P1: Select a confirmed Active sample and inventory its existing Notion/OneDrive research and models; build historical financial statements and source index. Default goal is five annual and eight available interim/quarterly periods, subject to actual disclosure.

P2: Build linked forecast model/valuation and source-backed research updates. Add data/model validation and baseline-version conflict checks.

P3: Validate OneDrive delegated authorization/renewal, private upload/read-back, cloud event execution and Notion report linking. Independently test each delivery channel and retry behavior. No private files in public Actions logs/artifacts.

P4: Trial local subscription review, workload/cost measurement and weekly prioritization for 3–5 Active companies for at least two weeks. Mandatory cloud-event work must not wait for local capacity.

P5: Extend to all Active holdings after acceptance. Review existing server dependencies before any retirement.

## Rollout and rollback contract

- One capability per PR; no broad directory refactor as a prerequisite.
- Keep an immutable previous report/model version; candidate output cannot silently overwrite an approved model or human baseline.
- Shadow runs write only test outputs and do not send normal recipient emails.
- New integrations require separate test destinations and feature flags; initial value is off.
- Code rollback uses a revert PR or stable release. External data restoration uses archived versions and is verified separately.
- Every run records actual model/provider, evidence/model/config versions, checks, missing data, costs where available and delivery outcome.
- Important events blocked by data, authentication or budget remain visibly pending with a notification; never mark them complete.

## Acceptance before first production activation

All offline regressions pass; branch CI passes; test destinations are separate; one-company sample is manually reconciled; duplicate/revised/late inputs are replayed; API/source/upload/process failures resume correctly; backup restore is tested; production switch and rollback are documented.

## Review focus

Credential leakage or `.env` loading during collection; unexpected network/email/subprocess execution; changed production behavior; missing default-off paths; implicit trading/portfolio changes; weak source/period/currency validation; duplicate events vs. amended evidence; tests that hide failures instead of covering current contracts; falsely claiming deployment or data isolation is already complete.
