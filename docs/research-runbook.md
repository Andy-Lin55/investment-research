# Holdings research pilot: operation, limits and acceptance

Status: development candidate, default off. Existing daily briefing and weekly snapshot modules/jobs are unchanged. This branch adds optional execution, not a completed production migration. Budget authorized on 2026-10-27: CNY 2/task, 10/Beijing day, 100/month. Missing account-specific prices block calls rather than invent charges.

## What exists

- `financials.forecast.project`: explicitly sourced industrial annual baseline and base/bull/bear assumptions; linked income/balance/cash statements, parent EPS, earnings-multiple valuation, assumption changes, funding gaps and reproducible versions. Cash/financing is never plugged. Unknown inputs remain pending. The historical adapter is still industrial A-share CNY; bank/insurance and automated cross-market historical adapters remain outside this first pilot.
- `research.cli`: offline doctor by default; explicit opt-in/write for scan/worker/weekly/local. Only Active listings are selected. A private, confirmed issuer registry links listings to company folders; unknown/duplicate identities remain visible. No holdings/trades/signals change.
- CNINFO pagination; broad HKEX queries with capped-window splitting; SEC recent/history submissions. Daily replay window is 21 days; that is a bounded lookback, not a guarantee of finding arbitrarily old backfills. Primary PDFs/HTML are retained. Bounded extracted text retains page locators and partial-coverage metadata. Scanned/image-only files remain pending, not silently zero.
- Explicitly mapped official RSS feeds provide policy/trading/fundamental cards. Feed excerpts are not full article ingestion; material items require original retrieval. No configured feed/exposure mapping means a visible coverage gap. This does not promise access to every proprietary transaction/fund-flow/industry dataset.
- Existing OneDrive PDF/DOCX/XLSX/text and Notion child blocks are read only when listed in the private company registry. Cached Excel values/old notes remain prior analysis; inaccessible files remain gaps. Human files are never updated.
- Notion task checkpoints, human-review properties, per-step delivery receipts, Graph immutable version files with read-back checksums, and candidate reports. Large text/context live in verified OneDrive references rather than task properties. Billing reservations and uncertain outcomes persist.
- Weekly screening includes all mapped Active issuers; priority goes to changes, due questions and oldest review. Only optional deep reviews go to the local subscription worker. They never fall back to paid APIs. Local candidates are immutable and published by the cloud worker. CLI model identity not reported by the subscription backend remains a provenance gap, not a fabricated model name.
- Formal release is a separate default-off function requiring exact model acceptance and human confirmation. Pointer ETags, prior snapshots, partial delivery state and explicit rollback protect newer baselines. Independent local ZIP backup/restore verifies checksums and restores only to a new destination.

## Setup and first authorization

1. Install `requirements.txt` and `requirements-research.txt` in an isolated Python environment. Copy `.research.env.example` to ignored `.research.env`, owner-only permissions; fill existing Notion/provider values privately. Do not send secrets in chat or put private sources in GitHub.
2. `python -m research.cli notion-setup --write --parent PAGE_ID --output PRIVATE_CONFIG_JSON` creates a separate pilot workspace with tasks and candidate reports. It does not edit existing Universe/Events/Research schemas. Set the returned source IDs in private configuration. Production report destination is connected only after pilot acceptance.
3. Register your Graph public-client application for personal Microsoft accounts at [Microsoft Entra](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade). Enable public-client/device flow. Delegated scopes are `Files.ReadWrite`, `User.Read`, `offline_access`. This route needs no client secret. This is the application's own registration, not reuse of desktop sync credentials. See [Microsoft's device flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code).
4. Only when ready to sign in: `python -m research.auth --client-id YOUR_APPLICATION_ID --cache /PRIVATE_DIR/.research-graph-token.json`. Follow Microsoft's verification page/code. Refresh/access/device tokens are not printed. Keep `ONEDRIVE_TOKEN_CACHE` pointing to that private file.
5. For cloud refresh-token rotation, provision a fine-grained GitHub token restricted to this repository and Actions secrets write as `RESEARCH_GITHUB_TOKEN`. Refresh tokens stay in `ONEDRIVE_REFRESH_TOKEN`; do not store them in Notion/OneDrive. The token writer is restricted to that one secret. Keep repository/environment credentials separate from PR regressions.
6. Confirm actual DMXAPI account/group input/output prices in CNY per million tokens. Configure both prices; rates and reported usage determine project costs. Failed/uncertain calls retain their reservation. Unknown models/usage, rate overruns, or exhausted budgets stay blocked. Existing public price endpoint access can fail; this is not proof of a zero price.

## Private issuer registry

Store in `RESEARCH_ISSUERS_JSON` (GitHub secret) or a local `RESEARCH_REGISTRY_FILE`; it is needed independently of Graph authentication. Never commit it. Each row contains:

```json
{
  "issuer_id": "confirmed-company-id",
  "identity_confirmed": true,
  "tickers": ["600519.SH"],
  "company_path": "投资/行业/公司",
  "baseline_version": null,
  "context_files": [{"path": "投资/行业/公司/old-note.md", "role": "prior_research"}],
  "context_pages": ["NOTION_PAGE_UUID"],
  "forecast_inputs_path": "投资/行业/公司/03_财务模型/forecast-inputs.json",
  "next_review_date": "2026-11-01"
}
```

Do not merge A/H/US listings by similar names. The forecast input file contains `baseline`, `scenarios`, `as_of` with the contract tested in `tests/test_forecast_model.py`. Every assumption needs a source or explicit system-assumption rationale. Historical cash-definition bridges and financial fields require verification. Blank assumptions do not generate a complete valuation. Formal baseline changes must be reflected in the registry before subsequent tasks; a stale baseline intentionally blocks publication.

## Run and recover

`python -m research.cli doctor --env-file PRIVATE_ENV` is offline. Service credentials are never automatically loaded from `.env`. Set `HOLDINGS_RESEARCH_ENABLED=true` only after configuration/read-back checks. Explicit commands:

```sh
python -m research.cli scan --write --env-file PRIVATE_ENV
python -m research.cli weekly --write --slots 3 --env-file PRIVATE_ENV
python -m research.cli worker --write --limit 3 --env-file PRIVATE_ENV
python -m research.cli local --write --limit 3 --env-file PRIVATE_ENV
```

The GitHub pilot uses one concurrency group, main-only credentials, a separate `research-pilot` environment and no private Actions artifacts. Suggested schedules: daily 06:17 Beijing plus 10:43/16:43/22:43 replay/resume; weekly Saturday 08:23. GitHub can delay schedules. These are new pilot schedules; the old daily briefing's 05:00 schedule is unchanged. The workflow stays off until its enable variable is explicitly set.

Optional local polling can be installed after login: `python scripts/install_local_review.py --env-file PRIVATE_ENV --write`. It polls hourly/at login and uses subscription only. The installer refuses an existing job; it is not run automatically by this branch. A sleeping/off computer can defer optional reviews; cloud mandatory tasks are independent.

- Re-run cloud worker after service recovery: existing source/analysis/report receipts prevent re-paying or duplicate delivery.
- A lost Notion create response is queried by publication key; if still unknown, no second create is attempted. Operator investigates instead of clearing checkpoints blindly.
- Invalid evidence/citations/model arithmetic produces `quality_blocked`; changing providers does not repair facts. API retries are limited to two task attempts, including uncertain charges. Budget is lifetime task/day/month across durable task history.
- Late/revised sources get a new revision task, with metadata-only discovery linked to its resolved original. Full-source retrieval failures stay pending. Missing API pricing, OneDrive consent or model assumptions cannot count as complete research.
- Human edits to review fields are preserved. A changed formal model baseline triggers conflict. Local worker never writes formal pointers or mutates cloud task checkpoints.
- Snapshot: `python -m research.cli backup --write --source COMPANY_VERSION_DIR --output OUTSIDE_ONEDRIVE.zip`. Restore only to a new folder with `restore --source SNAPSHOT.zip --output NEW_DIR`. Keep at least one independent copy outside the synchronized source tree.

## Acceptance and remaining rollout work

Live Notion pilot has verified task/status read-back, task/report idempotency and preservation of a manual review state using synthetic data; that is not real-company research acceptance. Required next checks: first Graph authorization; upload/read-back/renewal/ETag conflict on the actual account; confirmed issuer registry and source/pricing configuration; a source-backed financial baseline/assumption set; real company end-to-end research; independent backup recovery; and at least 14 observed pilot days for 3–5 companies covering earnings/incremental/no-change work. `research.release.trial_readiness` excludes synthetic or unverified runs.

Do not merge/enable the pilot as a substitute for acceptance. Do not retire the server: source adapters, provider routing, local subscription runtime and other services may still depend on it. Full Active expansion, financial-sector/cross-market adapters, longer observation and dependency retirement remain explicitly unaccepted work.
