"""核心依赖方向与 MinIO 唯一对象存储的架构契约。"""

import ast
import re
import tomllib
from pathlib import Path
from typing import cast

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
CORE_SOURCE_ROOTS = (
    REPOSITORY_ROOT / "packages/domain/src",
    REPOSITORY_ROOT / "packages/cognition/src",
)
FORBIDDEN_CORE_IMPORTS = {"dramatiq", "fastapi", "minio", "openai", "sqlalchemy"}
FORBIDDEN_OBJECT_STORAGE_DEPENDENCIES = {"aioboto3", "boto3", "botocore"}
EXPECTED_MINIO_SETTINGS = {
    "minio_access_key",
    "minio_bucket",
    "minio_endpoint_url",
    "minio_secret_key",
}
EXPECTED_OBJECT_STORAGE_IMPLEMENTATIONS = {
    "MemoryObjectStorage",
    "MinioObjectStorage",
}


def _import_roots(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def _dependency_name(requirement: str) -> str:
    name = re.split(r"[\s<>=!~;\[]", requirement, maxsplit=1)[0]
    return name.strip().lower().replace("_", "-")


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in cast(list[object], value) if isinstance(item, str)]


def _manifest_dependencies(manifest_path: Path) -> set[str]:
    with manifest_path.open("rb") as file:
        manifest = cast(dict[str, object], tomllib.load(file))

    requirements: list[str] = []
    project_value = manifest.get("project")
    if isinstance(project_value, dict):
        project = cast(dict[str, object], project_value)
        requirements.extend(_string_items(project.get("dependencies")))
    groups_value = manifest.get("dependency-groups")
    if isinstance(groups_value, dict):
        dependency_groups = cast(dict[str, object], groups_value)
        for group in dependency_groups.values():
            requirements.extend(_string_items(group))
    return {_dependency_name(item) for item in requirements}


@pytest.mark.parametrize("source_root", CORE_SOURCE_ROOTS, ids=("domain", "cognition"))
def test_core_packages_do_not_import_frameworks_or_vendor_sdks(source_root: Path) -> None:
    violations: dict[str, list[str]] = {}
    for source_path in source_root.rglob("*.py"):
        forbidden = sorted(_import_roots(source_path) & FORBIDDEN_CORE_IMPORTS)
        if forbidden:
            violations[str(source_path.relative_to(REPOSITORY_ROOT))] = forbidden

    assert violations == {}, f"核心包存在越界导入：{violations}"


def test_project_manifests_do_not_declare_aws_object_storage_clients() -> None:
    violations: dict[str, list[str]] = {}
    for manifest_path in REPOSITORY_ROOT.rglob("pyproject.toml"):
        if any(part in {".venv", "node_modules"} for part in manifest_path.parts):
            continue
        forbidden = sorted(
            _manifest_dependencies(manifest_path) & FORBIDDEN_OBJECT_STORAGE_DEPENDENCIES
        )
        if forbidden:
            violations[str(manifest_path.relative_to(REPOSITORY_ROOT))] = forbidden

    assert violations == {}, f"项目声明了未支持的对象存储客户端：{violations}"


def test_object_storage_bootstrap_settings_use_only_minio_names() -> None:
    settings_path = REPOSITORY_ROOT / "packages/infrastructure/src/cnb_infrastructure/settings.py"
    tree = ast.parse(settings_path.read_text(encoding="utf-8"), filename=str(settings_path))
    settings_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    field_names = {
        node.target.id
        for node in settings_class.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    object_storage_fields = {
        name for name in field_names if name.startswith(("minio_", "s3_", "aws_"))
    }

    assert object_storage_fields == EXPECTED_MINIO_SETTINGS


def test_infrastructure_exposes_only_minio_and_memory_object_storage_implementations() -> None:
    infrastructure_root = REPOSITORY_ROOT / "packages/infrastructure/src/cnb_infrastructure"
    implementations: set[str] = set()
    forbidden_backend_classes: set[str] = set()

    for source_path in infrastructure_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.endswith("ObjectStorage"):
                implementations.add(node.name)
            if "aws" in node.name.lower() or "s3" in node.name.lower():
                forbidden_backend_classes.add(node.name)

    assert implementations == EXPECTED_OBJECT_STORAGE_IMPLEMENTATIONS
    assert forbidden_backend_classes == set()
