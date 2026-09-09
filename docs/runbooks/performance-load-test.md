# 性能压测与基线手册

## 目标与边界

Locust 场景覆盖存活检查、可观测聚合、会话列表和可选的内部对话发消息。发消息只使用明确标记的合成文本；不得把真实消息、Bearer Token、Cookie、密钥或完整响应正文写入报告。应在隔离的性能环境执行，禁止直接压测生产环境。

## 准备

1. 使用与目标发布一致的提交、Alembic head 和运行配置。
2. 准备独立租户和测试会话，清空无关后台任务并确认 Worker 心跳正常。
3. OIDC 环境把短期测试令牌仅放入当前进程环境变量；开发环境默认使用 `admin` 开发角色头。
4. 若要覆盖发送链路，设置专用会话 ID；未设置时场景自动跳过写入。

```powershell
$env:CNB_PERFORMANCE_CONVERSATION_ID='00000000-0000-0000-0000-000000000000'
# $env:CNB_PERFORMANCE_BEARER_TOKEN='short-lived-token'
```

## 冒烟与正式基线

先运行低并发冒烟：

```powershell
uv run python -X utf8 -m locust -f tests/performance/locustfile.py --headless -u 5 -r 2 -t 30s --host http://127.0.0.1:8000 --only-summary
```

再运行十分钟基线：

```powershell
uv run python -X utf8 -m locust -f tests/performance/locustfile.py --headless -u 50 -r 5 -t 10m --host http://127.0.0.1:8000 --only-summary
```

默认质量门是失败率不超过 1%、总体 P95 不超过 1000 ms 且请求数大于 0。可按已批准目标临时覆盖：

```powershell
$env:CNB_LOAD_MAX_FAILURE_RATE_PERCENT='0.5'
$env:CNB_LOAD_MAX_P95_MS='800'
```

需要保留 CSV 时只写到已忽略的 `tests/performance/results/`，确认不含凭证和正文后再将汇总数字转录到发布证据；原始临时文件按项目清理规则删除。不要提交 HTML 报告或测试令牌。

## 结果判读

- 分别查看每个命名路由的吞吐、失败率、P50/P95/P99，不只看总体平均值。
- 对话发送需同时检查 Agent Run 成功率、模型延迟、Token/成本、队列积压和重复消息数。
- 失败样本按错误分类和请求 ID 定位，禁止在共享材料中粘贴原始正文。
- 若未达到门槛，先定位数据库查询、连接池、模型路由或 Worker 饱和点，再以同样参数复测。
- 只有相同提交、基础设施规格、数据规模和运行配置下的结果可以直接比较。

本机没有 Docker 或隔离性能环境时，只能验证脚本、质量门和应用测试，不得把本地无基础设施结果登记为正式容量基线。
