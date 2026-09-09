# Cyber Netizen Bot

以自研 Agent 认知运行时为核心的“赛博网友”项目。Web 首发形态是统一管理后台，其中包含内部全功能对话工作台；后续 IM 平台通过统一 Channel Adapter 接入。

当前处于 P0 工程基线阶段。最新开发计划见 [`docs/plans/2026-09-09-v2.md`](docs/plans/2026-09-09-v2.md)。

## 已建立的能力

- `uv workspace` Python Monorepo。
- FastAPI API 骨架及健康、系统概览、配置注册表接口。
- 自研 Domain、Cognition、Application、Infrastructure、Contracts、Adapters 包边界。
- Dramatiq Worker 骨架。
- React 管理后台骨架，包含总览、配置中心、内部对话和其他管理入口。
- PostgreSQL/pgvector、Redis、MinIO 本地基础设施定义。
- Alembic 初始配置与审计表迁移。
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

当前配置中心已实现只读 Schema 纵向切片；草稿、发布、回滚和密钥存储将在后续阶段实现，界面不会把未完成能力伪装成可用。

## GitHub

本目录已经初始化为本地 `main` Git 仓库。创建远程私有仓库后执行：

```powershell
git remote add origin <repository-url>
git push -u origin main
```

密钥只保存在本地 `.env` 或 GitHub Environments/Secrets 中，不得提交。

