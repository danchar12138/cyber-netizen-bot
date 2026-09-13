"""以确定性格式导出 Web 客户端使用的 OpenAPI 契约。"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "系统健康", "description": "应用存活与基础设施就绪状态。"},
    {"name": "身份认证", "description": "管理后台身份认证与登录配置。"},
    {"name": "系统管理", "description": "系统总览、启动设置与任务状态。"},
    {"name": "管理与权限", "description": "智能体、用户、角色和审计管理。"},
    {"name": "配置中心", "description": "运行配置、版本发布与密钥管理。"},
    {"name": "认知内核", "description": "认知资源、运行轨迹与确定性回放。"},
    {"name": "评测实验室", "description": "评测集、模型对比与匿名盲评。"},
    {"name": "渠道与适配器", "description": "渠道实例、能力协商与安全出站。"},
    {"name": "外部身份与入站", "description": "外部身份映射、入站箱与安全重放。"},
    {"name": "记忆与关系", "description": "长期记忆、情景记录、关系与索引任务。"},
    {"name": "可观测性", "description": "服务指标、模型成本、告警与运行轨迹。"},
    {"name": "任务与主动行为", "description": "后台任务、死信、定时行为与任务进程。"},
    {"name": "内部对话附件", "description": "MinIO 附件上传、校验、预览与清理。"},
    {"name": "内部对话", "description": "会话、消息、反馈与流式运行。"},
    {"name": "数据生命周期", "description": "数据导出、遗忘、保留与恢复演练。"},
]

_TAG_TRANSLATIONS = {
    "health": "系统健康",
    "authentication": "身份认证",
    "system": "系统管理",
    "administration": "管理与权限",
    "configuration": "配置中心",
    "cognition": "认知内核",
    "evaluations": "评测实验室",
    "channels": "渠道与适配器",
    "integrations": "外部身份与入站",
    "memory": "记忆与关系",
    "observability": "可观测性",
    "tasks": "任务与主动行为",
    "internal-chat-attachments": "内部对话附件",
    "internal-chat": "内部对话",
    "data-lifecycle": "数据生命周期",
}
_FALLBACK_OPERATION_SUMMARIES = {
    "list_instances": "列出渠道实例",
    "create_instance": "创建渠道实例",
    "update_instance": "更新渠道实例",
    "set_credential": "写入渠道凭证",
    "clear_credential": "清除渠道凭证",
    "test_connection": "测试渠道连接",
    "simulate": "模拟渠道能力协商",
    "deliver": "发送渠道消息",
    "simulate_inbound": "模拟渠道入站事件",
    "list_events": "列出渠道诊断事件",
    "list_identity_mappings": "列出外部身份映射",
    "create_identity_mapping": "创建外部身份映射",
    "update_identity_mapping_status": "更新外部身份映射状态",
    "list_conversation_mappings": "列出外部会话映射",
    "create_conversation_mapping": "创建外部会话映射",
    "update_conversation_mapping_status": "更新外部会话映射状态",
    "list_inbox_events": "列出入站箱事件",
    "replay_inbox_event": "安全重放入站箱事件",
    "list_lifecycle_runs": "列出数据生命周期运行记录",
    "retention_cleanup": "执行保留期清理",
    "orphan_cleanup": "清理 MinIO 孤儿对象",
}
_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")


def stable_operation_id(route: APIRoute) -> str:
    """仅根据 HTTP 方法和公开路径生成不会随处理函数重命名漂移的 ID。"""
    methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
    if len(methods) != 1:
        raise ValueError(f"路由 {route.path_format} 必须且只能声明一个 HTTP 方法")
    path = re.sub(r"\{([^}]+)\}", r"by_\1", route.path_format.strip("/"))
    normalized_path = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_") or "root"
    return f"{methods[0].lower()}_{normalized_path}"


def localize_openapi_routes(application: FastAPI | APIRouter) -> None:
    """统一生成中文操作摘要、标签和成功响应说明。"""
    for route in application.routes:
        if not isinstance(route, APIRoute):
            continue
        route.tags = [
            _TAG_TRANSLATIONS.get(tag, tag) if isinstance(tag, str) else tag for tag in route.tags
        ]
        route.response_description = "请求成功"
        if route.summary and _CHINESE_TEXT.search(route.summary):
            continue
        route.summary = _localized_summary(route)


def _localized_summary(route: APIRoute) -> str:
    explicit = _FALLBACK_OPERATION_SUMMARIES.get(route.name)
    if explicit is not None:
        return explicit

    description = route.description.strip()
    if _CHINESE_TEXT.search(description):
        first_line = description.splitlines()[0].strip()
        first_clause = re.split(r"[。；]", first_line, maxsplit=1)[0].strip("，, ")
        if first_clause:
            return first_clause

    section = route.tags[0] if route.tags else "应用接口"
    return f"执行{section}操作"


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
