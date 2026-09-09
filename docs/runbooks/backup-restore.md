# PostgreSQL 与 MinIO 备份恢复演练

本手册用于在隔离环境验证 PostgreSQL 业务真相源和 MinIO 私有对象能够从同一批备份恢复。恢复验证不得连接生产数据库、生产 MinIO 或生产 OIDC；Redis 不是业务真相源，不进入备份集。

## 目标与完成标准

- PostgreSQL 备份可由 `pg_restore` 完整恢复，Alembic 版本与备份时一致。
- MinIO 对象数量、总字节数和逐对象摘要与备份清单一致。
- 隔离 API 的 `/health/ready`、管理会话和内部对话读取冒烟通过。
- 备份清单自身有 SHA-256，演练只在管理后台登记清单摘要、校验计数和三项布尔结果。
- 运行记录、日志和截图不得包含数据库密码、MinIO 密钥、对象键清单、消息正文、Prompt 或访问令牌。

生产环境应在此逻辑备份基线之外启用 PostgreSQL WAL/PITR（推荐 pgBackRest 或云厂商等价能力）、异地加密副本、MinIO 版本控制与站点复制，并按业务目标单独定义 RPO/RTO。`data.backup.expected_interval_hours` 只控制管理后台的演练过期提示，不替代备份调度。

## 1. 生成一致备份集

先确认应用写入已暂停或已建立数据库与对象存储的一致性边界。开发 Compose 演练可在没有写请求时执行：

```powershell
$drillVersion = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path (Resolve-Path '.').Path "backups\$drillVersion"
New-Item -ItemType Directory -Path $backupRoot, (Join-Path $backupRoot 'objects') | Out-Null

docker compose exec -T postgres pg_dump `
  --username=cyber_netizen `
  --dbname=cyber_netizen `
  --format=custom `
  --file=/tmp/cnb-database.dump
docker compose cp postgres:/tmp/cnb-database.dump (Join-Path $backupRoot 'database.dump')
docker compose exec -T postgres rm -f /tmp/cnb-database.dump

docker compose run --rm `
  --entrypoint /bin/sh `
  --volume "${backupRoot}:/backup" `
  minio-init `
  -c 'mc alias set source http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc mirror --overwrite "source/$MINIO_BUCKET" /backup/objects'
```

随后生成离线清单。清单文件允许保存相对对象路径和摘要，但必须和备份文件一起加密、限制访问且不得上传到日志或管理 API：

```powershell
$databaseDump = Join-Path $backupRoot 'database.dump'
$objectRoot = Join-Path $backupRoot 'objects'
$manifestPath = Join-Path $backupRoot 'manifest.json'
$objectEntries = Get-ChildItem -LiteralPath $objectRoot -File -Recurse | ForEach-Object {
  [ordered]@{
    path = [IO.Path]::GetRelativePath($objectRoot, $_.FullName).Replace('\', '/')
    bytes = $_.Length
    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}
$manifest = [ordered]@{
  schema_version = 'cnb-backup-manifest-v1'
  created_at = (Get-Date).ToUniversalTime().ToString('o')
  database = [ordered]@{
    file = 'database.dump'
    bytes = (Get-Item -LiteralPath $databaseDump).Length
    sha256 = (Get-FileHash -LiteralPath $databaseDump -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  objects = @($objectEntries)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
$manifestSha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
```

把备份集写入受访问控制的加密存储，并按组织保留策略删除本地明文副本。不要把 `backups/` 提交到 Git；仓库已忽略该目录。

## 2. 在隔离容器恢复

以下容器名和端口专用于演练。执行前确认它们没有被其他任务使用；禁止把地址替换为生产端点。

```powershell
docker network create cnb-restore-drill
docker run --detach --name cnb-restore-postgres `
  --network cnb-restore-drill `
  --publish 127.0.0.1:55432:5432 `
  --tmpfs /var/lib/postgresql/data `
  --env POSTGRES_DB=cyber_netizen_restore `
  --env POSTGRES_USER=cyber_netizen_restore `
  --env POSTGRES_PASSWORD=restore-only-password `
  pgvector/pgvector:pg16
docker run --detach --name cnb-restore-minio `
  --network cnb-restore-drill `
  --publish 127.0.0.1:59000:9000 `
  --tmpfs /data `
  --env MINIO_ROOT_USER=restore-user `
  --env MINIO_ROOT_PASSWORD=restore-only-password `
  minio/minio:RELEASE.2025-04-22T22-12-26Z server /data
```

等待两个容器就绪后恢复数据库与对象：

```powershell
docker cp (Join-Path $backupRoot 'database.dump') cnb-restore-postgres:/tmp/database.dump
docker exec cnb-restore-postgres pg_restore `
  --username=cyber_netizen_restore `
  --dbname=cyber_netizen_restore `
  --exit-on-error `
  /tmp/database.dump

docker run --rm `
  --network cnb-restore-drill `
  --volume "${backupRoot}:/backup:ro" `
  --entrypoint /bin/sh `
  minio/mc:RELEASE.2025-04-16T18-13-26Z `
  -c 'mc alias set target http://cnb-restore-minio:9000 restore-user restore-only-password >/dev/null && mc mb --ignore-existing target/cyber-netizen >/dev/null && mc mirror /backup/objects target/cyber-netizen'
```

## 3. 验证完整性与应用冒烟

数据库至少验证 Alembic head、关键表可读、租户数量和业务总行数。计数可以登记，查询结果正文不要进入演练证据：

```powershell
docker exec cnb-restore-postgres psql `
  --username=cyber_netizen_restore `
  --dbname=cyber_netizen_restore `
  --tuples-only `
  --command="SELECT version_num FROM alembic_version;"
docker exec cnb-restore-postgres psql `
  --username=cyber_netizen_restore `
  --dbname=cyber_netizen_restore `
  --tuples-only `
  --command="SELECT (SELECT count(*) FROM tenants) + (SELECT count(*) FROM users) + (SELECT count(*) FROM conversations) + (SELECT count(*) FROM messages) + (SELECT count(*) FROM memories) + (SELECT count(*) FROM attachments);"
```

用清单逐一计算恢复后对象摘要并比较，记录匹配数量，不登记对象路径。然后以隔离端点启动 API：

```powershell
$env:CNB_ENVIRONMENT='development'
$env:CNB_DATABASE_URL='postgresql+psycopg://cyber_netizen_restore:restore-only-password@127.0.0.1:55432/cyber_netizen_restore'
$env:CNB_MINIO_ENDPOINT_URL='http://127.0.0.1:59000'
$env:CNB_MINIO_ACCESS_KEY='restore-user'
$env:CNB_MINIO_SECRET_KEY='restore-only-password'
$env:CNB_MINIO_BUCKET='cyber-netizen'
$env:CNB_READINESS_DEEP_CHECKS='true'
uv run uvicorn cnb_api.main:app --host 127.0.0.1 --port 18000
```

在另一个终端确认：

```powershell
Invoke-RestMethod http://127.0.0.1:18000/health/ready
Invoke-RestMethod http://127.0.0.1:18000/api/v1/administration/session
Invoke-RestMethod http://127.0.0.1:18000/api/v1/data-lifecycle/overview
```

如果任一步失败，演练视为失败：保留受控诊断证据，修复后从全新隔离环境重新执行，不得把失败登记为已验证。

## 4. 登记与清理

在管理后台“数据生命周期 → 登记备份恢复演练”填写：

- `$manifestSha256`，而不是清单正文或存储地址；
- 数据库校验行数和对象校验数量；
- PostgreSQL 完整性、MinIO 对象完整性、应用冒烟三项真实结果；
- 精确确认短语 `BACKUP RESTORE VERIFIED`。

停止隔离 API 后，只清理本手册创建的固定名称容器和网络：

```powershell
docker rm --force cnb-restore-postgres cnb-restore-minio
docker network rm cnb-restore-drill
```

本地备份集包含敏感业务数据。确认远端加密副本可用且符合保留策略后，使用组织批准的安全删除流程处理 `$backupRoot`；不要用宽泛递归删除命令。
