"""以确定性格式导出 Web 客户端使用的 OpenAPI 契约。"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.routing import APIRoute


def stable_operation_id(route: APIRoute) -> str:
    """仅根据 HTTP 方法和公开路径生成不会随处理函数重命名漂移的 ID。"""
    methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
    if len(methods) != 1:
        raise ValueError(f"路由 {route.path_format} 必须且只能声明一个 HTTP 方法")
    path = re.sub(r"\{([^}]+)\}", r"by_\1", route.path_format.strip("/"))
    normalized_path = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_") or "root"
    return f"{methods[0].lower()}_{normalized_path}"


def render_openapi_document(application: FastAPI) -> str:
    """生成排序稳定、保留中文说明且以换行结尾的 OpenAPI JSON。"""
    document = cast(dict[str, object], application.openapi())
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def export_openapi_document(application: FastAPI, output: Path) -> None:
    """将当前 API 契约写入指定文件，供 TypeScript 代码生成器消费。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_openapi_document(application), encoding="utf-8", newline="\n")


def main(arguments: Sequence[str] | None = None) -> int:
    """执行 OpenAPI 导出命令。"""
    parser = argparse.ArgumentParser(description="导出赛博网友 API 的 OpenAPI JSON 契约")
    parser.add_argument("--output", required=True, type=Path, help="OpenAPI JSON 输出路径")
    options = parser.parse_args(arguments)

    from cnb_api.main import app

    export_openapi_document(app, options.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
