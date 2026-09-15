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
