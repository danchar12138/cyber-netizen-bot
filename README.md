# Cyber Netizen Bot

以自研 Agent 认知运行时为核心的“赛博网友”项目。Web 首发形态是统一管理后台，其中包含内部全功能对话工作台；后续 IM 平台通过统一 Channel Adapter 接入。

当前已完成 P0、P1，并正在推进 P2 管理后台。最新开发计划见 [`docs/plans/2026-09-09-v2.md`](docs/plans/2026-09-09-v2.md)。

## 已建立的能力

- `uv workspace` Python Monorepo。
- FastAPI API 骨架及存活检查、深度就绪检查、系统概览接口。
- 自研 Domain、Cognition、Application、Infrastructure、Contracts、Adapters 包边界。
- Dramatiq Worker 骨架。
- React 管理后台骨架，包含总览、配置中心、内部对话和其他管理入口。
- PostgreSQL/pgvector、Redis、MinIO 本地基础设施定义。
- 配置注册表、全作用域编辑、不可变草稿、发布前重校验、安全差异、版本历史、回滚和逐项生效来源。
- AES-256-GCM 自托管密钥存储，以及只返回掩码的写入、轮换、完整性测试、清除和审计接口。
- 统一错误 Envelope、请求追踪 ID、稳定开发身份与游标分页。
- 会话、消息、Agent Run、有序事件持久化，以及可取消、可断线恢复的 WebSocket 流式闭环。
- 厂商无关的 `ModelProvider` 契约、无需密钥的本地 Provider 和 OpenAI 官方 SDK `Responses API` 适配器。
- Alembic 配置、对话、加密密钥与审计迁移。
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

## 常用检查

```powershell
uv run poe check
corepack pnpm --filter @cnb/web check
uv run alembic heads
```

## 配置边界

`.env` 只保存系统启动前必须知道的配置，例如数据库、Redis、对象存储连接和配置加密主密钥。模型、人格、Prompt、记忆、渠道、工具、策略等运行配置将由配置注册表、数据库版本和管理后台维护。

配置中心已支持系统、租户、Agent、渠道和用户作用域编辑。普通运行配置使用完整不可变快照，可在发布前查看安全差异并重新按当前 Registry 校验；最终生效接口逐项返回来源作用域、作用域 ID 和版本。

密钥不进入普通配置版本。自托管 `SecretStore` 使用 `CNB_CONFIG_MASTER_KEY` 驱动的 AES-256-GCM 信封加密，管理 API 和 Web 只返回掩码、完整性状态和时间。主密钥必须是 Base64 编码的 32 字节随机值；`.env.example` 中的固定值只可用于本地开发，预发布和生产必须替换。例如可生成新值：

```powershell
uv run python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

## 内部对话

管理后台的内部对话页已经接通最小纵向闭环。默认使用 `development/friendly-echo-v1`，无需外部密钥即可验证消息持久化、流式增量、取消、心跳和断线恢复。在配置中心写入 `model.openai.api_key` 并发布 `model.chat.provider=openai` 与模型名称后，新 Agent Run 会按最终配置动态使用官方 Python SDK `Responses API`；凭证无需也不允许通过模型环境变量维护。

## GitHub

代码托管在私有仓库 `danchar12138/cyber-netizen-bot`，默认分支为 `main`。

密钥只保存在本地 `.env` 或 GitHub Environments/Secrets 中，不得提交。
