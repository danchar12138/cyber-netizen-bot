# 赛博网友机器人（Cyber Netizen Bot）

以自研拟人智能体（Agent）认知运行时为核心的“赛博网友”项目。Web 首发形态是统一管理后台，其中包含内部全功能对话工作台；后续即时通讯平台通过统一渠道适配器接入。

当前已完成原始计划 P0 至 P7 的代码与基础设施范围，并持续增强到通用告警生命周期、处置审计、重放复核、安全历史导出、稳健异常基线、值班交接摘要，以及带人工确认护栏、反馈记录、质量统计和离线阈值校准的确定性告警处置建议。身份安全、数据生命周期、性能成本、拟人评测、生产镜像、发布供应链，以及 Telegram 安全收发与运营闭环均已落地；生产式 Docker Compose、全链迁移、服务闭环和隔离备份恢复已由 Linux CI 实跑通过。

最新开发计划见 [`docs/plans/2026-09-16-v50.md`](docs/plans/2026-09-16-v50.md)，版本总进度见 [`docs/plans/2026-09-16-总进度概览.md`](docs/plans/2026-09-16-总进度概览.md)。原始范围见 [`docs/plans/2026-09-09-v2.md`](docs/plans/2026-09-09-v2.md)，MinIO 唯一对象存储修订与 P0 至 P7 完成清单见 [`docs/plans/2026-09-09-v3.md`](docs/plans/2026-09-09-v3.md)。GitHub `main` 分支保护仍受私有仓库套餐能力限制，仓库内功能与质量门禁不受影响。

管理后台、OpenAPI 文档、运行配置说明和安全错误采用中文优先语境；字段名、operation ID、数据库枚举及必要的协议或品牌缩写保持稳定。产品界面统一使用“智能体、提示词、模型服务、渠道适配器、入站箱、任务进程”等名称。

## 架构决策

- [ADR 0001：采用模块化单体与自研认知核心](docs/adr/0001-模块化单体与自研认知核心.md)
- [ADR 0002：以数据库作为运行配置真相并隔离密钥](docs/adr/0002-数据库配置真相与密钥边界.md)
- [ADR 0003：使用 MinIO 作为唯一对象存储](docs/adr/0003-MinIO唯一对象存储.md)

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
- Agent/用户租户隔离列表、Agent 创建与已发布认知资源复制、重命名、全局 Agent 选择、带确认的批量启停、影响预览、归档、软删除保留期、实时总览计数和只追加审计查询页面。
- 会话、附件、记忆、认知资源、评测和定时行为按所选 Agent 隔离；标准 HTTP 请求通过 `X-CNB-Agent-ID`、WebSocket 通过 `agent_id` 查询参数传递选择，服务端统一复核 UUID、租户归属和启用状态。
- 统一错误 Envelope、请求追踪 ID、稳定开发身份与游标分页。
- 从 FastAPI 确定性导出 OpenAPI 3.1 契约，并通过锁定版本的 Hey API 生成 Fetch SDK 与 TypeScript 类型；Web 标准 HTTP 调用均由生成 SDK 承载，访问令牌只驻留内存，CI 会拦截契约或客户端漂移。
- 会话、消息、Agent Run、有序事件持久化，以及可取消、可断线恢复的 WebSocket 流式闭环。
- 租户隔离的 `message_parts` 有序内容块持久化；Markdown、文本、图片和文件元数据与消息正文投影、流式事件、编辑分支、数据导出及遗忘语义保持一致。
- 多模态模型输入闭环：按用户权限从 MinIO 私有桶受限读取并复核大小、MIME 与 SHA-256，提取 UTF-8 文本、JSON、CSV、PDF 和 DOCX 正文；OpenAI Responses API 使用原生图片/文件块，不支持相应能力的 Provider 自动进行安全文本降级。
- 厂商无关的 `ModelProvider` 契约、无需密钥的本地 Provider 和 OpenAI 官方 SDK `Responses API` 适配器。
- 自研拟人认知状态机：感知、上下文组装、Social Mind、结构化决策、确定性 Policy Gate 与表达器。
- 版本化 Persona 宪法/特质/风格、可半衰期衰减的情绪状态，以及带来源、优先级、必需项和 Token 预算的 Context Assembler。
- 长对话即时分层摘要：按冻结配置读取历史窗口，将较早消息确定性聚合并过滤疑似凭据，最近消息保持逐条上下文；摘要按不可信检索背景封装，Trace 只保留数量、层级与 Token 统计。
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
- 中文优先的高精度反思内核：区分稳定偏好、个人事实、重要经历、互动边界、普通提问、寒暄和疑似凭据；关系变化依据感谢、自我披露、纠错、边界与敌意信号进行有饱和度的校准。
- 记忆、来源、关系、Episode、召回试验和 embedding 重建管理后台；内部对话侧栏展示当前关系和本轮召回结果。
- PostgreSQL 真相源、事务 Inbox/Outbox、Dramatiq 至少一次投递、执行中主动续租的数据库租约、指数退避、死信、取消、恢复和安全重放组成的可靠异步任务闭环。
- Episode 巩固、记忆提取、embedding 重建、关系更新与对话后 Reflection 异步处理，以及按安静时段、活跃度、关系边界和每日社交预算治理的主动行为。
- 任务、尝试、Worker 心跳、死信与定时行为管理 API 和后台；主动行为默认关闭，所有运行参数均由配置中心校验、发布和回滚。
- 厂商无关的多模态内容块与 `ChannelAdapter` Protocol，以及能力协商、长文本拆分、流式缓冲、Markdown/附件透明降级和结构化安全错误。
- 正式内部 Web Adapter、Telegram Bot API 安全出站 Adapter、飞书/Discord 零外部副作用占位包，以及渠道能力与模型能力矩阵、平台模拟器和 Adapter 契约测试。
- 租户与 Agent 双重隔离的渠道实例、信封加密凭证、启停、连接测试、健康状态、原子限流、幂等发送和不含正文/密钥的诊断事件管理 API 与后台。
- 租户与 Agent 双重隔离的外部主体映射、会话/线程路由、版本化净化 Envelope 和 PostgreSQL 幂等 Inbox；管理诊断仅暴露 UUID、内容块类型/数量与 SHA-256 标识摘要。
- 入站 Worker 以确定性消息 ID 复用现有 Conversation 与认知运行时，安全吸收平台重试、Worker 重投和人工重放；渠道配置/密钥作用域、多模态附件复核及中断 Run 防重复输出已接通。
- MinIO 官方 Python SDK 附件适配器、预签名浏览器直传、服务端摘要复核、私有预览和生命周期清理。
- OIDC Authorization Code + PKCE 正式认证、本地稳定身份绑定、服务端 RBAC、会话撤销、临时停用、用户级原子分钟限流，以及保留可信 OIDC 基线的可到期手工角色覆盖。
- 数据白名单 JSON 导出、带精确确认的用户遗忘、保留期清理、MinIO 孤儿保护与清理、隔离恢复演练登记和完整管理后台。
- OpenTelemetry OTLP Trace、SQLAlchemy 与模型调用链路、路由模板请求指标、API/Agent SLO、数据库队列积压、模型 Token 与调用时冻结成本、确定性阈值告警和可观测管理页面。
- Locust 分层性能场景、可配置失败率/P95 质量门，以及 PostgreSQL、Redis、MinIO、模型服务、队列、成本和 OTLP 故障处置手册。
- 版本化拟人评测集、当前认知/模型真实回放、确定性自动质量门、冻结版本/Token/成本，以及来源随机化的匿名 A/B 双侧多维评分和聚合报告。
- 当前已发布 `chat.realizer` 路由内的多模型同源对比：一次冻结评测集、配置、人格、Prompt、策略、路由与事件时间，原子保存各候选运行，并在后台并列展示通过率、延迟、Token、成本和逐用例回答；候选上限由配置中心管理。
- API、Worker、Web 非 root 多阶段生产镜像，同源 HTTP/WebSocket 反向代理，以及只读文件系统、最小权限和健康探针生产 Compose 覆盖层。
- 生产式 Compose 自动验收：全链 Alembic 迁移、三服务深度健康检查、PostgreSQL/MinIO 合成备份与隔离恢复、完整性比对和恢复后 API 冒烟。
- Python/Node 依赖审计、Hadolint、Trivy 镜像门禁、SPDX SBOM、GitHub provenance/SBOM attestation、Cosign OIDC 无密钥签名和多架构 GHCR 发布。
- Markdown/GFM 消息、自动保存草稿、图片/文件选择、键盘跳转和 axe 无障碍回归。
- Alembic 配置、对话、附件、多模态消息块、加密密钥、审计、认知运行、长期记忆、可靠异步任务、渠道控制平面、OIDC、数据生命周期、可观测成本、拟人评测、多模型对比、多智能体管理、渠道归属、智能体生命周期、外部入站路由、用户访问策略与告警运营历史迁移，当前唯一 head 为 `20260916_0035`。
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

## 多 Agent 管理与隔离

管理后台顶部提供全局 Agent 选择器，选择会保存在浏览器本地并通过 `storage` 事件同步到其他标签页。“Agent 管理”支持创建空白 Agent，或复制同租户源 Agent 当前已发布的人格、Prompt、模型档案、模型路由、工具和策略；副本从版本 1 独立演进，不共享后续草稿或发布操作。租户内 Agent 名称大小写不敏感唯一。

单选 Agent 后可查看会话、Agent Run、认知版本、记忆、关系、评测、渠道和定时行为的影响计数，并直接重命名。生命周期采用“影响预览 → 逐字确认归档 → 逐字确认软删除”的三段式流程：归档会阻止新运行，停用所属渠道并取消待执行主动行为；软删除只记录删除时间与最早清理时间，保留天数由 `data.retention.deleted_agent_days` 在配置中心管理。管理 API 不提供直接物理删除；达到保留期后，只有管理员显式运行的数据保留期清理会先删除 MinIO 私有对象，再在数据库事务内清理 Agent 及关联记录。

生成的 HTTP 客户端会自动附带 `X-CNB-Agent-ID`，内部对话 WebSocket 使用 `agent_id` 查询参数。服务端不信任客户端选择，会再次确认 Agent ID 格式、当前租户归属和启用状态；未选择时保持开发身份的默认 Agent 兼容路径。已归档或已删除 Agent 对运行接口统一表现为不存在。会话列表与操作、附件、记忆、认知资源、评测、定时行为和相应前端缓存键均包含 Agent 边界，切换后不会复用上一 Agent 的作用域数据。

## 拟人表现评测

“评测实验室”支持直接运行内置拟人安全基线，也可在后台创建、发布和选择不可变评测集版本。每次回放使用当前发布的认知资源和模型生成候选回答，冻结配置、Persona、Prompt、Policy、模型路由、Token 与估算成本，并用行动匹配、回复存在性和显式短语规则形成自动质量门。

多模型同源对比只允许选择当前 Agent 已发布 `chat.realizer` 路由引用的模型档案，不接受任意 Provider 或模型名。一次实验中的所有候选共享评测用例与认知快照，并使用确定性一致的事件和会话标识；对比历史只返回无正文指标摘要，受评测权限保护的详情才返回完整候选回答。后台可直接勾选至少两个候选，查看通过率、总延迟、输入/输出 Token、估算成本和同一用例的并列回答；`evaluation.comparison.max_candidates` 可在配置中心按 Agent 发布和回滚。

可回复用例会进入匿名 A/B 队列。评审提交前只看到随机化的两侧回答，不会得到候选位置、模型或自动判定；服务端去盲后聚合人格一致、自然度、共情理解、边界尊重四维评分与总体胜/平/负。数据规范和操作流程见 [`docs/runbooks/anthropomorphic-evaluation.md`](docs/runbooks/anthropomorphic-evaluation.md)。

## 可观测性、成本与性能

管理后台“可观测性”页面展示 API 错误率和 P50/P95/P99、Agent Run 成功率与延迟、队列积压、按 Provider/模型聚合的 Token 与冻结估算成本，以及按已发布配置确定性计算的活动告警。告警处置建议只允许管理员人工采纳或驳回，驳回时可选择稳定的替代动作；质量概览按窗口聚合采纳率、采纳后状态、动作/来源分布与同窗重放事实，并提供基于生命周期聚合事实的候选阈值场景回放。回放仅是离线决策支持，不证明因果关系，不执行自动处置或自动调参。SLO、告警阈值、成本预算、建议阈值和聚合窗口均在配置中心管理；模型单价属于版本化模型档案。

OpenTelemetry 的启用状态、服务名、OTLP endpoint/Header 和 Trace 采样率属于数据库可用前的启动配置。管理 API 只返回安全状态，不返回 Exporter Header；Trace 和指标禁止采集正文、完整 Prompt、隐藏推理、密钥、令牌和对象键。配置与故障处置见 [`docs/runbooks/observability-alerts.md`](docs/runbooks/observability-alerts.md)，压测方法见 [`docs/runbooks/performance-load-test.md`](docs/runbooks/performance-load-test.md)。

## 身份与数据生命周期

开发环境可继续使用请求头模拟内置角色；预发布和生产环境强制使用 OIDC Authorization Code + PKCE。访问令牌只在内存中使用，服务端按可信 issuer、签名、audience、tenant 和角色 claim 绑定本地身份与权限，API、WebSocket、导出和高风险管理命令均执行服务端鉴权。

“用户与身份”页面聚合租户、可信角色、OIDC 绑定、管理会话与会话成员关系，并可设置每分钟请求预算和最长 365 天的临时停用。角色覆盖使用管理员专属权限、逐字确认和可选到期时间；后续 OIDC 登录始终更新可信角色基线，但不会冲掉仍有效的手工覆盖，覆盖撤销或到期后自动恢复最近可信角色。认证后的每个 HTTP/WebSocket 管理请求都会在权限判断前统一复核本地用户状态和访问预算，限流响应包含 `Retry-After`。

管理后台“数据生命周期”页面可查看并修改生效策略入口、下载一次性白名单 JSON 导出、执行用户遗忘、运行保留期与 MinIO 孤儿清理，并登记隔离恢复演练。导出文件不在服务端落盘；安全运行证据不保存用户正文、Prompt、密钥、令牌、对象键或底层异常。实际备份与隔离恢复步骤见 [`docs/runbooks/backup-restore.md`](docs/runbooks/backup-restore.md)。

## 长期记忆与关系

管理后台的“记忆与关系”页面可创建、搜索、确认、争议、纠正和遗忘记忆，查看来源与冲突/替代链，维护关系事件与边界，管理 Episode，并执行可观测的 embedding 重建。相关召回数量、候选池、敏感级别、时间半衰期和五项混合权重都在配置中心定义、校验、发布和回滚，无需手改配置文件。

当前 `local-hash-v1` 是无需外部密钥、可重复回放的 256 维字符 n-gram Hash 基线。PostgreSQL + pgvector 是记忆真相源；Episode 巩固、反思、关系更新和 embedding 重建已经迁移到 Dramatiq 可恢复任务，Redis 丢失后可由 PostgreSQL Outbox 重新投递。反思的记忆策略与关系变化步长均由配置中心按 Agent 管理；疑似凭据、普通提问和寒暄默认不写长期记忆。

## 异步任务与主动行为

管理后台的“任务与主动行为”页面可查看任务状态、尝试记录、反思安全决策与 Worker 心跳，取消未完成任务，确认重放失败或死信任务，并创建、查看和取消定时主动行为。反思任务只保存 Run、消息和作用域 UUID，Worker 执行时从 PostgreSQL 复核并读取来源；任务载荷不复制消息正文或运行配置值，错误与结果摘要也不回显正文。重复投递由去重键、行锁、执行租约和基于 Run ID 的确定性副作用 ID 吸收。

主动行为默认关闭。启用后仍会在执行时按最新配置重新检查评分阈值、安静时段、用户近期活跃度、关系边界和每日社交预算；正式 Web Channel Adapter 与 Telegram 安全出站 Adapter 已位于这一确定性策略边界之后，飞书和 Discord 仍为明确不访问平台 API 的占位实现。

## 渠道与多模态

管理后台的“渠道与适配器”页面可查看平台与模型能力矩阵，创建、启停渠道实例，安全写入或清除凭证，执行连接测试、幂等发送测试、能力降级模拟，并查看不含消息正文、文件名、凭证和远端原始响应的诊断事件。每个渠道实例不可变地归属创建时所选 Agent；列表、详情、变更、凭证、收发模拟和诊断事件均由服务端按租户与 Agent 双重过滤，同租户不同 Agent 可使用相同渠道名称。图片和常用文档只能通过已复核的附件 ID、媒体类型、大小与 SHA-256 元数据进入渠道边界；凭证状态展示只读取不可逆元数据，不触发解密。

Telegram 已支持 Bot Token 连接测试、纯文本主动发送、Forum 话题回复、消息编辑和安全文本入站。管理后台可查询 Webhook 状态、注册 HTTPS Webhook、选择是否丢弃积压更新、清理 Webhook 并刷新探测；这些操作均要求相应权限和显式确认。Token 与渠道级 `telegram_webhook_secret` 均只进入信封加密存储；Adapter 固定访问官方 HTTPS 端点，并将限流、服务异常、网络失败、平台拒绝和非法响应转换为不含 Token、请求 URL、消息正文与远端描述的稳定错误。Markdown、附件与流式请求通过能力矩阵透明降级；入站仅接受普通用户的文本 `message` Update，覆盖私聊、群聊与 Forum 线程。管理后台“出站消息联调”可填写目标 chat ID、话题 ID 和编辑消息 ID；向外部渠道发送前会要求显式确认。

管理后台“渠道与适配器”页面还提供按当前 Agent 和时间窗聚合的运营指标：入站/出站事件、送达、透明降级、失败、限流、失败率和最近失败时间。指标接口 `GET /api/v1/channels/operations/metrics` 只返回聚合数值和时间戳，不返回消息正文、渠道凭证、完整 URL 或平台原始错误；指定其他 Agent 的渠道会被拒绝。失败率大于 0 时，管理员必须进入“任务与主动行为”页面逐项确认后显式重放失败任务，渠道页面不会自动重试或暴露远端错误原文。

内部对话的图片和文档会在模型调用前再次执行租户、用户、附件状态和内容完整性复核。单图片字节数、单文档字节数、请求附件总量、文档正文字符数和 PDF 提取页数均可在配置中心的“多模态”分区按系统、租户或 Agent 作用域发布和回滚，无需修改配置文件。对象键、二进制正文和提取正文不会进入 API、运行轨迹或模型调用错误。

## 外部身份与入站 Inbox

管理后台“外部身份与 Inbox”页面可绑定平台稳定主体 ID 与本地用户，并将平台会话/线程显式路由到内部 Conversation。标准化入站事件通过签名结果、时效和大小边界后，以渠道和外部消息 ID 的 SHA-256 组合幂等落库，再投递 `inbound` Worker 队列。所有运行设置位于配置中心，映射变更与重放受 RBAC 和审计保护。

`POST /api/v1/integrations/inbound/{channel_id}/simulate` 仍是内部 Web Adapter 管理联调入口。Telegram 真实入站使用公开的 `POST /api/v1/webhooks/telegram/{channel_id}`：部署侧将该 URL 和渠道级 `telegram_webhook_secret` 注册到 Telegram Bot API 的 `secret_token` 后，平台必须在 `X-Telegram-Bot-Api-Secret-Token` 请求头携带对应值。该端点不要求管理登录，只接受受限大小的 JSON 文本 Update，验证外部身份和会话/线程映射后返回 `204`，不回显正文、密钥或内部 Inbox/任务 ID。Worker 会严格解析净化后的 Envelope，以租户、渠道和外部消息 ID 生成确定性 `client_message_id`，复用现有 Conversation、长期记忆、多模态和自研认知运行时创建并处理 Agent Run；仅对已完成且有正文的 Agent Run 自动回复 Telegram，收件人和 Forum 线程沿用入站映射，出站使用稳定幂等键，因此平台重试、Worker 重投和人工重放不会重复生成消息、Run 或外部回复。管理后台可查看内部消息/Run ID、终态和幂等结果，但不展示正文、附件对象键、凭证或模型原始响应。飞书和 Discord 仍然是零外部副作用占位，不访问平台 API。

## GitHub

代码托管在私有仓库 `danchar12138/cyber-netizen-bot`，默认分支为 `main`。

密钥只保存在本地 `.env` 或 GitHub Environments/Secrets 中，不得提交。
