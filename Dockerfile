# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.10 AS uv

FROM python:3.12.14-slim-bookworm AS python-dependencies

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/adapters/pyproject.toml packages/adapters/pyproject.toml
COPY packages/application/pyproject.toml packages/application/pyproject.toml
COPY packages/cognition/pyproject.toml packages/cognition/pyproject.toml
COPY packages/contracts/pyproject.toml packages/contracts/pyproject.toml
COPY packages/domain/pyproject.toml packages/domain/pyproject.toml
COPY packages/infrastructure/pyproject.toml packages/infrastructure/pyproject.toml

FROM python-dependencies AS api-builder

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-local --package cnb-api
COPY apps/api apps/api
COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package cnb-api

FROM python-dependencies AS worker-builder

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-local --package cnb-worker
COPY apps/worker apps/worker
COPY packages packages
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package cnb-worker

FROM python:3.12.14-slim-bookworm AS api

ARG VERSION=0.0.0-dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Cyber Netizen API" \
      org.opencontainers.image.description="赛博网友管理与消息网关" \
      org.opencontainers.image.source="https://github.com/danchar12138/cyber-netizen-bot" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=api-builder --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 migrations ./migrations

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).read()"]
CMD ["python", "-m", "uvicorn", "cnb_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

FROM python:3.12.14-slim-bookworm AS worker

ARG VERSION=0.0.0-dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Cyber Netizen Worker" \
      org.opencontainers.image.description="赛博网友可靠异步任务进程" \
      org.opencontainers.image.source="https://github.com/danchar12138/cyber-netizen-bot" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=worker-builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "from cnb_infrastructure import get_settings; from redis import Redis; client = Redis.from_url(get_settings().redis_url.get_secret_value(), socket_timeout=3); assert client.ping()"]
CMD ["python", "-m", "dramatiq", "cnb_worker.tasks", "--processes", "1", "--threads", "8"]

FROM node:26.8-alpine AS web-builder

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:${PATH}
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@12.3.4 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --filter @cnb/web...
COPY apps/web apps/web
RUN pnpm --filter @cnb/web build

FROM nginxinc/nginx-unprivileged:1.30.0-alpine AS web

USER root
# Alpine 安全修复可能早于 Nginx 镜像重建；Trivy 仍对升级后的最终层执行阻断扫描。
# hadolint ignore=DL3017
RUN apk upgrade --no-cache

ARG VERSION=0.0.0-dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Cyber Netizen Web" \
      org.opencontainers.image.description="赛博网友统一管理后台" \
      org.opencontainers.image.source="https://github.com/danchar12138/cyber-netizen-bot" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary"

ENV CNB_API_UPSTREAM=http://api:8000 \
    NGINX_ENVSUBST_FILTER=^CNB_API_UPSTREAM$ \
    NGINX_ENVSUBST_OUTPUT_DIR=/tmp
COPY deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=web-builder --chown=101:101 /app/apps/web/dist /usr/share/nginx/html

USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "--quiet", "--output-document=/dev/null", "http://127.0.0.1:8080/health/web"]
