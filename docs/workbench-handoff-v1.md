# Workbench → 持仓研究交接 v1.0（P1 接收端）

本版本只实现**接收、校验、加载为历史研究候选上下文**。Schema 为 `research/schemas/workbench-handoff-v1.schema.json`，解析器为 `research/handoff_v1.py`，脱敏合成样本位于 `tests/fixtures/handoff-v1/`。真实 2026-11-12 立讯回放底稿是旧式回构数据，**不是** v1.0 包；不可直接当作已采纳交接件。

## 配置与目录

在已确认 Active 的公司 profile 上显式提供：

```json
{"handoffs": [{"handoff_id": "demo-issuer-a-v1", "path": "投资/测试/issuer-a/04_研究底稿/Workbench交接/demo-issuer-a-v1/handoff.json", "sha256": "<handoff.json 原始字节的小写 SHA-256>"}]}
```

`handoff.json` 必须事先以不可变方式存入该公司目录，并在接收端可读。SHA-256 仅在 profile 引用（外部清单），不写进 JSON 自身；Graph Drive `read` 回读原始字节后校验。路径由 `company_path` 与 `handoff_id` 推导并严格比较；Graph shortcut 不允许无显式映射。每公司最多 10 件，每件最多 2 MB。缺省 `handoffs` 走原有历史资料入口，不影响旧任务。

交接内容按 `prior_research` 进入 coverage；原始结构化 JSON 保留在归档的 coverage 检查点，模型提示词只放研究问题、逻辑、监控、缺口和来源摘要，**不把旧预测作为当前披露事实/正式模型输入**。producer 的 `review_status=accepted` 仅记录为 `declared_review_status`，接收端仍设 `candidate`；本版本没有可信人工采纳记录写入路径。发现错误包时失败关闭，任务显示 `retryable_failure`，并在任务记录中保留 `last_error_type=HandoffError` 和 `last_error_code`（只含固定错误代号，不含正文）。原文读取异常仍按既有通用错误类型处理。

研究截点用冻结任务 `created_at` 日期零时 UTC，防止当日后补资料当作事前已知。若需要精确的盘中时分、时区或历史回构准入，后续任务须扩展任务本身的可信带时区 `knowledge_cutoff`，不能以抓取日期代替知识截点。原有 `context_files`/Notion 页面尚未全面按截点过滤，本次仅对新增 handoff 启用严格截点。

## 契约与迁移

- `1.x` 可增加未知可选字段，解析器保留原 JSON，提示词仅传受控字段；必填字段不可省略。
- `0.x`、`2.x` 等未知 major 拒绝为 `unsupported_schema`，原件不修改；旧式 `hypotheses/sources` 回放底稿需先人工核对知识截点、原文身份、修订和证据，构造**新 handoff_id**，填写缺失项或 `gaps`，再作为候选重新验收。不得把回构逻辑伪称历史人工采纳。
- 原文 `source_index` 中每条需 `source_id`、原始内容 SHA-256 `revision_id`、类型、发布时间、版本说明、页码/位置和地址；引用必须在包内解析。加载时尚未逐一读取附件并验证每个原文 SHA-256，也未核验跨盘授权与可访问性，属于 P2/P6 前置缺口。
- 预测金额是十进制字符串；`null` 表示缺失，`"0"` 表示已确认零值。来源验证与维度合法性不等于同口径预测比较；不同证券/币种/股类不可直接算差额，P3 再实现。

## 验收范围与下一步

本轮合成样本覆盖合法候选、无基线、多证券/币种、旧格式缺字段；`tests/test_research_handoff_v1.py` 覆盖公司身份、截点、原始字节摘要、引用、旧 schema 与接受状态隔离，另以 Draft 2020-12 验证器测试 Schema 及合成样本。运行时仍采用手写校验器，尚未使用 JSON Schema 引擎强制检查每个 1.x 可选字段；未知字段仅保存在归档包，不进入受控摘要。尚未实施 P2 历史引用放行/报告来源呈现、P3 同口径预测对比、P4 独立采纳状态、P5 真实模型全链路、P6 Workbench 自动导出及 P7 多公司运营。现有 `evidence_ids` 不接受历史交接 ID，故**不可声称已经实现有证据约束的历史判断引用**。
