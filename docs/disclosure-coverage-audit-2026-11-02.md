# Disclosure coverage audit — 2026-11-02

Read-only Beijing publication window: **2026-10-26 through 2026-11-02 inclusive**. The configured Universe returned **44 unique Active/Watch securities** (25 Active, 19 Watch). The Events read paginated through **2,277 records**. A source inventory completion means the configured requests finished; it does not prove that the provider indexed every disclosure.

| Market | Roster | Inventory completed | Blocked | Observed source items | Formal report URLs absent from primary Events |
|---|---:|---:|---:|---:|---:|
| A-share | 20 | 20 | 0 | 80 | 5 |
| Hong Kong | 20 | 20 | 0 | 136 | 17 |
| US | 3 | 0 | 3 | — | Not assessed |
| NEEQ | 1 | 0 | 1 | — | Not assessed |

Of **35 formal report candidate URLs**, **13** matched a primary Events record and **22** did not. Matching requires the exact ticker and the original URL, with HTTP/HTTPS treated as equivalent. News records do not satisfy the match. The count is by source URL, so language versions or cross-listing can appear separately. The five A-share URLs include one H-share announcement for an issuer whose A-share report already matched. The manually backfilled JiaYi original matched.

The broad CNINFO scan used **63 requests**, including pagination. Company-name results contributed additional records for **10 of 20 A-share names**. For example, the JiaYi code query found one in-window item and the merged inventory contained three. The HKEX scan used **40 broad English/Chinese requests** after the previous category-restricted adapter missed a confirmed `3968.HK` quarterly report. HKEX response limits are reported separately from an empty successful response.

The independent Tushare income-period check returned 2026Q3 for all **20 A-share names**. For **17** names the publication was inside the audit window and the same period was observed in the CNINFO report inventory; the other **3** had publication dates before the window. This comparison does not assert completeness outside the window.

**Remaining gaps:** `BRK.B`, `QFIN.O` and `SPG.N` were blocked before SEC requests because the audit configuration lacked a real contact. SEC access was therefore not tested. `830806.NQ` is a numeric NEEQ symbol, not a US SEC ticker; the NEEQ adapter was unavailable during this audit. The audit returned an incomplete status for these four names. No live Notion writes, email, model calls or research runs were performed.

The collector keeps the existing list/dictionary return shapes, adds optional health/strict controls, merges exact code/name results, follows CNINFO pagination, and applies Beijing date bounds. The daily collection jobs surface incomplete source retrieval and retain the pre-parse idempotency check. The audit is separate from scheduled jobs:

```sh
python -m scripts.audit_disclosures --env-file PRIVATE_ENV --output LOCAL_EVIDENCE_DIR \
  --as-of 2026-11-02 --days 7 --financials
```

Use `--resume` to reuse matching CNINFO and Tushare observations while refreshing Notion and HKEX reads. The detailed roster, event references, inventory checkpoints and original URLs are local audit evidence; they are not committed to the repository.

Known configuration gaps now appear as `blocked` per ticker in the filings job summary. Unexpected failures and truncation on configured sources still fail the collection job; the briefing runs independently with available records. If a real `SEC_USER_AGENT` secret already exists, the filings job reads it. No secret was created by this change. Use ticker plus original URL for deduplication, including HTTP/HTTPS equivalence; reserve date/type deduplication for records without an original URL.

On November 3 the NEEQ adapter was verified against the official inventory. It found four documents for 830806.NQ in the seven-day lookback, including the October 28 quarterly report, and the briefing reader extracted the original PDF text.
