# 生产镜像与发布供应链

本文说明 API、Worker、Web 三个生产镜像的构建、发布、验证与回滚方式。镜像只包含运行所需文件，均以非 root 用户启动；对象存储只支持 MinIO。

## 镜像与运行边界

| 镜像 | Docker target | 运行用户 | 健康检查 | 默认入口 |
| --- | --- | --- | --- | --- |
| API | `api` | `10001:10001` | `/health/live` | Uvicorn `cnb_api.main:app` |
| Worker | `worker` | `10001:10001` | Redis `PING` | Dramatiq `cnb_worker.tasks` |
| Web | `web` | `101:101` | `/health/web` | 非特权 Nginx，监听 `8080` |

Web 通过同源 `/api/` 代理 HTTP 与 WebSocket 请求，`CNB_API_UPSTREAM` 只用于容器启动时生成 Nginx 配置，不进入前端构建产物。默认值为 `http://api:8000`。API 的深度就绪探针是 `/health/ready`，部署编排器应将其用于 readiness，而不是 liveness。

生产 Compose 对应用容器启用只读根文件系统、`no-new-privileges`、丢弃全部 Linux capabilities 和受限 `/tmp`。PostgreSQL、Redis 与 MinIO 数据必须使用持久卷；Redis 强制密码，应用使用独立的 MinIO 凭据而不是 Root 凭据。生产密码、OIDC、主密钥与连接地址由部署平台的 Secret 注入，不得写入镜像、Compose 文件或 GitHub Release 附件。

## 本地构建与生产式启动

构建三个镜像：

```powershell
docker build --target api -t cyber-netizen-api:local .
docker build --target worker -t cyber-netizen-worker:local .
docker build --target web -t cyber-netizen-web:local .
```

`compose.production.yaml` 是现有基础设施 Compose 的生产应用覆盖层。先通过环境或部署平台提供文件中标为必填的启动参数；其中 `CNB_REDIS_URL` 必须包含 `REDIS_PASSWORD` 对应凭据，MinIO 初始化任务会幂等创建应用用户并绑定读写策略。再执行迁移和启动：

```powershell
docker compose -f compose.yaml -f compose.production.yaml --profile operations run --rm migrate
docker compose -f compose.yaml -f compose.production.yaml up -d api worker web
docker compose -f compose.yaml -f compose.production.yaml ps
```

不得在多副本 API 启动命令中自动迁移数据库。迁移应作为发布前的单实例作业执行，确认成功后再滚动更新 API 与 Worker。全部宿主机端口默认只绑定 `127.0.0.1`，管理后台默认位于 `http://localhost:8080`；需要对外服务时应由 TLS 反向代理转发，或显式设置 `CNB_WEB_BIND_ADDRESS` 并同步配置网络防火墙。

## 持续集成质量门

每次推送和 Pull Request 会执行：

1. `uv lock --check`、冻结安装、Ruff、Pyright、pytest、Alembic head 与所有 Python workspace 包构建。
2. pnpm 冻结安装、lint、TypeScript、Vitest、Vite build 与 Playwright/axe。
3. `pip-audit` 和 `pnpm audit --prod` 生产依赖审计。
4. Hadolint、三个镜像的 BuildKit 构建、Trivy 高危/严重漏洞门禁。
5. 为每个 CI 镜像生成 SPDX JSON SBOM 并作为短期构件留存。

Dependabot 每周检查 uv、pnpm、Docker 基础镜像和 GitHub Actions 更新。安全升级仍需通过完整 CI，不直接绕过分支保护。

## 版本发布

推送符合 `vMAJOR.MINOR.PATCH` 的 Git tag，或从 GitHub Actions 手动输入同格式版本，可触发“发布容器镜像”工作流。工作流会：

1. 构建 `linux/amd64` 与 `linux/arm64` 多架构镜像并推送 GHCR。
2. 发布版本标签和提交 SHA 标签，不发布可漂移的 `latest` 标签。
3. 生成 BuildKit provenance 与 SBOM，并使用 GitHub artifact attestation 绑定镜像摘要。
4. 使用 GitHub OIDC 和 Sigstore Fulcio 对镜像摘要执行 Cosign 无密钥签名。
5. 创建 GitHub Release，附加三个 SPDX JSON SBOM、不可变镜像引用和 SHA-256 校验和。

镜像名称为：

- `ghcr.io/danchar12138/cyber-netizen-bot-api`
- `ghcr.io/danchar12138/cyber-netizen-bot-worker`
- `ghcr.io/danchar12138/cyber-netizen-bot-web`

正式部署必须使用 Release 附件记录的 `image@sha256:...`，不要只引用版本标签。

## 验证发布物

先从 Release 附件或 GHCR 获取不可变引用，再验证 GitHub provenance：

```powershell
gh attestation verify "oci://ghcr.io/danchar12138/cyber-netizen-bot-api@sha256:<digest>" --repo danchar12138/cyber-netizen-bot
```

验证 Cosign 的 GitHub Actions OIDC 身份：

```powershell
cosign verify "ghcr.io/danchar12138/cyber-netizen-bot-api@sha256:<digest>" --certificate-oidc-issuer "https://token.actions.githubusercontent.com" --certificate-identity-regexp "^https://github.com/danchar12138/cyber-netizen-bot/.github/workflows/release.yml@refs/(tags|heads)/.+$"
```

校验下载附件，并按需复扫指定摘要：

```powershell
Get-FileHash -Algorithm SHA256 .\sbom-api.spdx.json
trivy image --severity HIGH,CRITICAL --ignore-unfixed "ghcr.io/danchar12138/cyber-netizen-bot-api@sha256:<digest>"
```

## 回滚与安全事件

回滚不重新构建旧代码：把 Compose 或部署平台中的三个镜像引用统一切换到上一份已验证 Release 的摘要，先执行数据库兼容性判断，再按 Web、API、Worker 的依赖关系滚动替换。若迁移不向后兼容，应先执行对应的已演练降级步骤。

发现基础镜像或依赖漏洞时，更新锁文件或镜像版本、运行完整 CI、发布新版本并切换到新摘要；不要覆盖已有标签或删除原 SBOM。签名、provenance、Release 附件或摘要不一致时，停止部署并按供应链事件处理，不得临时跳过验证。
