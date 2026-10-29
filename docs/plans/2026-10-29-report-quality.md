# Report quality and inline financial evidence

User scope: repair Notion formatting, show financial-model data inside reports, and learn from local Alpha Engine/public research agents while retaining the original research question. Existing production daily/weekly paths, credentials and budgets remain unchanged. Base: `0562448`.

## Findings and reference boundary

- Existing publication splits text into ordinary paragraphs; headings, tables and readable citations are absent. Forecast JSON is represented only by a few prose numbers, and historical statements are not attached to the report input.
- Read-only inspection of installed Alpha Engine 0.3.2 found accessible financial-research skills. Its earnings-preview instructions stage evidence, persist original tool output, calculate/check before writing, and link report claims to a source/derivation appendix. Three-statement instructions separate history/assumptions and verify cash/balance links. The main process is compiled V8 bytecode; full internal orchestration and model routing are not established. No proprietary source/prompts are copied into this repository.
- Public references: https://github.com/virattt/dexter/blob/main/src/agent/prompts.ts (bounded financial tool use and channel formatting), https://github.com/langchain-ai/open_deep_research/blob/main/src/open_deep_research/prompts.py (research brief, evidence preservation, source-backed final writing). These are design references, not runtime dependencies or authorizations to invoke their tools. Open Deep Research is archived; do not adopt it as a new production service.

## Implementation

1. Native Notion presentation: real headings, thesis/conclusion, source-linked findings, counterevidence, actions table, unresolved questions and source appendix. Keep legacy report verification for legacy keys; use an explicitly versioned new presentation for the existing candidate. Verify nested tables and all text/links by read-back. Preserve the old report and original analysis.
2. Inline financial evidence: structured historical IS/BS/CF with explicit periods, raw units/display scales, source identities and missing-value semantics; show verified forecasts/scenarios/assumptions/checks when supplied. Missing approved forecasts remain pending. Read only explicitly scoped verified financial packages; do not treat old cached human-model forecasts as current approved estimates.
3. Research contract: fix the core question; state requested metrics, countercase and falsification/next-action conditions; prioritize primary evidence and typed financial data over old notes. Inventory missing/stale/partial sources and bounded retrieval needs before writing. Preserve original evidence and use concise auditable reasoning summaries, not hidden reasoning transcripts. Keep the current local-subscription/API routing and hard cost gates; no unlimited model/tool loop or new paid data subscriptions.

## Decisions and acceptance

- Ruling: this is the requested report upgrade within the approved research workflow, not a new service. Use existing Python, Notion, OneDrive and model routing. The first reformatted candidate reuses its exact original thesis/conclusions; only presentation and linked financial evidence change. Cost if wrong: the user may prefer regenerated conclusions; regeneration remains a distinct new candidate rather than silent replacement.
- Ruling: local installed skills demonstrate available design, not the complete Alpha Engine runtime. Borrow source/validation discipline, not the vendor-specific S&P-only restriction or proprietary text. Missing data does not become fabricated coverage.
- Required: TDD contracts for native-block readback, inline table values/units/periods, question preservation and bounded evidence planning; full offline suite, independent review, exact-revision CI, real isolated Notion publication/read-back with no AI repayment. No production merge/enablement.

## Review corrections and current verification

- Initial independent review found seven important edge cases. Added RED-to-GREEN regressions and fixed long assumption-change tables/pre-create validation, Excel literal percent formats, explicit currency/scope/scale and stock/flow periods, scheduling-versus-primary provenance, unexpected Notion descendants, date-based period selection and late topic/location excerpt sampling. Cash conversion and debt repayment drivers now survive main-table selection; omitted periods and the selection rule are explicit.
- The complete archive keeps per-cell cache gaps and formats; the presentation and prompt use a compact gap inventory without treating absent values as zero. Topic/location sampling records exact supplied-text offsets and is explicitly non-exhaustive.
- 394 offline tests pass (five existing dependency warnings). Production daily/weekly path diff against the stable production revision is empty. An isolated native Notion presentation was created and read back with unchanged original analysis and no AI calls. A OneDrive root-link slash normalization difference was reproduced and fixed without relaxing numeric or query-parameter checks. Browser visual inspection remains unconfirmed because the available browser session requires Notion sign-in.
- A separate local-subscription candidate using the fixed question and mapped workbook is being exercised. Its delivery and investment reasoning must be assessed separately from formatting and structural test success.
