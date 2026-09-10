"""OpenAPI 契约导出的确定性与生成前置条件测试。"""

import json
from pathlib import Path

from fastapi import FastAPI

from cnb_api.openapi import export_openapi_document, render_openapi_document, stable_operation_id


def _test_application() -> FastAPI:
    application = FastAPI(title="中文契约测试", generate_unique_id_function=stable_operation_id)

    @application.get("/items/{item_id}")
    async def _get_item(  # pyright: ignore[reportUnusedFunction]
        item_id: str,
    ) -> dict[str, str]:
        return {"item_id": item_id}

    return application


def test_openapi_document_is_deterministic_and_preserves_chinese() -> None:
    application = _test_application()

    first = render_openapi_document(application)
    second = render_openapi_document(application)

    assert first == second
    assert first.endswith("\n")
    assert "中文契约测试" in first
    assert (
        json.loads(first)["paths"]["/items/{item_id}"]["get"]["operationId"]
        == "get_items_by_item_id"
    )


def test_openapi_export_creates_parent_directory(tmp_path: Path) -> None:
    output = tmp_path / "contracts" / "openapi.json"

    export_openapi_document(_test_application(), output)

    assert output.read_text(encoding="utf-8") == render_openapi_document(_test_application())


def test_production_openapi_operation_ids_are_unique_and_path_stable() -> None:
    from cnb_api.main import app

    operations = [
        operation
        for path_item in app.openapi()["paths"].values()
        for method, operation in path_item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    operation_ids = [operation["operationId"] for operation in operations]

    assert len(operation_ids) == len(set(operation_ids))
    assert "get_api_v1_system_overview" in operation_ids
    assert (
        app.openapi()["paths"]["/api/v1/system/overview"]["get"]["responses"]["403"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/ApiErrorResponse"
    )
