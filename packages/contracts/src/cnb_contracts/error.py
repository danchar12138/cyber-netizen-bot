"""所有 HTTP API 共用的版本化错误 Envelope。"""

from typing import Literal

from pydantic import BaseModel, Field

from cnb_domain import JsonValue


class ApiErrorDetail(BaseModel):
    """可供客户端定位字段或业务条件的安全错误明细。"""

    field: str | None = None
    message: str
    error_type: str | None = None


class ApiError(BaseModel):
    """不暴露堆栈与敏感数据的错误主体。"""

    code: str
    message: str
    request_id: str
    details: tuple[ApiErrorDetail, ...] = ()
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ApiErrorResponse(BaseModel):
    """统一 HTTP 错误响应。"""

    schema_version: Literal["1"] = "1"
    error: ApiError
