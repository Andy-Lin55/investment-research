# Broker evidence: explicit acquisition and source-backed comparison

This optional module does not change scheduled daily/weekly jobs. It accepts an explicitly supplied FX credential, never reads the desktop skill automatically and never logs keys or signed download URLs. Keep private configurations, selections and candidates outside the Git repository.

## Discovery

Prepare a private JSON matrix with 1–6 focused rows, each containing `question` and `keywords`. For a new full company study, define 3–7 research subquestions first and choose at most six for this bounded search. Candidate matches are not confirmed issuer mappings.

`python -m research.broker_cli search --matrix PRIVATE_MATRIX --key-file PRIVATE_KEY_ENV --output NEW_PRIVATE_CANDIDATES`

The command searches one recent-three-month page per question. Results are deduplicated by report ID and normalized institution/title/date. Source outages, access failures or malformed responses are errors, not proof of no coverage. No PDF entitlement is consumed by this command; provider search-call entitlement may apply.

## Acquire one selected original

Selection JSON: `company_path`, `issuer_id`, `identity_confirmed: true`, and `report` containing integer `report_id`, `title`, `org_name`, ISO-date `published_at`. Confirm the issuer/listing against the original; a familiar keyword alone is insufficient. Use an acceptance namespace for unconfirmed portfolio mappings.

`python -m research.broker_cli archive --write --env-file PRIVATE_RESEARCH_ENV --key-file PRIVATE_KEY_ENV --selection PRIVATE_SELECTION --output NEW_PRIVATE_RECEIPT`

Requires `RESEARCH_BROKER_SOURCES_ENABLED=true` in the explicit research configuration. Global holdings production can remain disabled. Add `--cached-pdf PRIVATE_ORIGINAL` to reuse a previously acquired and reviewed original without another download request or FX credential. Graph credentials are still needed for archival.

Original files, extraction and manifest live at `COMPANY/01_原始资料/卖方研报/fxbaogao/REPORT_ID/`. The manifest binds issuer, institution, report date, original digest and page-marked extraction. Immutable writes are read-back verified. A download attempt is durably reserved with a unique ID before any entitlement-consuming request. Lost download/upload responses without an archived original require reconciliation; never delete the attempt marker just to retry. Existing originals are reused. Same-ID revisions need a separately reviewed acquisition; do not overwrite the prior original.

At most two downloads and six HTTP operations per client execution; redirects count toward the latter. An API URL is never followed to another host with credentials. Original download hosts must be exact HTTPS `dr.fxbaogao.com`, without embedded credentials or nonstandard ports. Files over 40 MiB, HTML and unreadable PDFs are rejected. Monthly source entitlement is provider-controlled; until an account balance endpoint is verified, operators must check remaining quota on the provider site. These bounds are not a claim of a synchronized monthly balance.

## Consume verified originals

Add up to two manifest receipts to the confirmed issuer's private `broker_report_refs` list. Each receipt has `path`, `sha256`, `verified: true`. The worker verifies issuer, company scope, manifest/original digests and re-extracts original PDF bytes. Provider summaries and paragraph search excerpts do not enter formal evidence. Failed references appear in context gaps.

Broker-enabled tasks use the versioned `holdings-broker-v1` contract: independent hypothesis, mechanism and falsifier; dated broker comparisons with available PDF pages; own assessment, affected model assumptions and required first-party verification. A single broker or a selected sample is always `sampled_views_only`, never market consensus. Own-model assumptions still need approval; missing historical/forecast inputs remain pending.

Notion uses native headings for independent hypothesis and broker disagreements. Each broker comparison shows institution, report date, PDF pages, original-report link and model implication. Existing task schemas remain valid when broker originals are absent. Validation checks structure, reference identities and page availability; it cannot certify the truth of a model's paraphrase or investment judgment. Human source/number and thesis review remain mandatory.

## Rollout gates

Two-company pilots first; use existing originals before more downloads. Preserve the original research question and prior candidates. Record primary-source coverage, model gaps, actual provider provenance, subscription failures, costs and independent quality review. No automatic API fallback while pricing is deferred. Real-company forecast approval, dedicated cloud credentials, source-identity mapping, and a 3–5 company 14-day trial still precede production enablement. This module alone does not activate scheduled broker discovery.
