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

The GitHub research workflow uses dedicated `RESEARCH_NOTION_TOKEN`, `RESEARCH_DMXAPI_KEY`, `RESEARCH_GEMINI_API_KEY` and `RESEARCH_SEC_USER_AGENT` secrets, mapped to the existing runtime environment names. Provision these separately in `research-pilot`; they never fall back to the old pipeline's `NOTION_TOKEN`/`DMXAPI_KEY`/`GEMINI_API_KEY`. No credentials have been copied or migrated by this workflow change. Dedicated research access, destination verification and explicit enablement remain required before cloud activation. Existing daily/weekly jobs keep their current secrets and schedules.

Offline isolation tests pin the approved daily/weekly workflow bytes and forbid legacy production credential references in the pilot or secret references in PR regression CI. An intentional change to the old workflows needs its own reviewed production update and baseline refresh.

1. Install `requirements.txt` and `requirements-research.txt` in an isolated Python environment. Copy `.research.env.example` to ignored `.research.env`, owner-only permissions; fill existing Notion/provider values privately. Do not send secrets in chat or put private sources in GitHub.
2. `python -m research.cli notion-setup --write --parent PAGE_ID --output PRIVATE_CONFIG_JSON` creates a separate pilot workspace with tasks, candidate reports and follow-ups. It does not edit existing Universe/Events/Research schemas. Set the returned source IDs in private configuration. Production report destination is connected only after pilot acceptance.
3. Register your Graph public-client application for personal Microsoft accounts at [Microsoft Entra](https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade). Enable public-client/device flow. Delegated scopes are `Files.ReadWrite`, `User.Read`, `offline_access`. This route needs no client secret. This is the application's own registration, not reuse of desktop sync credentials. See [Microsoft's device flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code).
4. Only when ready to sign in: `python -m research.auth --client-id YOUR_APPLICATION_ID --cache /PRIVATE_DIR/.research-graph-token.json`. Follow Microsoft's verification page/code. Refresh/access/device tokens are not printed. Keep `ONEDRIVE_TOKEN_CACHE` pointing to that private file.
5. For cloud refresh-token rotation, provision a fine-grained GitHub token restricted to this repository and Actions secrets write as `RESEARCH_GITHUB_TOKEN`. Refresh tokens stay in `ONEDRIVE_REFRESH_TOKEN`; do not store them in Notion/OneDrive. The token writer is restricted to that one secret. Keep repository/environment credentials separate from PR regressions.
6. Confirm actual DMXAPI account/group input/output prices in CNY per million tokens. Configure both prices; rates and reported usage determine project costs. Failed/uncertain calls retain their reservation. Unknown models/usage, rate overruns, or exhausted budgets stay blocked. Existing public price endpoint access can fail; this is not proof of a zero price.

## Read-only coverage review

Use `python -m research.cli inventory --source /PRIVATE/OneDrive/投资 --output /PRIVATE/coverage-DATE.json --env-file PRIVATE_ENV` to query Active rows and inspect local directory/file metadata. This command requires neither production enablement nor `--write`; it performs no cloud mutations, document hydration or model calls. Stdout contains counts only. Private JSON and human-readable CSV files are created with owner-only permissions, never overwrite prior exports, and are refused inside Git working trees.

Every Active listing remains in the report, including duplicates and unmapped issuers. Exact directory-name matches are suggestions for human confirmation, never automatic corpus authorization. Hidden/symlink directories are excluded; traversal bounds and unreadable metadata remain gaps. Policy/trading feeds marked configured are still unverified until fetched. SEC lookup accepts the explicit `.O`/`.N` venue spelling and dotted class-share spelling while preserving original portfolio codes; multiple matching issuer identities fail closed.

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
  "financial_workbook_path": "投资/行业/公司/02_财务数据/版本/公司_历史三表与底稿.xlsx",
  "financial_workbook_as_of": "2026-10-27",
  "financial_workbook_company": "公司",
  "research_question": "规模扩张能否转化为持续利润和经营现金流？",
  "next_review_date": "2026-11-01"
}
```

Do not merge A/H/US listings by similar names. The forecast input file contains `baseline`, `scenarios`, `as_of` with the contract tested in `tests/test_forecast_model.py`. Every assumption needs a source or explicit system-assumption rationale. Historical cash-definition bridges and financial fields require verification. Blank assumptions do not generate a complete valuation. Formal baseline changes must be reflected in the registry before subsequent tasks; a stale baseline intentionally blocks publication.

## Report presentation and research direction

New tasks use version 2 native Notion reports: headings, readable numbered citations, historical statement tables, explicit forecast/scenario and assumption tables when configured, counterevidence, conditional actions and missing questions. Legacy task keys keep the previous presentation and lookup behavior. Reformatting an existing candidate requires a distinct versioned publication key; it never overwrites the original report or silently changes its conclusions.

The optional historical-workbook fields above admit only the explicitly mapped company template, with a checksum, actual-period labels and supported display units. Cached historical values are not approved forecasts. Main-report tables select key metrics and comparable periods; the complete data and cell coordinates stay in the source workbook and archived context. Missing values remain distinct from zero; currency/scope/scale gaps stay explicit. Native read-back verifies table children, values and links, rejecting unexpected descendants. Presentation validation runs before recording an uncertain page-creation attempt.

The fixed research question enters a versioned research brief with source priorities, accounting boundaries, required countercase and conditions for changing the conclusion. Bounded excerpts retain source locations and explicit partial coverage. The report may request at most six typed missing-data items. Those are an auditable needs list, not an autonomous tool loop: only configured collectors and the mapped workbook reader retrieve data. No arbitrary URLs/files/tools, paid fallback or new data subscriptions are authorized by model output. Local AlphaEngine skills and public Dexter/Open Deep Research implementations informed the design; AlphaEngine's compiled orchestration was not reconstructed and none of those systems is a new runtime dependency. Improved instructions and passing structure checks alone do not prove better investment judgments.

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

- New immutable uploads are first written under `投资/_研究上传暂存`, read back and hashed, then moved to the final folder with the staging ETag and conflict behavior `fail`. An interrupted or mismatched staging file is never accepted as final. Lost move responses are reconciled by final checksum; different existing files (including zero-byte human files) remain conflicts. Interrupted staging attempts are retained for inspection and retried with a new staging name; this may retransmit a file, but upload-session credentials are never persisted in Notion/GitHub or public logs. Legacy partial final files require explicit operator reconciliation; they are not silently removed.
- Re-run cloud worker after service recovery: existing source/analysis/report receipts prevent re-paying or duplicate delivery.
- A lost Notion create response is queried by publication key and its full expected body is compared on read-back; if still unknown, no second create is attempted. Operator investigates instead of clearing checkpoints blindly.
- Invalid evidence/citations/model arithmetic produces `quality_blocked`; changing providers does not repair facts. API retries are limited to two task attempts, including uncertain charges. Budget is lifetime task/day/month across durable task history.
- Reservation and attempt intent are saved in one checkpoint. A crashed inflight/legacy orphan reservation stays `pending_attempt_confirmation`, without spending again. Blocked/inactive/release-state tasks do not consume worker slots. After fixing inputs/configuration, an operator can select `RetryRequested`; this does not reset charges or bypass model checks. Retryable tasks rotate by last-attempt time.
- Forecasts require `amount_unit` equal to currency, `share_unit=shares` and unit scales of one. A model cut off before the triggering disclosure remains `pending_model_refresh`. Exact prior model references feed assumption comparisons; an unavailable/mismatched baseline remains a gap.
- The isolated subscription backend does not import legacy settings or load repository `.env`. Existing immutable local candidates are verified/reused before requesting more subscription capacity.
- Late/revised sources get a new revision task, with metadata-only discovery linked to its resolved original. Full-source retrieval failures stay pending. Missing API pricing, OneDrive consent or model assumptions cannot count as complete research.
- Human edits to review fields are preserved. A changed formal model baseline triggers conflict. Local worker never writes formal pointers or mutates cloud task checkpoints.
- Snapshot: `python -m research.cli backup --write --source COMPANY_VERSION_DIR --output OUTSIDE_ONEDRIVE.zip`. Restore only to a new folder with `restore --source SNAPSHOT.zip --output NEW_DIR`. Keep at least one independent copy outside the synchronized source tree.

## Acceptance and remaining rollout work

Live Notion pilot has verified task/status read-back, task/report idempotency and preservation of a manual review state using synthetic data; that is not real-company research acceptance. Personal OneDrive delegated authorization, renewal, checksum read-back, ETag protection and staged final moves have now been exercised against the real account using isolated synthetic data, including interruption/retry, lost move responses and a concurrent target-name conflict. These checks do not certify cloud deployment or long-term reliability. Required next checks: cloud credential provisioning; confirmed issuer registry and source/pricing configuration; a source-backed financial baseline/assumption set; real company end-to-end research; independent backup recovery; and at least 14 observed pilot days for 3–5 companies covering earnings/incremental/no-change work. `research.release.trial_readiness` excludes synthetic or unverified runs.

Do not merge/enable the pilot as a substitute for acceptance. Do not retire the server: source adapters, provider routing, local subscription runtime and other services may still depend on it. Full Active expansion, financial-sector/cross-market adapters, longer observation and dependency retirement remain explicitly unaccepted work.
## Reviewable forecast workbooks

The opt-in runner delivers a versioned `.xlsx` beside the candidate model JSON under `03_财务模型/候选/`, with a separate verified checkpoint and a link in the Notion candidate. Baseline source, all three explicit scenarios, linked statements, cash/balance checks, funding shortfalls and assumption changes remain visible. Missing assumptions leave forecast outputs blank. This currently supports industrial annual models only.

Use `python -m financials.review_cli export --model PRIVATE_MODEL_JSON --output PRIVATE_REVIEW_XLSX`, then edit only the Value and Source columns in Assumptions. `python -m financials.review_cli import --model PRIVATE_ORIGINAL_MODEL_JSON --workbook PRIVATE_REVIEW_XLSX --output PRIVATE_NEW_INPUTS_JSON` accepts only literal assumption values with explicit sources against that exact original version. It rejects baseline/identity changes, duplicate rows and formula inputs, ignores edited output cells, and rebuilds with `human_review=pending`. The returned JSON has the baseline/scenarios/as_of structure expected by the explicit forecast-input configuration. Importing is not approval or automatic cloud deployment. Outputs are exclusive, mode 0600 and outside Git. Equal numeric values retain the original representation (Excel's `0.0` to `0` serialization does not create a spurious model revision). Numeric or provenance edits still create new versions; no rounding tolerance is introduced. Native Excel recalculation is now verified on a synthetic industrial fixture; actual-company financial acceptance remains separate.

## Persistent questions and conditional actions

`notion-setup` now adds a third database, **关注事项与行动条件**, inside the same dedicated pilot workspace. Configure its data-source ID as `RESEARCH_NOTION_FOLLOWUPS_SOURCE_ID` in the private configuration and, before eventual cloud enablement, in the pilot GitHub environment. Existing report/Universe databases are not changed.

Each candidate creates issuer-scoped questions and conditional research actions linked to the originating report. Repeated questions reuse the same key. The runner never changes State, Due, HumanNotes or Summary on existing records, never reopens completed/shelved items and never executes an action. Human corrections belong in Summary/HumanNotes; Payload/Key/Issuer retain machine identity and provenance. Existing records retain their original report and due date; revised dates are a human decision. Unresolved items enter subsequent research as prior analysis, not primary facts; due dates influence the weekly screen.

Delivery checkpoints each item separately. Lost creation responses are reconciled by key; if no record is visible after an uncertain creation, the task remains `pending_followup_confirmation` and only looks up again. Do not blindly reset its attempted key or create another record. Confirm the missing outcome manually before any deliberate retry. A missing dedicated source blocks the new worker before analysis. No paid calls are made by this register.

During interrupted delivery, context/model snapshots are frozen until that publication is reconciled. Human corrections remain stored and are incorporated in the next independent research publication. A caller attempting to substitute new inputs while a delivery journal is outstanding receives `pending_input_reconciliation`; previous analysis, spend and uncertain-create records are preserved. Do not reset these journals to force a fresh call.

2026-10-28 follow-up acceptance: 334 offline regressions passed. Five standalone real Notion synthetic checks passed, followed by seven combined online Graph/Notion checks: workbook interruption/retry, workbook checksum/idempotency, report lost response, follow-up lost response, complete delivery, manual state preservation and next-task question carry-forward. No model API was invoked. The earlier ProxyError did not recur in the successful diagnostic and combined runs; no proxy/TLS setting was changed, and its underlying cause is not established.

Installed Microsoft Excel on macOS verified five synthetic cases by editing and saving the workbook, then independently reading the saved recalculated values: 20% growth, blank required value, blank required source, explicit zero growth and restoration of original inputs. Historical baseline values stayed unchanged. Reimporting the restored native workbook now preserves its exact original model version, with human review still pending. A newly reproduced numeric-serialization defect was fixed RED→GREEN and independently reviewed with no findings. These are functional checks, not a real-company or 14-day reliability trial. Production is still off; deferred API prices, dedicated cloud credentials, confirmed mappings and human financial assumptions remain required.



## Broker originals and sampled expectations

See [broker-evidence.md](broker-evidence.md) for explicit FX search/acquisition, immutable PDF provenance, entitlement reconciliation and the broker-enabled quality contract. Existing scheduled workflows remain unchanged.
# v17 通用工作流（独立开发，默认关闭）

入口为 `python -m research.v17`，独立开关 `RESEARCH_V17_ENABLED=true` 加显式 `--write` 才允许读云端和写队列。旧每日简报、周度财务更新和原研究试点入口不随 v17 启用；旧研究 worker 会跳过 v17 任务及控制记录。

运行顺序为 `scan --write` → `worker --write --limit 3`；`weekly --write` 为全部已确认 Active 创建轻筛，资料和到期问题均无变化时不调用模型。有变化或问题到期则转订阅深研，云端保存阶段请求；在个人电脑执行 `local --write` 只提交订阅候选，再由单一云端 publisher 校验和交付。电脑关机时的业绩／重大公告走预算内云端 API，不等待本地订阅。

自然日发现及周度扫描在独立 `research_discovery.yml` 中，只有主分支和 v17 开关都满足才执行。与原研究试点共用单一 publisher 并发组，避免 Graph token 轮换和同一公司发布冲突。不得将本地订阅凭据放入 Actions。

公司配置沿用私有 issuer registry，增加 `research_profile`，例如以下虚构配置：

```json
{
  "issuer_id":"example-issuer",
  "identity_confirmed":true,
  "tickers":["000000.SZ"],
  "company_path":"投资/示例行业/示例公司",
  "research_profile":{
    "research_question":"经营变化是否改善可持续回报？",
    "sector_template":"industrial",
    "fiscal_year_end":"12-31",
    "sources":["official"],
    "company_aliases":["示例公司","Example Company"],
    "max_broker_reports":5,
    "evidence_terms":["现金转换","客户集中度"],
    "broker_queries":["示例公司 盈利预测"]
  }
}
```

Status＝Active 来自实时 Notion roster，不能靠上面的虚构配置自行创建持仓。未确认映射、未设置核心问题或不支持的行业模板均明确保留缺口。当前经过验证的联动模板是 **12 月结算的 industrial**；银行及其他业务或非自然年模板不自动套用，不能宣称全行业已覆盖。

`context_files`、`context_pages` 和已映射 `financial_workbook_path` 会读取对应公司 OneDrive／Notion 历史资料，保持为历史分析或带缺口的缓存财务。不得把旧研究当成新事实；正式基线仍由 `research-current.json` 及人工采纳状态控制。所有模型假设显式标为研究候选。

正式披露、政策与外部信息使用原有官方来源适配器。政策／行业／交易信息通过 `RESEARCH_OFFICIAL_FEEDS_JSON` 明确配置 issuer exposure 和分类；RSS 摘要不能当原文。无相应来源配置就不宣称该类信息已全面扫描。发现失败不推进该来源水位。首次补扫与历史三表归档分开；单轮新增原件有边界，未扫完保留 partial，后续复用已缓存原件继续。

发现报告须另外开启 `RESEARCH_BROKER_SOURCES_ENABLED` 并配置研究专用 `FXBAOGAO_API_KEY`。每次最多使用六份原文、每轮最多六个查询、最多两轮补证。原文要通过封面主体／代码匹配；异常副本保存在“待核验”，搜索命中和译文不能直接当原文。资料扣额与模型预算分别记录，未知下载结果不盲目重试。

模型 API 计价未知时保留 `waiting_budget`，不调用付费模型；额度仍为单次 2／每日 10／每月 100 元。本地订阅仅供可等待任务。阶段请求、回复、原件、模型 JSON／Excel 及结果都按版本归档；交付失败重用已生成结果，避免重付模型费用。

Notion 正文显示持有逻辑、差异、三表关键数、EPS／情景估值、反证及行动条件。同一持有逻辑沿用稳定 ID；本次缺少证据而未复核的逻辑继续跟踪，不视为确认或撤销。AI 研究版本与人工采纳版本分开：发布回读成功才推进自动研究状态，正式模型和人工持有逻辑不被自动改写。部分研究不能标成完整／已审核报告。

A 股标准基线的长期借款如在数据接口留空，仅在匹配同公司同期间的合并年报、原表该栏明确空白，且其他非流动负债与已核对小计完整勾稽时，才给模型使用可追溯的零值。历史三表原始空值不改写。

上线仍需真实样本、跨公司／跨期／事件验证、已确认 Active 覆盖清单、实际计价与云端权限检查，以及 3–5 家连续 14 天试跑。离线回归和单份报告不是该运行验收。开发期间不要打开生产开关。

