# Cyber Netizen Bot

以自研 Agent 认知运行时为核心的“赛博网友”项目。Web 首发形态是统一管理后台，其中包含内部全功能对话工作台；后续 IM 平台通过统一 Channel Adapter 接入。

当前已建立 P0 工程基线，并提前打通配置中心的首个纵向闭环。最新开发计划见 [`docs/plans/2026-09-09-v2.md`](docs/plans/2026-09-09-v2.md)。

## 已建立的能力

- `uv workspace` Python Monorepo。
- FastAPI API 骨架及存活检查、深度就绪检查、系统概览接口。
- 自研 Domain、Cognition、Application、Infrastructure、Contracts、Adapters 包边界。
- Dramatiq Worker 骨架。
- React 管理后台骨架，包含总览、配置中心、内部对话和其他管理入口。
- PostgreSQL/pgvector、Redis、MinIO 本地基础设施定义。
- 配置注册表、不可变草稿、发布、版本历史、回滚和后台编辑页面。
- 统一错误 Envelope、请求追踪 ID、稳定开发身份与游标分页。
- 会话、消息、Agent Run、有序事件持久化，以及可取消、可断线恢复的 WebSocket 流式闭环。
- 厂商无关的 `ModelProvider` 契约、无需密钥的本地 Provider 和 OpenAI 官方 SDK `Responses API` 适配器。
- Alembic 初始配置、密钥引用与审计表迁移。
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

当前配置中心已实现 Schema、非密钥系统作用域编辑、不可变草稿、发布、版本历史、生成新版本式回滚，以及供 Agent Run 使用的最终生效值解析。差异预览、密钥存储和其他作用域的完整后台编辑仍在后续阶段实现，界面不会把未完成能力伪装成可用。

## 内部对话

管理后台的内部对话页已经接通最小纵向闭环。开发模式默认使用 `development/friendly-echo-v1`，无需外部密钥即可验证消息持久化、流式增量、取消、心跳和断线恢复。`OpenAIResponsesProvider` 已实现官方 Python SDK `Responses API` 的厂商适配层，但在密钥存储与 Provider 后台管理完成前不会默认启用，也不新增必须手改的模型环境变量。

## GitHub

代码托管在私有仓库 `danchar12138/cyber-netizen-bot`，默认分支为 `main`。

密钥只保存在本地 `.env` 或 GitHub Environments/Secrets 中，不得提交。
