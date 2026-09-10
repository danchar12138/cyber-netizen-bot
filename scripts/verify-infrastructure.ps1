[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$WebPort = 18080,

    [string]$EvidencePath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Docker {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 命令执行失败，退出码为 $LASTEXITCODE。"
    }
}

function Invoke-DockerCapture {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 查询执行失败，退出码为 $LASTEXITCODE。"
    }
    return $output
}

function Wait-DockerProbe {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [ValidateRange(1, 300)][int]$TimeoutSeconds = 60
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        & docker @Arguments *> $null
        if ($LASTEXITCODE -eq 0) {
            return
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)

    throw "Docker 服务未在 $TimeoutSeconds 秒内就绪。"
}

function Get-Scalar {
    param(
        [Parameter(Mandatory)][string[]]$DockerArguments,
        [Parameter(Mandatory)][string]$Label
    )

    $value = ((Invoke-DockerCapture -Arguments $DockerArguments) -join "").Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Label 查询结果为空。"
    }
    return $value
}

function Remove-AcceptanceDirectory {
    param(
        [Parameter(Mandatory)][string]$Target,
        [Parameter(Mandatory)][string]$AllowedRoot
    )

    if (-not (Test-Path -LiteralPath $Target)) {
        return
    }
    $resolvedTarget = [IO.Path]::GetFullPath($Target)
    $resolvedRoot = [IO.Path]::GetFullPath($AllowedRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $requiredPrefix = $resolvedRoot + [IO.Path]::DirectorySeparatorChar
    if (
        -not $resolvedTarget.StartsWith($requiredPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFileName($resolvedTarget)).StartsWith(
            "cnb-infrastructure-acceptance-",
            [StringComparison]::Ordinal
        )
    ) {
        throw "拒绝清理不属于本次基础设施验收的目录。"
    }
    Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
}

if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 Docker CLI；请在安装 Docker Desktop 或 Docker Engine 的环境执行。"
}

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$composeFile = Join-Path $repositoryRoot "compose.yaml"
$productionComposeFile = Join-Path $repositoryRoot "compose.production.yaml"
$runSuffix = ([Guid]::NewGuid().ToString("N")).Substring(0, 12)
$projectName = "cnb-acceptance-$runSuffix"
$restoreNetwork = "cnb-restore-$runSuffix"
$restorePostgres = "cnb-restore-postgres-$runSuffix"
$restoreMinio = "cnb-restore-minio-$runSuffix"
$restoreRedis = "cnb-restore-redis-$runSuffix"
$restoreApi = "cnb-restore-api-$runSuffix"
$temporaryRoot = if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    [IO.Path]::GetTempPath()
} else {
    $env:RUNNER_TEMP
}
$workRoot = Join-Path $temporaryRoot "cnb-infrastructure-acceptance-$runSuffix"
$backupRoot = Join-Path $workRoot "backup"
$backupObjects = Join-Path $backupRoot "objects"
$restoredObjects = Join-Path $workRoot "restored-objects"
$databaseDump = Join-Path $backupRoot "database.dump"
$manifestPath = Join-Path $backupRoot "manifest.json"

$postgresPassword = "pg-$runSuffix"
$redisPassword = "redis-$runSuffix"
$minioRootUser = "root$runSuffix"
$minioRootPassword = "root-secret-$runSuffix"
$minioAppUser = "app$runSuffix"
$minioAppPassword = "app-secret-$runSuffix"
$restoreDatabase = "cyber_netizen_restore"
$restoreDatabaseUser = "restore_user"
$restoreDatabasePassword = "restore-pg-$runSuffix"
$restoreMinioUser = "restore$runSuffix"
$restoreMinioPassword = "restore-minio-$runSuffix"
$restoreRedisPassword = "restore-redis-$runSuffix"
$configMasterKey = [Convert]::ToBase64String(
    [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
)

$env:POSTGRES_PASSWORD = $postgresPassword
$env:REDIS_PASSWORD = $redisPassword
$env:MINIO_ROOT_USER = $minioRootUser
$env:MINIO_ROOT_PASSWORD = $minioRootPassword
$env:MINIO_API_CORS_ALLOW_ORIGIN = "http://localhost:$WebPort"
$env:CNB_ENVIRONMENT = "development"
$env:CNB_LOG_LEVEL = "INFO"
$env:CNB_DATABASE_URL = "postgresql+psycopg://cyber_netizen:${postgresPassword}@postgres:5432/cyber_netizen"
$env:CNB_REDIS_URL = "redis://:${redisPassword}@redis:6379/0"
$env:CNB_MINIO_ENDPOINT_URL = "http://minio:9000"
$env:CNB_MINIO_ACCESS_KEY = $minioAppUser
$env:CNB_MINIO_SECRET_KEY = $minioAppPassword
$env:CNB_MINIO_BUCKET = "cyber-netizen"
$env:CNB_CONFIG_MASTER_KEY = $configMasterKey
$env:CNB_CORS_ORIGINS = "[`"http://localhost:$WebPort`"]"
$env:CNB_READINESS_DEEP_CHECKS = "true"
$env:CNB_AUTHENTICATION_MODE = "development"
$env:CNB_OTEL_ENABLED = "false"
$env:CNB_API_IMAGE = "cyber-netizen-api:acceptance-$runSuffix"
$env:CNB_WORKER_IMAGE = "cyber-netizen-worker:acceptance-$runSuffix"
$env:CNB_WEB_IMAGE = "cyber-netizen-web:acceptance-$runSuffix"
$env:CNB_WEB_BIND_ADDRESS = "127.0.0.1"
$env:CNB_WEB_PORT = "$WebPort"

$composeArguments = @(
    "compose",
    "--project-name", $projectName,
    "--file", $composeFile,
    "--file", $productionComposeFile
)
$composeInitialized = $false
$restoreNetworkCreated = $false
$evidence = $null

New-Item -ItemType Directory -Path $backupObjects, $restoredObjects -Force | Out-Null

try {
    Invoke-Docker -Arguments @("info", "--format", "{{.ServerVersion}}")
    Invoke-Docker -Arguments ($composeArguments + @("config", "--quiet"))
    $composeInitialized = $true

    Invoke-Docker -Arguments ($composeArguments + @("build", "api", "worker", "web"))
    Invoke-Docker -Arguments (
        $composeArguments + @("up", "--detach", "--wait", "postgres", "redis", "minio")
    )
    Invoke-Docker -Arguments ($composeArguments + @("up", "minio-init"))
    Invoke-Docker -Arguments (
        $composeArguments + @("--profile", "operations", "run", "--rm", "migrate")
    )
    try {
        Invoke-Docker -Arguments (
            $composeArguments + @(
                "up", "--detach", "--no-build", "--wait", "api", "worker", "web"
            )
        )
    } catch {
        & docker @composeArguments ps
        & docker @composeArguments logs --no-color --tail 100 api worker web
        throw
    }

    $baseUri = "http://127.0.0.1:$WebPort"
    try {
        $live = Invoke-RestMethod -Uri "$baseUri/health/live" -TimeoutSec 10
        $ready = Invoke-RestMethod -Uri "$baseUri/health/ready" -TimeoutSec 10
        $session = Invoke-RestMethod `
            -Uri "$baseUri/api/v1/administration/session" `
            -TimeoutSec 10
        $identity = Invoke-RestMethod -Uri "$baseUri/api/v1/chat/identity" -TimeoutSec 10
        $conversation = Invoke-RestMethod `
            -Method Post `
            -Uri "$baseUri/api/v1/chat/conversations" `
            -ContentType "application/json" `
            -Body '{"title":"基础设施验收会话"}' `
            -TimeoutSec 10

        $profileReferences = @()
        foreach ($profile in @(
            @{ key = "acceptance-fast"; model = "friendly-fast-v1"; inputPrice = 1; outputPrice = 2 },
            @{ key = "acceptance-quality"; model = "friendly-quality-v1"; inputPrice = 3; outputPrice = 6 }
        )) {
            $profileDraftBody = @{
                kind = "model_profile"
                key = $profile.key
                name = "$($profile.key) PostgreSQL 验收档案"
                payload = @{
                    provider = "development"
                    model = $profile.model
                    purposes = @("chat.realizer")
                    pricing = @{
                        input_usd_per_million_tokens = $profile.inputPrice
                        output_usd_per_million_tokens = $profile.outputPrice
                    }
                }
            } | ConvertTo-Json -Depth 6 -Compress
            $profileDraft = Invoke-RestMethod `
                -Method Post `
                -Uri "$baseUri/api/v1/cognition/resources" `
                -ContentType "application/json" `
                -Body $profileDraftBody `
                -TimeoutSec 10
            $publishedProfile = Invoke-RestMethod `
                -Method Post `
                -Uri "$baseUri/api/v1/cognition/resources/$($profileDraft.id)/publish" `
                -TimeoutSec 10
            $profileReferences += @{
                key = $publishedProfile.key
                version = $publishedProfile.version
            }
        }
        $routeDraftBody = @{
            kind = "model_route"
            key = "chat.realizer"
            name = "PostgreSQL 多模型验收路由"
            payload = @{
                purpose = "chat.realizer"
                primary_profile = $profileReferences[0]
                fallback_profiles = @($profileReferences[1])
                timeout_seconds = 30
                max_attempts = 2
            }
        } | ConvertTo-Json -Depth 6 -Compress
        $routeDraft = Invoke-RestMethod `
            -Method Post `
            -Uri "$baseUri/api/v1/cognition/resources" `
            -ContentType "application/json" `
            -Body $routeDraftBody `
            -TimeoutSec 10
        $null = Invoke-RestMethod `
            -Method Post `
            -Uri "$baseUri/api/v1/cognition/resources/$($routeDraft.id)/publish" `
            -TimeoutSec 10
        $comparisonTargets = Invoke-RestMethod `
            -Uri "$baseUri/api/v1/evaluations/comparison-targets" `
            -TimeoutSec 10
        $comparisonBody = @{
            profile_keys = @("acceptance-fast", "acceptance-quality")
        } | ConvertTo-Json -Compress
        $comparison = Invoke-RestMethod `
            -Method Post `
            -Uri "$baseUri/api/v1/evaluations/comparisons" `
            -ContentType "application/json" `
            -Body $comparisonBody `
            -TimeoutSec 30
        $comparisonList = Invoke-RestMethod `
            -Uri "$baseUri/api/v1/evaluations/comparisons?limit=20" `
            -TimeoutSec 10
        $comparisonDetail = Invoke-RestMethod `
            -Uri "$baseUri/api/v1/evaluations/comparisons/$($comparison.id)" `
            -TimeoutSec 10
    } catch {
        & docker @composeArguments logs --no-color --tail 100 api web
        throw
    }
    if (
        $live.status -ne "healthy" -or
        $ready.status -ne "ready" -or
        $session.authentication_mode -ne "development" -or
        [string]::IsNullOrWhiteSpace($identity.tenant_id) -or
        [string]::IsNullOrWhiteSpace($conversation.id)
    ) {
        throw "生产式 Compose 的 API、依赖或持久化冒烟未通过。"
    }
    $comparisonSummaryRunProperties = @($comparisonList.items)[0].entries[0].run.PSObject.Properties.Name
    if (
        @($comparisonTargets.items).Count -ne 2 -or
        @($comparison.entries).Count -ne 2 -or
        @($comparisonDetail.entries).Count -ne 2 -or
        @($comparisonDetail.entries[0].run.results).Count -ne 5 -or
        $comparisonSummaryRunProperties -contains "results"
    ) {
        throw "PostgreSQL 多模型对比的目标、完整详情或无正文摘要契约不一致。"
    }
    $comparisonDatabaseCounts = Get-Scalar `
        -Label "PostgreSQL 多模型对比原子持久化" `
        -DockerArguments (
            $composeArguments + @(
                "exec", "-T", "postgres", "psql", "--username=cyber_netizen",
                "--dbname=cyber_netizen", "--tuples-only", "--no-align",
                "--command=SELECT concat((SELECT count(*) FROM evaluation_comparisons), ':', (SELECT count(*) FROM evaluation_comparison_entries), ':', (SELECT count(*) FROM evaluation_runs), ':', (SELECT count(*) FROM evaluation_case_results));"
            )
        )
    if ($comparisonDatabaseCounts -ne "1:2:2:10") {
        throw "PostgreSQL 多模型对比没有原子保存实验、两个运行及其十条用例结果。"
    }

    $seedObjectScript = @'
mc alias set source http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null &&
printf 'cyber-netizen-restore-probe\n' | mc pipe "source/$MINIO_BUCKET/acceptance/restore-probe.bin" >/dev/null
'@
    Invoke-Docker -Arguments (
        $composeArguments + @(
            "run", "--rm", "--no-deps", "--entrypoint", "/bin/sh", "minio-init", "-c",
            $seedObjectScript
        )
    )

    Invoke-Docker -Arguments (
        $composeArguments + @(
            "exec", "-T", "postgres", "pg_dump",
            "--username=cyber_netizen", "--dbname=cyber_netizen", "--format=custom",
            "--file=/tmp/cnb-database.dump"
        )
    )
    Invoke-Docker -Arguments (
        $composeArguments + @("cp", "postgres:/tmp/cnb-database.dump", $databaseDump)
    )
    Invoke-Docker -Arguments (
        $composeArguments + @("exec", "-T", "postgres", "rm", "-f", "/tmp/cnb-database.dump")
    )

    $backupObjectsScript = @'
mc alias set source http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null &&
mc mirror --overwrite "source/$MINIO_BUCKET" /backup >/dev/null
'@
    Invoke-Docker -Arguments (
        $composeArguments + @(
            "run", "--rm", "--no-deps", "--volume", "${backupObjects}:/backup",
            "--entrypoint", "/bin/sh", "minio-init", "-c", $backupObjectsScript
        )
    )

    $countSql = "SELECT (SELECT count(*) FROM tenants) + (SELECT count(*) FROM users) + (SELECT count(*) FROM agents) + (SELECT count(*) FROM conversations) + (SELECT count(*) FROM messages) + (SELECT count(*) FROM memories) + (SELECT count(*) FROM attachments);"
    $sourceRevision = Get-Scalar -Label "源数据库 Alembic 版本" -DockerArguments (
        $composeArguments + @(
            "exec", "-T", "postgres", "psql", "--username=cyber_netizen",
            "--dbname=cyber_netizen", "--tuples-only", "--no-align",
            "--command=SELECT version_num FROM alembic_version;"
        )
    )
    $sourceRowCount = [long](Get-Scalar -Label "源数据库业务行数" -DockerArguments (
        $composeArguments + @(
            "exec", "-T", "postgres", "psql", "--username=cyber_netizen",
            "--dbname=cyber_netizen", "--tuples-only", "--no-align", "--command=$countSql"
        )
    ))

    $objectEntries = @(
        Get-ChildItem -LiteralPath $backupObjects -File -Recurse | ForEach-Object {
            [ordered]@{
                path = [IO.Path]::GetRelativePath($backupObjects, $_.FullName).Replace("\", "/")
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
    )
    if ($objectEntries.Count -lt 1) {
        throw "MinIO 备份未包含验收对象。"
    }
    $manifest = [ordered]@{
        schema_version = "cnb-backup-manifest-v1"
        database = [ordered]@{
            file = "database.dump"
            bytes = (Get-Item -LiteralPath $databaseDump).Length
            sha256 = (Get-FileHash -LiteralPath $databaseDump -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        objects = $objectEntries
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    $manifestSha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()

    Invoke-Docker -Arguments @("network", "create", $restoreNetwork)
    $restoreNetworkCreated = $true
    Invoke-Docker -Arguments @(
        "run", "--detach", "--name", $restorePostgres, "--network", $restoreNetwork,
        "--tmpfs", "/var/lib/postgresql/data",
        "--env", "POSTGRES_DB=$restoreDatabase",
        "--env", "POSTGRES_USER=$restoreDatabaseUser",
        "--env", "POSTGRES_PASSWORD=$restoreDatabasePassword",
        "pgvector/pgvector:pg16"
    )
    Invoke-Docker -Arguments @(
        "run", "--detach", "--name", $restoreMinio, "--network", $restoreNetwork,
        "--tmpfs", "/data",
        "--env", "MINIO_ROOT_USER=$restoreMinioUser",
        "--env", "MINIO_ROOT_PASSWORD=$restoreMinioPassword",
        "minio/minio:RELEASE.2025-04-22T22-12-26Z", "server", "/data"
    )
    Invoke-Docker -Arguments @(
        "run", "--detach", "--name", $restoreRedis, "--network", $restoreNetwork,
        "redis:7.4-alpine", "redis-server", "--appendonly", "no", "--requirepass",
        $restoreRedisPassword
    )

    Wait-DockerProbe -Arguments @(
        "exec", $restorePostgres, "pg_isready", "--username=$restoreDatabaseUser",
        "--dbname=$restoreDatabase"
    )
    Wait-DockerProbe -Arguments @(
        "exec", $restoreRedis, "redis-cli", "--no-auth-warning", "-a",
        $restoreRedisPassword, "ping"
    )
    Wait-DockerProbe -TimeoutSeconds 90 -Arguments @(
        "exec", $restoreMinio, "curl", "--fail", "http://127.0.0.1:9000/minio/health/ready"
    )

    Invoke-Docker -Arguments @("cp", $databaseDump, "${restorePostgres}:/tmp/database.dump")
    Invoke-Docker -Arguments @(
        "exec", $restorePostgres, "pg_restore", "--username=$restoreDatabaseUser",
        "--dbname=$restoreDatabase", "--exit-on-error", "--no-owner", "--no-privileges",
        "/tmp/database.dump"
    )
    Invoke-Docker -Arguments @(
        "run", "--rm", "--network", $restoreNetwork,
        "--env", "MC_HOST_restore=http://${restoreMinioUser}:${restoreMinioPassword}@${restoreMinio}:9000",
        "minio/mc:RELEASE.2025-04-16T18-13-26Z", "mb", "--ignore-existing",
        "restore/cyber-netizen"
    )
    Invoke-Docker -Arguments @(
        "run", "--rm", "--network", $restoreNetwork,
        "--volume", "${backupObjects}:/backup:ro",
        "--env", "MC_HOST_restore=http://${restoreMinioUser}:${restoreMinioPassword}@${restoreMinio}:9000",
        "minio/mc:RELEASE.2025-04-16T18-13-26Z", "mirror", "/backup",
        "restore/cyber-netizen"
    )
    Invoke-Docker -Arguments @(
        "run", "--rm", "--network", $restoreNetwork,
        "--volume", "${restoredObjects}:/restored",
        "--env", "MC_HOST_restore=http://${restoreMinioUser}:${restoreMinioPassword}@${restoreMinio}:9000",
        "minio/mc:RELEASE.2025-04-16T18-13-26Z", "mirror", "restore/cyber-netizen",
        "/restored"
    )

    $restoredRevision = Get-Scalar -Label "恢复数据库 Alembic 版本" -DockerArguments @(
        "exec", $restorePostgres, "psql", "--username=$restoreDatabaseUser",
        "--dbname=$restoreDatabase", "--tuples-only", "--no-align",
        "--command=SELECT version_num FROM alembic_version;"
    )
    $restoredRowCount = [long](Get-Scalar -Label "恢复数据库业务行数" -DockerArguments @(
        "exec", $restorePostgres, "psql", "--username=$restoreDatabaseUser",
        "--dbname=$restoreDatabase", "--tuples-only", "--no-align", "--command=$countSql"
    ))
    if ($sourceRevision -ne $restoredRevision -or $sourceRowCount -ne $restoredRowCount) {
        throw "PostgreSQL 恢复后的版本或业务计数与备份不一致。"
    }

    $expectedObjects = @{}
    foreach ($entry in $objectEntries) {
        $expectedObjects[$entry.path] = "$($entry.bytes):$($entry.sha256)"
    }
    $restoredEntries = @(
        Get-ChildItem -LiteralPath $restoredObjects -File -Recurse | ForEach-Object {
            $relativePath = [IO.Path]::GetRelativePath($restoredObjects, $_.FullName).Replace("\", "/")
            [ordered]@{
                path = $relativePath
                signature = "$($_.Length):$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())"
            }
        }
    )
    if ($restoredEntries.Count -ne $objectEntries.Count) {
        throw "MinIO 恢复后的对象数量与备份不一致。"
    }
    foreach ($entry in $restoredEntries) {
        if (-not $expectedObjects.ContainsKey($entry.path) -or $expectedObjects[$entry.path] -ne $entry.signature) {
            throw "MinIO 恢复后的对象摘要与备份不一致。"
        }
    }

    Invoke-Docker -Arguments @(
        "run", "--detach", "--name", $restoreApi, "--network", $restoreNetwork,
        "--read-only", "--tmpfs", "/tmp:size=64m,mode=1777", "--security-opt",
        "no-new-privileges:true", "--cap-drop", "ALL",
        "--env", "CNB_ENVIRONMENT=development",
        "--env", "CNB_DATABASE_URL=postgresql+psycopg://${restoreDatabaseUser}:${restoreDatabasePassword}@${restorePostgres}:5432/${restoreDatabase}",
        "--env", "CNB_REDIS_URL=redis://:${restoreRedisPassword}@${restoreRedis}:6379/0",
        "--env", "CNB_MINIO_ENDPOINT_URL=http://${restoreMinio}:9000",
        "--env", "CNB_MINIO_ACCESS_KEY=$restoreMinioUser",
        "--env", "CNB_MINIO_SECRET_KEY=$restoreMinioPassword",
        "--env", "CNB_MINIO_BUCKET=cyber-netizen",
        "--env", "CNB_CONFIG_MASTER_KEY=$configMasterKey",
        "--env", "CNB_READINESS_DEEP_CHECKS=true",
        "--env", "CNB_AUTHENTICATION_MODE=development",
        $env:CNB_API_IMAGE
    )
    Wait-DockerProbe -TimeoutSeconds 90 -Arguments @(
        "exec", $restoreApi, "python", "-c",
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3).read()"
    )
    $applicationProbe = @'
import json
import urllib.request

def read(path: str) -> dict[str, object]:
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected status: {response.status}")
        return json.load(response)

ready = read("/health/ready")
session = read("/api/v1/administration/session")
conversations = read("/api/v1/chat/conversations?limit=10")
if ready.get("status") != "ready":
    raise RuntimeError("restored dependencies are not ready")
if session.get("authentication_mode") != "development":
    raise RuntimeError("restored admin session is unavailable")
if not conversations.get("items"):
    raise RuntimeError("restored conversation data is unavailable")
'@
    Invoke-Docker -Arguments @("exec", $restoreApi, "python", "-c", $applicationProbe)

    $evidence = [ordered]@{
        schema_version = "cnb-infrastructure-acceptance-v1"
        migration_revision = $sourceRevision
        database_row_count = $restoredRowCount
        object_count = $restoredEntries.Count
        backup_manifest_sha256 = $manifestSha256
        compose_services_ready = $true
        postgresql_integrity = $true
        minio_object_integrity = $true
        restored_application_smoke = $true
        evaluation_comparison_persisted = $true
        evaluation_comparison_counts = $comparisonDatabaseCounts
    }
} finally {
    & docker rm --force $restoreApi $restoreRedis $restoreMinio $restorePostgres *> $null
    if ($restoreNetworkCreated) {
        & docker network rm $restoreNetwork *> $null
    }
    if ($composeInitialized) {
        & docker @composeArguments down --volumes --remove-orphans *> $null
        & docker image rm --force $env:CNB_API_IMAGE $env:CNB_WORKER_IMAGE $env:CNB_WEB_IMAGE *> $null
    }
    & docker run --rm --volume "${workRoot}:/work" --entrypoint /bin/sh `
        minio/mc:RELEASE.2025-04-16T18-13-26Z -c "chmod -R a+rwX /work" *> $null
    Remove-AcceptanceDirectory -Target $workRoot -AllowedRoot $temporaryRoot
}

$evidenceJson = $evidence | ConvertTo-Json -Depth 4
if (-not [string]::IsNullOrWhiteSpace($EvidencePath)) {
    $evidenceParent = Split-Path -Parent $EvidencePath
    if (-not [string]::IsNullOrWhiteSpace($evidenceParent)) {
        New-Item -ItemType Directory -Path $evidenceParent -Force | Out-Null
    }
    $evidenceJson | Set-Content -LiteralPath $EvidencePath -Encoding utf8
}
Write-Output $evidenceJson
