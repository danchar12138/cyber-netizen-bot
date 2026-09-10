# Cyber Netizen Bot

以自研 Agent 认知运行时为核心的“赛博网友”项目。Web 首发形态是统一管理后台，其中包含内部全功能对话工作台；后续 IM 平台通过统一 Channel Adapter 接入。

当前已完成 P1 至 P7，包括身份安全、数据生命周期、可观测性/性能/成本、拟人评测，以及生产镜像与发布供应链。P0 的生产式 Docker Compose、全链迁移、服务闭环和隔离备份恢复也已由 Linux CI 实跑通过；仅 GitHub `main` 分支保护因私有仓库当前套餐限制而待处理。最新开发计划见 [`docs/plans/2026-09-09-v3.md`](docs/plans/2026-09-09-v3.md)。

## 已建立的能力

- `uv workspace` Python Monorepo。
- FastAPI API 骨架及存活检查、深度就绪检查、系统概览接口。
- 自研 Domain、Cognition、Application、Infrastructure、Contracts、Adapters 包边界。
- Dramatiq Worker 骨架。
- React 管理后台骨架，包含总览、配置中心、内部对话和其他管理入口。
- 系统启动设置安全摘要、用户身份、会话消息详情、任务基础状态、多标签页同步与手机响应式页面。
- PostgreSQL/pgvector、Redis、MinIO 本地基础设施定义，以及 MinIO 私有桶幂等初始化。
- 配置注册表、全作用域编辑、不可变草稿、发布前重校验、安全差异、版本历史、回滚、安全配置包导入/导出和逐项生效来源。
- AES-256-GCM 自托管密钥存储，以及只返回掩码的写入、轮换、完整性测试、清除和审计接口。
- 服务端 `admin`、`operator`、`viewer` 最小权限矩阵、开发管理会话和可搜索的访问控制页面。
- Agent/用户租户隔离列表、带确认的批量启停、实时总览计数和只追加审计查询页面。
- 统一错误 Envelope、请求追踪 ID、稳定开发身份与游标分页。
- 从 FastAPI 确定性导出 OpenAPI 3.1 契约，并通过锁定版本的 Hey API 生成 Fetch SDK 与 TypeScript 类型；Web 标准 HTTP 调用均由生成 SDK 承载，访问令牌只驻留内存，CI 会拦截契约或客户端漂移。
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
- PostgreSQL 真相源、事务 Inbox/Outbox、Dramatiq 至少一次投递、数据库租约、指数退避、死信、取消、恢复和安全重放组成的可靠异步任务闭环。
- Episode 巩固、记忆提取、embedding 重建、关系更新与对话后 Reflection 异步处理，以及按安静时段、活跃度、关系边界和每日社交预算治理的主动行为。
- 任务、尝试、Worker 心跳、死信与定时行为管理 API 和后台；主动行为默认关闭，所有运行参数均由配置中心校验、发布和回滚。
- 厂商无关的多模态内容块与 `ChannelAdapter` Protocol，以及能力协商、长文本拆分、流式缓冲、Markdown/附件透明降级和结构化安全错误。
- 正式内部 Web Adapter、飞书/Discord/Telegram 零外部副作用占位包，以及渠道能力与模型能力矩阵、平台模拟器和 Adapter 契约测试。
- 租户隔离的渠道实例、信封加密凭证、启停、连接测试、健康状态、原子限流、幂等发送和不含正文/密钥的诊断事件管理 API 与后台。
- MinIO 官方 Python SDK 附件适配器、预签名浏览器直传、服务端摘要复核、私有预览和生命周期清理。
- OIDC Authorization Code + PKCE 正式认证、本地稳定身份绑定、服务端 RBAC、会话撤销，以及 Prompt 注入和上传内容安全加固。
- 数据白名单 JSON 导出、带精确确认的用户遗忘、保留期清理、MinIO 孤儿保护与清理、隔离恢复演练登记和完整管理后台。
- OpenTelemetry OTLP Trace、SQLAlchemy 与模型调用链路、路由模板请求指标、API/Agent SLO、数据库队列积压、模型 Token 与调用时冻结成本、确定性阈值告警和可观测管理页面。
- Locust 分层性能场景、可配置失败率/P95 质量门，以及 PostgreSQL、Redis、MinIO、模型服务、队列、成本和 OTLP 故障处置手册。
- 版本化拟人评测集、当前认知/模型真实回放、确定性自动质量门、冻结版本/Token/成本，以及来源随机化的匿名 A/B 双侧多维评分和聚合报告。
- API、Worker、Web 非 root 多阶段生产镜像，同源 HTTP/WebSocket 反向代理，以及只读文件系统、最小权限和健康探针生产 Compose 覆盖层。
- 生产式 Compose 自动验收：全链 Alembic 迁移、三服务深度健康检查、PostgreSQL/MinIO 合成备份与隔离恢复、完整性比对和恢复后 API 冒烟。
- Python/Node 依赖审计、Hadolint、Trivy 镜像门禁、SPDX SBOM、GitHub provenance/SBOM attestation、Cosign OIDC 无密钥签名和多架构 GHCR 发布。
- Markdown/GFM 消息、自动保存草稿、图片/文件选择、键盘跳转和 axe 无障碍回归。
- Alembic 配置、对话、附件、加密密钥、审计、认知运行、长期记忆、可靠异步任务、渠道控制平面、OIDC、数据生命周期、可观测成本和拟人评测迁移，当前 head 为 `20260910_0014`。
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
corepack pnpm api:generate
uv run poe check
corepack pnpm --filter @cnb/web check
uv run alembic heads
```

修改路由或 API 契约后必须先运行 `corepack pnpm api:generate`，并同时提交 [`apps/web/openapi.json`](apps/web/openapi.json) 与 `apps/web/src/api-client/generated/`。生成目录视为构建依赖，不得手工编辑；WebSocket、MinIO 预签名直传和需要读取下载响应头的业务封装保留在生成 SDK 之外。

生产式镜像构建、Compose 启动、GHCR 发布、SBOM 与签名验证见 [`docs/runbooks/release-supply-chain.md`](docs/runbooks/release-supply-chain.md)。当前 CI 会额外执行锁文件检查、Python workspace 包构建、生产依赖审计、三个镜像构建、Trivy 扫描、SBOM 生成，以及 `scripts/verify-infrastructure.ps1` 基础设施验收。验收会上传不含密钥、正文和对象键的 `infrastructure-acceptance-evidence`，用于证明迁移版本、服务就绪、PostgreSQL/MinIO 恢复完整性与恢复后 API 可用性。

隔离性能环境的冒烟压测：

```powershell
uv run python -X utf8 -m locust -f tests/performance/locustfile.py --headless -u 5 -r 2 -t 30s --host http://127.0.0.1:8000 --only-summary
```

## 配置边界

`.env` 只保存系统启动前必须知道的配置，例如数据库、Redis、MinIO 连接和配置加密主密钥。模型、人格、Prompt、记忆、渠道、工具、策略等运行配置将由配置注册表、数据库版本和管理后台维护。

配置中心已支持系统、租户、Agent、渠道和用户作用域编辑。普通运行配置使用完整不可变快照，可在发布前查看安全差异并重新按当前 Registry 校验；最终生效接口逐项返回来源作用域、作用域 ID 和版本。

任意配置版本均可导出为版本化 JSON 配置包；包内不含密钥、内部资源 ID 或操作者信息。导入文件限制为 2 MiB，服务端会重新校验格式、Schema、配置键、作用域和值约束，并且只创建新草稿，不会直接覆盖或发布当前生效配置。

密钥不进入普通配置版本。自托管 `SecretStore` 使用 `CNB_CONFIG_MASTER_KEY` 驱动的 AES-256-GCM 信封加密，管理 API 和 Web 只返回掩码、完整性状态和时间。主密钥必须是 Base64 编码的 32 字节随机值；`.env.example` 中的固定值只可用于本地开发，预发布和生产必须替换。例如可生成新值：

```powershell
uv run python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

## 内部对话

管理后台的内部对话页已经接通多阶段拟人认知纵向闭环。默认使用 `development/friendly-echo-v1`，无需外部密钥即可验证消息持久化、认知决策、流式增量、不回复、取消、心跳和断线恢复。在配置中心写入 `model.openai.api_key` 并发布 `model.chat.provider=openai` 与模型名称后，新 Agent Run 会按最终配置动态使用官方 Python SDK `Responses API`；也可在“模型与路由”中发布精确模型档案与用途路由。凭证无需也不允许通过模型环境变量维护。

“人格版本”“模型与路由”“Prompt 与上下文”“工具与策略”“评测实验室”和“运行轨迹”均已接通真实管理 API。认知资源只保存无密钥结构化定义；运行轨迹只返回阶段摘要、裁剪统计、行动候选、人格状态和模型尝试元数据。

## 拟人表现评测

“评测实验室”支持直接运行内置拟人安全基线，也可在后台创建、发布和选择不可变评测集版本。每次回放使用当前发布的认知资源和模型生成候选回答，冻结配置、Persona、Prompt、Policy、模型路由、Token 与估算成本，并用行动匹配、回复存在性和显式短语规则形成自动质量门。

可回复用例会进入匿名 A/B 队列。评审提交前只看到随机化的两侧回答，不会得到候选位置、模型或自动判定；服务端去盲后聚合人格一致、自然度、共情理解、边界尊重四维评分与总体胜/平/负。数据规范和操作流程见 [`docs/runbooks/anthropomorphic-evaluation.md`](docs/runbooks/anthropomorphic-evaluation.md)。

## 可观测性、成本与性能

管理后台“可观测性”页面展示 API 错误率和 P50/P95/P99、Agent Run 成功率与延迟、队列积压、按 Provider/模型聚合的 Token 与冻结估算成本，以及按已发布配置确定性计算的活动告警。SLO、告警阈值、成本预算和聚合窗口均在配置中心管理；模型单价属于版本化模型档案。

OpenTelemetry 的启用状态、服务名、OTLP endpoint/Header 和 Trace 采样率属于数据库可用前的启动配置。管理 API 只返回安全状态，不返回 Exporter Header；Trace 和指标禁止采集正文、完整 Prompt、隐藏推理、密钥、令牌和对象键。配置与故障处置见 [`docs/runbooks/observability-alerts.md`](docs/runbooks/observability-alerts.md)，压测方法见 [`docs/runbooks/performance-load-test.md`](docs/runbooks/performance-load-test.md)。

## 身份与数据生命周期

开发环境可继续使用请求头模拟内置角色；预发布和生产环境强制使用 OIDC Authorization Code + PKCE。访问令牌只在内存中使用，服务端按可信 issuer、签名、audience、tenant 和角色 claim 绑定本地身份与权限，API、WebSocket、导出和高风险管理命令均执行服务端鉴权。

管理后台“数据生命周期”页面可查看并修改生效策略入口、下载一次性白名单 JSON 导出、执行用户遗忘、运行保留期与 MinIO 孤儿清理，并登记隔离恢复演练。导出文件不在服务端落盘；安全运行证据不保存用户正文、Prompt、密钥、令牌、对象键或底层异常。实际备份与隔离恢复步骤见 [`docs/runbooks/backup-restore.md`](docs/runbooks/backup-restore.md)。

## 长期记忆与关系

管理后台的“记忆与关系”页面可创建、搜索、确认、争议、纠正和遗忘记忆，查看来源与冲突/替代链，维护关系事件与边界，管理 Episode，并执行可观测的 embedding 重建。相关召回数量、候选池、敏感级别、时间半衰期和五项混合权重都在配置中心定义、校验、发布和回滚，无需手改配置文件。

当前 `local-hash-v1` 是无需外部密钥、可重复回放的 256 维字符 n-gram Hash 基线。PostgreSQL + pgvector 是记忆真相源；Episode 巩固、反思、关系更新和 embedding 重建已经迁移到 Dramatiq 可恢复任务，Redis 丢失后可由 PostgreSQL Outbox 重新投递。

## 异步任务与主动行为

管理后台的“任务与主动行为”页面可查看任务状态、尝试记录与 Worker 心跳，取消未完成任务，确认重放失败或死信任务，并创建、查看和取消定时主动行为。任务正文禁止携带疑似密钥，错误摘要不会回显异常正文；重复投递由去重键、行锁、执行租约和幂等处理器吸收。

主动行为默认关闭。启用后仍会在执行时按最新配置重新检查评分阈值、安静时段、用户近期活跃度、关系边界和每日社交预算；正式 Web Channel Adapter 已位于这一确定性策略边界之后，外部 IM Adapter 仍为明确不访问平台 API 的占位实现。

## 渠道与多模态

管理后台的“渠道与适配器”页面可查看平台与模型能力矩阵，创建、启停渠道实例，安全写入或清除凭证，执行连接测试、幂等发送测试、能力降级模拟，并查看不含消息正文、文件名、凭证和远端原始响应的诊断事件。图片和常用文档只能通过已复核的附件 ID、媒体类型、大小与 SHA-256 元数据进入渠道边界；凭证状态展示只读取不可逆元数据，不触发解密。

## GitHub

代码托管在私有仓库 `danchar12138/cyber-netizen-bot`，默认分支为 `main`。

密钥只保存在本地 `.env` 或 GitHub Environments/Secrets 中，不得提交。
