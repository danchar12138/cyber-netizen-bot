"""跨资源复用的不透明游标编码。"""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InvalidCursorError(ValueError):
    """客户端提交无法解析的分页游标时抛出。"""


@dataclass(frozen=True, slots=True)
class EntityCursor:
    """由时间和统一 UUID 组成的稳定键集游标。"""

    occurred_at: datetime
    entity_id: UUID


def encode_cursor(cursor: EntityCursor) -> str:
    """将键集游标编码为 URL 安全的不透明字符串。"""
    raw = f"{cursor.occurred_at.isoformat()}|{cursor.entity_id}".encode()
    return urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str | None) -> EntityCursor | None:
    """解析不透明游标，并将格式错误转换为稳定领域错误。"""
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        timestamp, entity_id = urlsafe_b64decode(padded).decode().split("|", maxsplit=1)
        occurred_at = datetime.fromisoformat(timestamp)
        if occurred_at.tzinfo is None:
            raise ValueError
        return EntityCursor(occurred_at=occurred_at, entity_id=UUID(entity_id))
    except (UnicodeDecodeError, ValueError) as error:
        raise InvalidCursorError("分页游标无效") from error
