# Broker evidence integration and two-company acceptance

Approved scope: user accepted the next-step sequence on 2026-10-30. Parent specification: `2026-10-27-holdings-research-upgrade.md`; report contract: `2026-10-29-report-quality.md`.

## Boundaries

- Existing daily/weekly business paths, dependencies, schedules and production enablement remain unchanged. Work on a separate `codex/` branch. No automatic portfolio action or issuer confirmation.
- Source hierarchy: current primary originals, verified financial data, explicitly approved forecasts, prior hypotheses, then dated broker perspectives. A broker estimate is not an actual, an approved own-model assumption or market consensus.
- FX authorization already completed locally. Application code accepts an explicitly supplied key; it never discovers desktop credentials. API bearer headers go only to `api.fxbaogao.com`; PDF downloads use only `dr.fxbaogao.com` with no credential header. Redirects and response sizes are bounded. Errors omit keys, response bodies and signed URLs.
- Research-question matrix must exist before search. Search is bounded to six subquestions and one page per query; returned identity matches are candidates. At most two original acquisitions per execution. Archive original bytes, metadata, extraction, PDF page locators and digests in immutable company-scoped paths.
- Reserve each report download in durable storage before obtaining a URL. Resume from a verified original if available; an uncertain download without archived original needs reconciliation, never an automatic second download. A changed PDF under the same ID requires a separate reviewed acquisition.
- Use already downloaded Miniso/Goldman and Luxshare/Guolian originals for acceptance first, consuming zero additional download quota. Test delivery under dedicated acceptance folders. Existing company identities/models are not silently expanded or approved.

## Task 1 — source adapter and immutable acquisition

Files: `research/broker_sources.py`, `tests/test_broker_sources.py`.
Interfaces: `FxBaogao(key, request=None, max_calls=6)`, `.search(matrix)`, `.paragraphs(report_id, keyword)`, `.download(report_id)`; `archive_report(client, drive, *, company_path, issuer_id, report, identity_confirmed, cached_pdf=None)` returns an immutable manifest receipt. `load_report(drive, ref, *, company_path, issuer_id)` returns a typed `sell_side_report` context card.

1. Write and observe RED tests for candidate deduplication, plan validation, nonzero provider status, malformed replies, quota bounds, exact-host credential routing, redirect/HTML/oversize rejection, archive replay, uncertain attempt, conflicting identity and original digest verification.
2. Implement using existing Requests/PyMuPDF; do not add runtime dependencies. Original-PDF extraction is the only substantive report text admitted; provider summary is excluded.
3. Focused tests GREEN; commit source adapter and tests.

## Task 2 — workflow context, report constraints and manual entry point

Files: `research/broker_cli.py`, `research/context.py`, `research/harness.py`, `research/runtime.py`, `research/report_view.py`, relevant tests, research env example/runbook.
Interfaces: explicit holding `broker_report_refs=[{path,sha256,verified}]` loads archived reports, capped at two. Missing/corrupt/out-of-scope references become visible gaps. CLI takes explicit private configuration and opt-in write flag; imports remain inert. No scheduler change.

1. RED tests: issuer/scope/hash checks, seller forecast classification survives prompt and Notion rendering, single-source coverage is not consensus, structured variant requires dated/page-backed broker comparisons and a falsifiable independent hypothesis, missing comparisons remain explicit rather than fabricated.
2. Add a versioned opt-in broker-analysis contract for tasks containing broker originals. Legacy tasks retain existing schema. Questions, countercase, independent hypothesis, forecast basis and next validation remain separate. Automated gates validate structure/provenance only; investment judgment needs manual acceptance.
3. Focused and whole offline suite GREEN; run isolated live acquisition/read-back with existing PDFs. Run two local-subscription candidates with sources and gaps fixed; do not invoke the unpriced API fallback.

## Task 3 — audit, plan update and delivery

Files: HTML plan v14 (archive v13), runbook, sanitized acceptance records. Preserve existing HTML navigation/edit/save/download/fullscreen/zoom/print behavior.

1. Independently review the whole branch; fix material findings through reproducing tests. Verify no production-path diff.
2. Save exact source identities, digests, pages, gaps, candidate outcomes and costs. Deliver candidate links with model limitations, never label incomplete forecasts or two-week trial complete.
3. Draft stacked PR against the existing report-quality branch and exact-revision CI; no main merge or feature activation. Update HTML with proven results and remaining production gates.

## Review focus

1. Credentials redirected or leaked in provider exceptions; signed URLs accidentally archived.
2. Wrong issuer or stale report admitted because a title contains a familiar company name.
3. Lost acquisition/upload responses causing another entitlement debit or corrupt original acceptance.
4. Broker projections mistaken for verified company facts, own forecasts or multi-broker consensus.
5. Feature-enabled requests affecting legacy daily/weekly workflow or bypassing source/budget/human gates.

## Execution ledger

Ruling: reuse the dedicated private `/private/tmp` research checkout and branch from the clean report-quality revision; no need for another worktree. This checkout already isolates the investment repository from the unrelated chat workspace.

Pre-flight: Task 1 manifest receipts are consumed by Task 2, and Task 2 typed sources drive Task 3 candidate validation; all carry issuer, immutable byte digest and PDF page provenance.

Baseline: system Python 3.9 lacks Tushare/markdown-it/schedule (7 failed, 9 errors); this is an environment mismatch, not a business-code regression. Re-run with the established Python 3.11 runtime before implementation.

Baseline verified: `/private/tmp/investment-dividend-runtime/bin/python -m pytest -q` → 394 passed, 5 existing SWIG warnings.
Task 1: 19 meaningful source contracts observed RED then GREEN. Task 2: 12 context/quality contracts and 4 CLI contracts observed RED then GREEN; whole suite 429 passed.

Task 1/2 live search: six focused queries through the newly implemented official REST adapter returned 51 deduplicated candidates; no additional PDF downloads. HTML v14 extends the existing editable deck to 21 pages, preserving CSS and interactive script except its versioned local-save key.

Final independent review: 0 Critical, 3 Important, 0 Minor. One fix pass reproduced four failing cases: interrupted original identity rebind, cached revision conflicts (complete/interrupted), and legacy extra-field delivery. All fixes observed RED→GREEN; full offline suite 433 passed, 5 existing SWIG warnings. No findings deferred and no review rulings.

Live acceptance supplement: the real 17-source Luxshare corpus rendered 100 top-level blocks, exceeding the existing single-publication limit. A new reproducing test was observed RED; broker report references now use one native table preserving every source and hyperlink. The real candidate renders 83 blocks, the Miniso source-only candidate 65; whole suite 434 passed. Legacy presentation is unchanged.

Ruling: current Miniso Active status was not verified, so its sample uses the existing isolated report destination directly, outside the holdings task queue. No Active status or production issuer mapping is fabricated. Cost: this sample validates source research and delivery, not mandatory-event holdings scheduling.

Ruling: reconcile the exact frozen request/candidate instead of rebuilding a different corpus during delivery. Source digests and request identity are checked, and formal baseline checks still run. New evidence belongs to a subsequent candidate; no private input is silently substituted.

Original-page acceptance: Luxshare broker forecast numbers checked against original pages 1/44. The Miniso draft misattributed Europe to page 3; original shows page 4. Preserve raw subscription output and a separately corrected candidate. The PDF first-page image also resolves the period omitted by damaged text extraction as 2026Q2; it remains a broker attribution pending first-party verification.

Page-provenance supplement: two new tests observed RED→GREEN; only broker excerpts now retain explicit original PDF page prefixes, exact source/prompt spans and a versioned policy in the brief fingerprint. Supplementary independent review: 0 Critical/Important, 436 tests plus 500 randomized broker cases and 1,500 legacy comparisons passed.

Native live read-back supplement: Notion merges adjacent citations with the same link and formatting. A reproducer observed RED; canonical comparison now merges only semantically identical runs, preserving exact text, full URL/query and bold distinctions. No second page is created after uncertain publication; recovery only looks up the saved publication key.

Live delivery complete: two original PDFs archived/read back; Luxshare native candidate delivered with inline historical statements, and Miniso source-only native candidate delivered outside the holdings queue. Both preserve the core question and retain pending valuation/human/model-provenance status. Two local-subscription calls; zero new PDF debits and zero paid model API calls. Final local offline suite: 438 passed, 5 existing SWIG warnings. Formal enablement and the 14-day trial remain outstanding.
