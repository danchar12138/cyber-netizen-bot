# 可观测性、成本与告警处置手册

## 安全边界

OpenTelemetry Trace 和 PostgreSQL 指标只允许保存请求 ID、租户 ID、Agent Run ID、HTTP 方法、路由模板、状态码、耗时、Provider、模型名和错误分类。禁止写入消息正文、完整 Prompt、隐藏推理、Token/密钥、Authorization Header、查询参数、文件名和 MinIO 对象键。

API 请求指标使用路由模板而非原始 URL；模型成本在调用完成时按该模型档案版本中的 `pricing` 冻结为微美元，后续修改价格不会重算历史调用。

## 启用 OpenTelemetry

OpenTelemetry 是数据库连接前必须可用的启动链路，因此通过环境配置：

```powershell
$env:CNB_OTEL_ENABLED='true'
$env:CNB_OTEL_SERVICE_NAME='cyber-netizen-api'
$env:CNB_OTEL_EXPORTER_OTLP_ENDPOINT='https://collector.example.com/v1/traces'
$env:CNB_OTEL_EXPORTER_OTLP_HEADERS='Authorization=Bearer%20replace-me'
$env:CNB_OTEL_TRACE_SAMPLE_RATIO='0.1'
uv run poe dev-api
```

正式环境的远程 OTLP 地址必须使用 HTTPS，凭证放在 `CNB_OTEL_EXPORTER_OTLP_HEADERS`，管理 API 只返回 Exporter 是否配置，不返回地址 Header 或密钥。修改启动配置后需要重启 API。

SLO、队列阈值、成本预算与聚合窗口在“配置中心 → observability”修改、校验并发布，无需手改文件：

- `observability.window_minutes`
- `alerts.baseline.window_minutes`
- `alerts.baseline.periods`
- `alerts.baseline.sensitivity`
- `alerts.baseline.minimum_current_count`
- `alerts.recommendation.long_running_minutes`
- `alerts.recommendation.suppression_minutes`
- `alerts.recommendation.minimum_repeated_occurrences`
- `slo.api.maximum_error_rate_percent`
- `slo.api.maximum_p95_ms`
- `slo.agent.minimum_success_rate_percent`
- `slo.agent.maximum_p95_ms`
- `alerts.queue.maximum_backlog`
- `alerts.queue.maximum_oldest_wait_seconds`
- `alerts.model.maximum_failure_rate_percent`
- `cost.window_budget_usd`

模型单价在“模型与路由”的模型档案中维护：

```json
{
  "pricing": {
    "input_usd_per_million_tokens": 2.5,
    "output_usd_per_million_tokens": 10
  }
}
```

## 告警处置

### API 错误率或延迟

1. 在“可观测性”确认窗口、请求量、P95/P99 与活动告警，避免用极小样本判断事故。
2. 用请求 ID 在 Collector/日志中关联，但不要复制请求正文到工单。
3. 检查 PostgreSQL 连接池、慢查询、OIDC JWKS 与 MinIO/模型服务延迟。
4. 若刚发布运行配置，查看安全差异并回滚可疑版本；启动配置变更则回滚部署。
5. 恢复后持续观察至少一个完整聚合窗口。

### Agent Run 成功率或延迟

1. 按 Run ID 打开安全认知轨迹，确认失败分类、模型尝试和熔断状态。
2. 检查当前模型用途路由、超时、尝试次数、Token 预算和 Provider 状态。
3. Provider 故障时发布已验证的降级模型档案或路由；不要提高无界重试次数。
4. 确认流式输出后没有重试，防止用户收到重复内容。

### 队列积压或等待过久

1. 在“任务与主动行为”确认待处理、重试、死信和 Worker 心跳。
2. Redis 故障时先恢复 Broker，再由 PostgreSQL Outbox 恢复投递；不要直接改任务状态。
3. Worker 不健康时滚动重启 Worker，确认租约过期任务被安全接管。
4. 对死信只使用后台的带审计确认重放，并验证幂等键没有产生重复副作用。

### 通用告警来源下钻与通知重放复核

1. 在“来源健康对比”使用下钻按钮，将来源、级别、状态和持续时间筛选联动到生命周期列表；列表按稳定游标加载，切换筛选后从第一页重新查询。
2. 对通用告警通知死信发起重放后，在“通知重放复核运营”确认允许或阻止结论、稳定原因码、来源和源任务短 ID。
3. 有效抑制期间的重放必须被阻止且不创建新任务；需要恢复投递时先确认事故状态，再解除抑制或等待抑制到期后重新发起重放。
4. 复核历史和通用审计只保存来源键、操作者、原因码与抑制到期时间，不得向排障记录复制任务载荷、通知目标、Secret 或业务正文。

### 异常基线与值班交接

1. 在“配置中心 → observability”维护 `alerts.baseline.*` 四项配置；基线窗口同时是交接摘要窗口，历史周期数越大计算越稳定。
2. 异常信号以等长历史窗口的中位数和 MAD 估计开启量、升级量基线；单个历史尖峰不应抬高整体基线，但样本过少时也不应据此放宽告警阈值。
3. 交接时先处理“优先关注”中的严重、未确认和已升级项，再查看抑制将到期项、阻止重放数与来源汇总；使用下钻按钮联动生命周期筛选。
4. 摘要不返回处置备注、任务载荷、通知目标、Secret、Prompt、隐藏推理或业务正文；需要查看处置备注时必须进入单条生命周期的审计时间线。
5. 单次摘要最多精确处理 10000 条生命周期和 10000 条处置记录；超过任一上限会明确拒绝，不会静默截断。应缩短基线窗口或按保留策略清理历史后重试。

### 告警运营历史保留与导出

1. 在“配置中心 → 数据生命周期”维护 `data.retention.observability_disposition_event_days`、`data.retention.observability_replay_review_days` 和 `data.retention.observability_recommendation_feedback_days`，校验并发布后由数据生命周期页展示最终生效值。
2. “执行保留期清理”会按当前租户和单批上限分别删除到期处置事件、重放复核事件和建议反馈；运行证据中的三组清理计数和截止时间用于复核结果。
3. 在“数据生命周期 → 告警历史导出”选择窗口并下载。导出只覆盖当前 Agent，不在服务端落盘，并通过响应头返回 SHA-256 和运行记录 ID。
4. 安全导出使用 `cnb-observability-alert-history-v2` Schema，包含生命周期、去除自由文本的处置事件、重放复核事件和冻结建议反馈；不包含处置自由文本、任务载荷、通知目标、Secret、Prompt、隐藏推理或业务正文。
5. 超过记录数或字节上限时应调整配置或缩短窗口，不得绕过保护直接查询生产库。

### 告警处置建议与人工确认

1. “告警处置建议”只根据当前生命周期、严重级别、升级状态、持续时间、重复次数和稳健异常基线生成，不调用模型，也不读取业务正文。
2. 严重、升级、异常或持续过久的告警建议人工确认并调查；稳定但重复的普通警告可建议短时抑制；信号不足时只建议继续观察。
3. 在“配置中心 → observability”维护 `alerts.recommendation.*` 三项配置，以控制长时间阈值、建议抑制时长和最小重复次数，无需修改环境变量或配置文件。
4. 建议端点是只读接口，每项均返回“必须人工确认、禁止自动执行、仅限当前智能体范围”护栏。后台按钮只对具备通用告警处置权限的角色显示，并复用既有带审计处置接口。
5. 已确认或仍在有效抑制期的告警不会重复生成建议；建议结果不包含处置备注、任务载荷、通知目标、Secret、Prompt、隐藏推理或业务正文。
6. 管理员采纳可执行建议时，后台先调用带审计的人工处置接口，再提交 `accepted` 结论；采纳“继续观察”和提交 `rejected` 都不会触发处置。反馈请求只包含结论和显式确认，动作、优先级与原因码由服务端重算并冻结。
7. 同一生命周期只记录一条反馈：相同结论幂等返回，不同结论明确冲突。若处置成功但反馈暂时失败，页面重试只补交采纳反馈，不重复处置；重新载入页面前应先在审计时间线确认处置状态。
8. “建议质量概览”展示窗口内采纳率、采纳后当前状态、动作/来源分布及同窗重放允许/阻止数量。这些是可审计的相关事实，不证明建议导致恢复或重放结果，也不会自动修改任何阈值。

### 成本超出窗口预算

1. 按 Provider/模型核对调用数、Token 与冻结估算成本。
2. 检查是否出现失败重试、过大的上下文或输出上限、异常主动行为。
3. 通过配置中心降低 Token 预算、主动行为预算，或发布更合适的模型路由。
4. 单价录入错误时修正并发布新模型档案；历史冻结成本保留当时估算，不追溯改写。

### PostgreSQL、Redis、MinIO 或模型服务故障

- PostgreSQL：API 写路径和可观测聚合会降级；先恢复主库/连接池并检查 Alembic head，禁止绕过迁移写库。
- Redis：前台数据库事实仍保留；恢复 Redis 后重启 Worker 并确认 Outbox 补投。
- MinIO：暂停附件上传/预览，文本对话继续；检查私有桶、凭证与容量，禁止临时切换任何未实现、未验证的对象存储后端。
- 模型服务：观察熔断与降级路由；验证凭证状态，切换已发布模型档案，不在日志中输出厂商原始响应。
- OTLP Exporter：导出失败不得阻断业务请求；修复 Collector/网络/证书后观察批量导出恢复，禁止为排障打印 Header。

## 恢复确认

恢复结束必须记录事故时间、告警代码、受影响窗口、配置/部署版本、采取动作和无正文验证结果。确认 API 与 Agent SLO 回到阈值内、队列等待下降、成本增长符合预期，且 Trace 抽查不存在敏感字段。
