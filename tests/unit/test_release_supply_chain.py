"""生产容器与发布供应链的静态安全契约。"""

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]


def _docker_stage(name: str) -> str:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    marker = f" AS {name}\n"
    start = dockerfile.index(marker) + len(marker)
    end = dockerfile.find("\nFROM ", start)
    return dockerfile[start:] if end == -1 else dockerfile[start:end]


@pytest.mark.parametrize(
    ("target", "user", "command_fragment"),
    [
        ("api", "USER 10001:10001", '"cnb_api.main:app"'),
        ("worker", "USER 10001:10001", '"cnb_worker.tasks"'),
        ("web", "USER 101:101", "/health/web"),
    ],
)
def test_production_image_targets_are_non_root_and_health_checked(
    target: str,
    user: str,
    command_fragment: str,
) -> None:
    stage = _docker_stage(target)

    assert user in stage
    assert "HEALTHCHECK" in stage
    assert command_fragment in stage
    assert "org.opencontainers.image.source" in stage


def test_docker_context_excludes_secrets_and_local_artifacts() -> None:
    patterns = set((REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert {".git", ".env*", "*.pem", "*.key", "**/node_modules"} <= patterns


def test_production_compose_applies_runtime_hardening() -> None:
    compose = (REPOSITORY_ROOT / "compose.production.yaml").read_text(encoding="utf-8")

    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose
    assert "CNB_API_IMAGE" in compose
    assert "CNB_WORKER_IMAGE" in compose
    assert "CNB_WEB_IMAGE" in compose
    assert "REDIS_PASSWORD" in compose
    assert "MINIO_APP_ACCESS_KEY" in compose


def test_web_runtime_only_renders_configuration_into_tmp() -> None:
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    nginx = (REPOSITORY_ROOT / "deploy/nginx/nginx.conf").read_text(encoding="utf-8")

    assert "NGINX_ENVSUBST_OUTPUT_DIR=/tmp/nginx/conf.d" in dockerfile
    assert "include /tmp/nginx/conf.d/*.conf;" in nginx
    assert "pid /tmp/nginx.pid;" in nginx
    assert "RUN apk upgrade --no-cache" in _docker_stage("web")
    assert '"--output-document=/dev/null"' in _docker_stage("web")
    assert '"--spider"' not in _docker_stage("web")


def test_release_workflow_produces_signed_attested_sboms() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "linux/amd64,linux/arm64" in workflow
    assert "actions/attest-build-provenance@" in workflow
    assert "actions/attest-sbom@" in workflow
    assert "cosign sign --yes" in workflow
    assert "sbom-${{ matrix.target }}.spdx.json" in workflow
    assert "value=latest" not in workflow


def test_ci_runs_compose_and_isolated_restore_acceptance() -> None:
    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    script = (REPOSITORY_ROOT / "scripts/verify-infrastructure.ps1").read_text(encoding="utf-8")

    assert "infrastructure-acceptance:" in workflow
    assert "./scripts/verify-infrastructure.ps1" in workflow
    assert "infrastructure-acceptance-evidence" in workflow
    assert '"build", "api", "worker", "web"' in script
    assert '"--no-owner", "--no-privileges"' in script
    assert "/health/ready" in script
    assert "/api/v1/chat/conversations?limit=10" in script
    assert "Remove-AcceptanceDirectory" in script
