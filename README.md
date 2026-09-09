# Cyber Netizen Bot

以自研 Agent 认知运行时为核心的“赛博网友”项目。Web 首发形态是统一管理后台，其中包含内部全功能对话工作台；后续 IM 平台通过统一 Channel Adapter 接入。

当前已完成 P1、P2、P3、P4，下一阶段是 P5 异步反思与主动行为；P0 的代码基线已具备，Docker 实跑与分支保护仍待环境验证。最新开发计划见 [`docs/plans/2026-09-09-v3.md`](docs/plans/2026-09-09-v3.md)。

## 已建立的能力

- `uv workspace` Python Monorepo。
- FastAPI API 骨架及存活检查、深度就绪检查、系统概览接口。
- 自研 Domain、Cognition、Application、Infrastructure、Contracts、Adapters 包边界。
- Dramatiq Worker 骨架。
- React 管理后台骨架，包含总览、配置中心、内部对话和其他管理入口。
- 系统启动设置安全摘要、用户身份、会话消息详情、任务基础状态、多标签页同步与手机响应式页面。
- PostgreSQL/pgvector、Redis、MinIO 本地基础设施定义，以及 MinIO 私有桶幂等初始化。
- 配置注册表、全作用域编辑、不可变草稿、发布前重校验、安全差异、版本历史、回滚和逐项生效来源。
- AES-256-GCM 自托管密钥存储，以及只返回掩码的写入、轮换、完整性测试、清除和审计接口。
- 服务端 `admin`、`operator`、`viewer` 最小权限矩阵、开发管理会话和可搜索的访问控制页面。
- Agent/用户租户隔离列表、带确认的批量启停、实时总览计数和只追加审计查询页面。
- 统一错误 Envelope、请求追踪 ID、稳定开发身份与游标分页。
- 会话、消息、Agent Run、有序事件持久化，以及可取消、可断线恢复的 WebSocket 流式闭环。
- 厂商无关的 `ModelProvider` 契约、无需密钥的本地 Provider 和 OpenAI 官方 SDK `Responses API` 适配器。
- 自研拟人认知状态机：感知、上下文组装、Social Mind、结构化决策、确定性 Policy Gate 与表达器。
- 版本化 Persona 宪法/特质/风格、可半衰期衰减的情绪状态，以及带来源、优先级、必需项和 Token 预算的 Context Assembler。
- `reply`、`ask`、`wait`、`no_reply`、`tool` 行动模型；`no_reply` 会落为不可见的 `suppressed` 消息，不调用模型生成伪回复。
- 人格、Prompt、模型档案、用途路由、工具与策略的不可变草稿、离线测试、原子发布、历史回滚和管理后台。
- Agent Run 精确冻结配置、人格、Prompt、策略和模型路由版本，历史执行不会漂移到新发布版本。
- Tool 类型化契约及权限、网络 allowlist、风险、副作用、调用次数、成本和审批策略门；当前阶段不执行真实外部工具。
- 模型用途路由、总 Token 预算、超时、有限尝试、流式输出后禁止重试、进程内熔断和安全降级，并持久化每次尝试的无正文元数据。
- 不含隐藏思维链、完整 Prompt 和消息正文的认知阶段轨迹，以及人格一致性、自然追问、关系边界、不回复和副作用拒绝回放评测。
- 工作、情景、语义、关系、自传和程序性六类长期记忆，以及用户、Agent、租户三级可见范围和四级敏感边界。
- 可追溯来源、逐字证据标记、确认/争议、不可变纠正版本、冲突关系与正文/摘录/向量同步遗忘。
- PostgreSQL 全文、`pg_trgm`、pgvector、时间衰减、重要性、置信度和关系连续性的确定性混合召回；当前内置 `local-hash-v1` 256 维编码基线。
- 关系阶段、亲和度、信任度、熟悉度、交互次数、安全摘要与边界的只追加事件演进。
- 对话运行时长期记忆和关系接入，以及只记录记忆 ID、分数、版本和关系版本的 `memory_recall` 安全轨迹阶段。
- 记忆、来源、关系、Episode、召回试验和 embedding 重建管理后台；内部对话侧栏展示当前关系和本轮召回结果。
- MinIO 官方 Python SDK 附件适配器、预签名浏览器直传、服务端摘要复核、私有预览和生命周期清理。
- Markdown/GFM 消息、自动保存草稿、图片/文件选择、键盘跳转和 axe 无障碍回归。
- Alembic 配置、对话、附件、加密密钥、审计、认知运行和长期记忆迁移，当前 head 为 `20260910_0008`。
- Python/Web 测试、静态检查和 GitHub Actions。

## 环境要求

- `uv >= 0.12`
- Node.js 24+ 与 Corepack
- Docker Desktop 或兼容 Docker Compose 环境

项目不依赖系统 Python；`uv` 会根据 `.python-version` 使用 Python 3.12。

## 首次启动

```powershell
Copy-Item .env.example .env
uv sync --frozen
corepack pnpm install --frozen-lockfile
docker compose up -d
uv run alembic upgrade head
```

分别启动 API、Worker 和 Web：

```powershell
uv run poe dev-api
uv run poe dev-worker
corepack pnpm --filter @cnb/web dev
```

默认地址：

- 管理后台：http://localhost:5173
- API：http://localhost:8000
- OpenAPI：http://localhost:8000/docs
- MinIO Console：http://localhost:9001

MinIO 是项目唯一的附件与大对象存储。应用使用 `CNB_MINIO_ENDPOINT_URL`、`CNB_MINIO_ACCESS_KEY`、`CNB_MINIO_SECRET_KEY` 和 `CNB_MINIO_BUCKET` 启动设置；Compose 中的 `minio-init` 会在服务健康后幂等创建私有桶。浏览器上传来源通过 `MINIO_API_CORS_ALLOW_ORIGIN` 配置，默认允许本地 Web 开发地址。

## 常用检查

```powershell
uv run poe check
corepack pnpm --filter @cnb/web check
uv run alembic heads
```

## 配置边界

`.env` 只保存系统启动前必须知道的配置，例如数据库、Redis、MinIO 连接和配置加密主密钥。模型、人格、Prompt、记忆、渠道、工具、策略等运行配置将由配置注册表、数据库版本和管理后台维护。

配置中心已支持系统、租户、Agent、渠道和用户作用域编辑。普通运行配置使用完整不可变快照，可在发布前查看安全差异并重新按当前 Registry 校验；最终生效接口逐项返回来源作用域、作用域 ID 和版本。

密钥不进入普通配置版本。自托管 `SecretStore` 使用 `CNB_CONFIG_MASTER_KEY` 驱动的 AES-256-GCM 信封加密，管理 API 和 Web 只返回掩码、完整性状态和时间。主密钥必须是 Base64 编码的 32 字节随机值；`.env.example` 中的固定值只可用于本地开发，预发布和生产必须替换。例如可生成新值：

```powershell
uv run python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

## 内部对话

管理后台的内部对话页已经接通多阶段拟人认知纵向闭环。默认使用 `development/friendly-echo-v1`，无需外部密钥即可验证消息持久化、认知决策、流式增量、不回复、取消、心跳和断线恢复。在配置中心写入 `model.openai.api_key` 并发布 `model.chat.provider=openai` 与模型名称后，新 Agent Run 会按最终配置动态使用官方 Python SDK `Responses API`；也可在“模型与路由”中发布精确模型档案与用途路由。凭证无需也不允许通过模型环境变量维护。

“人格版本”“模型与路由”“Prompt 与上下文”“工具与策略”“评测实验室”和“运行轨迹”均已接通真实管理 API。认知资源只保存无密钥结构化定义；运行轨迹只返回阶段摘要、裁剪统计、行动候选、人格状态和模型尝试元数据。

## 长期记忆与关系

管理后台的“记忆与关系”页面可创建、搜索、确认、争议、纠正和遗忘记忆，查看来源与冲突/替代链，维护关系事件与边界，管理 Episode，并执行可观测的 embedding 重建。相关召回数量、候选池、敏感级别、时间半衰期和五项混合权重都在配置中心定义、校验、发布和回滚，无需手改配置文件。

当前 `local-hash-v1` 是无需外部密钥、可重复回放的 256 维字符 n-gram Hash 基线。PostgreSQL + pgvector 是记忆真相源；P5 会在保持相同应用端口的前提下，把 Episode 巩固、反思、关系更新和 embedding 重建迁移到 Dramatiq 可恢复任务。

## GitHub

代码托管在私有仓库 `danchar12138/cyber-netizen-bot`，默认分支为 `main`。

密钥只保存在本地 `.env` 或 GitHub Environments/Secrets 中，不得提交。
