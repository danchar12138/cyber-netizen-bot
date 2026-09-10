"""会话附件预签名上传、校验、绑定、预览与清理用例。"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Protocol, cast
from uuid import UUID, uuid4

from cnb_application.configuration_service import ConfigurationService
from cnb_application.conversation_service import ConversationRepository
from cnb_application.object_storage import (
    ObjectInspectionError,
    ObjectNotFoundError,
    ObjectStorage,
    UploadGrant,
)
from cnb_domain import Attachment, AttachmentStatus, DevelopmentIdentity, Message

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AttachmentNotFoundError(LookupError):
    """当前用户不能访问目标附件时抛出。"""


class AttachmentValidationError(ValueError):
    """附件声明、对象内容或生命周期不符合约束时抛出。"""


class AttachmentConflictError(RuntimeError):
    """附件状态或消息绑定关系发生冲突时抛出。"""


@dataclass(frozen=True, slots=True)
class AttachmentReservation:
    """已持久化附件与对应直传授权。"""

    attachment: Attachment
    upload: UploadGrant


class AttachmentRepository(Protocol):
    """附件元数据持久化边界。"""

    async def create_attachment(self, attachment: Attachment) -> Attachment: ...

    async def get_attachment_for_user(
        self, attachment_id: UUID, user_id: UUID
    ) -> Attachment | None: ...

    async def list_attachments(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> tuple[Attachment, ...]: ...

    async def mark_attachment_ready(self, attachment_id: UUID) -> Attachment: ...

    async def reject_attachment(self, attachment_id: UUID, *, reason: str) -> Attachment: ...

    async def attach_to_message(
        self, *, attachment_ids: Sequence[UUID], message: Message, user_id: UUID
    ) -> tuple[Attachment, ...]: ...

    async def mark_attachment_deleted(
        self, attachment_id: UUID, *, user_id: UUID
    ) -> Attachment: ...

    async def list_expired_attachments(
        self, *, before: datetime, limit: int
    ) -> tuple[Attachment, ...]: ...


class AttachmentService:
    """实施附件白名单、大小、摘要、权限和生命周期策略。"""

    def __init__(
        self,
        *,
        repository: AttachmentRepository,
        object_storage: ObjectStorage,
        conversation_repository: ConversationRepository,
        configuration_service: ConfigurationService,
        identity: DevelopmentIdentity,
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._conversation_repository = conversation_repository
        self._configuration_service = configuration_service
        self._identity = identity

    async def reserve(
        self,
        *,
        conversation_id: UUID,
        client_message_id: UUID,
        original_name: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> AttachmentReservation:
        await self._require_conversation(conversation_id)
        name = self._normalize_name(original_name)
        mime = content_type.strip().lower().split(";", maxsplit=1)[0]
        digest = sha256.strip().lower()
        max_size, allowed_types, expiry_seconds = await self._upload_policy()
        if size_bytes <= 0:
            raise AttachmentValidationError("附件不能为空")
        if size_bytes > max_size:
            raise AttachmentValidationError(f"附件不能超过 {max_size} 字节")
        if mime not in allowed_types:
            raise AttachmentValidationError(f"不支持附件类型：{mime or '未声明'}")
        self._validate_extension(name, mime)
        if not _SHA256_PATTERN.fullmatch(digest):
            raise AttachmentValidationError("附件 SHA-256 必须是 64 位十六进制字符串")

        attachment_id = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=expiry_seconds)
        object_key = (
            f"tenants/{self._identity.tenant_id}/attachments/"
            f"{attachment_id}/{self._safe_object_name(name)}"
        )
        attachment = await self._repository.create_attachment(
            Attachment(
                id=attachment_id,
                tenant_id=self._identity.tenant_id,
                owner_id=self._identity.user_id,
                conversation_id=conversation_id,
                client_message_id=client_message_id,
                message_id=None,
                original_name=name,
                content_type=mime,
                size_bytes=size_bytes,
                sha256=digest,
                object_key=object_key,
                status=AttachmentStatus.PENDING,
                validation_error=None,
                created_at=now,
                expires_at=expires_at,
                uploaded_at=None,
                attached_at=None,
                deleted_at=None,
            )
        )
        upload = await self._object_storage.presign_upload(
            object_key=object_key,
            content_type=mime,
            sha256=digest,
            expires_seconds=expiry_seconds,
        )
        return AttachmentReservation(attachment=attachment, upload=upload)

    async def complete(self, attachment_id: UUID) -> Attachment:
        attachment = await self.get(attachment_id)
        if attachment.status in {AttachmentStatus.READY, AttachmentStatus.ATTACHED}:
            return attachment
        if attachment.status is not AttachmentStatus.PENDING:
            raise AttachmentValidationError("附件已不能完成上传")
        if attachment.expires_at <= datetime.now(UTC):
            await self._reject_and_delete(attachment, "上传授权已过期")
            raise AttachmentValidationError("上传授权已过期，请重新选择文件")
        try:
            observed = await self._object_storage.inspect_object(
                attachment.object_key,
                max_bytes=attachment.size_bytes,
            )
        except ObjectNotFoundError as error:
            raise AttachmentValidationError("对象尚未上传完成") from error
        except ObjectInspectionError as error:
            await self._reject_and_delete(attachment, "对象内容未通过安全检查")
            raise AttachmentValidationError("对象内容未通过安全检查") from error
        mismatch: str | None = None
        if observed.size_bytes != attachment.size_bytes:
            mismatch = "对象大小与预留声明不一致"
        elif observed.content_type.lower().split(";", maxsplit=1)[0] != attachment.content_type:
            mismatch = "对象媒体类型与预留声明不一致"
        elif observed.sha256 != attachment.sha256:
            mismatch = "对象 SHA-256 与预留声明不一致"
        elif observed.metadata_sha256 is not None and observed.metadata_sha256 != attachment.sha256:
            mismatch = "对象摘要元数据与预留声明不一致"
        elif observed.detected_content_type != attachment.content_type:
            mismatch = "对象真实类型与预留声明不一致"
        if mismatch is not None:
            await self._reject_and_delete(attachment, mismatch)
            raise AttachmentValidationError(mismatch)
        return await self._repository.mark_attachment_ready(attachment.id)

    async def get(self, attachment_id: UUID) -> Attachment:
        attachment = await self._repository.get_attachment_for_user(
            attachment_id, self._identity.user_id
        )
        if attachment is None or attachment.status is AttachmentStatus.DELETED:
            raise AttachmentNotFoundError(f"附件不存在：{attachment_id}")
        return attachment

    async def list_for_conversation(self, conversation_id: UUID) -> tuple[Attachment, ...]:
        await self._require_conversation(conversation_id)
        return await self._repository.list_attachments(
            conversation_id=conversation_id, user_id=self._identity.user_id
        )

    async def prepare_message_attachments(
        self,
        *,
        conversation_id: UUID,
        client_message_id: UUID,
        attachment_ids: Sequence[UUID],
    ) -> tuple[Attachment, ...]:
        if len(set(attachment_ids)) != len(attachment_ids):
            raise AttachmentValidationError("附件 ID 不能重复")
        attachments = tuple([await self.get(attachment_id) for attachment_id in attachment_ids])
        for attachment in attachments:
            if attachment.conversation_id != conversation_id:
                raise AttachmentValidationError("附件不属于当前会话")
            if attachment.client_message_id != client_message_id:
                raise AttachmentValidationError("附件不属于当前消息草稿")
            if attachment.status not in {AttachmentStatus.READY, AttachmentStatus.ATTACHED}:
                raise AttachmentValidationError(f"附件尚未就绪：{attachment.original_name}")
        return attachments

    async def attach_to_message(
        self, *, attachment_ids: Sequence[UUID], message: Message
    ) -> tuple[Attachment, ...]:
        if not attachment_ids:
            return ()
        return await self._repository.attach_to_message(
            attachment_ids=attachment_ids,
            message=message,
            user_id=self._identity.user_id,
        )

    async def preview(self, attachment_id: UUID) -> tuple[Attachment, str]:
        attachment = await self.get(attachment_id)
        if attachment.status not in {AttachmentStatus.READY, AttachmentStatus.ATTACHED}:
            raise AttachmentValidationError("附件尚未通过校验")
        url = await self._object_storage.presign_download(
            object_key=attachment.object_key,
            download_name=attachment.original_name,
            expires_seconds=300,
        )
        return attachment, url

    async def delete(self, attachment_id: UUID) -> Attachment:
        attachment = await self.get(attachment_id)
        if attachment.status is AttachmentStatus.ATTACHED:
            raise AttachmentValidationError("已随消息发送的附件不能直接删除")
        await self._object_storage.delete_object(attachment.object_key)
        return await self._repository.mark_attachment_deleted(
            attachment.id, user_id=self._identity.user_id
        )

    async def cleanup_expired(self, *, limit: int = 100) -> int:
        stale = await self._repository.list_expired_attachments(
            before=datetime.now(UTC), limit=limit
        )
        for attachment in stale:
            await self._object_storage.delete_object(attachment.object_key)
            await self._repository.mark_attachment_deleted(
                attachment.id, user_id=attachment.owner_id
            )
        return len(stale)

    async def _upload_policy(self) -> tuple[int, frozenset[str], int]:
        configuration = await self._configuration_service.resolve_effective(
            tenant_id=self._identity.tenant_id,
            agent_id=self._identity.agent_id,
            user_id=self._identity.user_id,
        )
        max_size = configuration.values["attachment.max_size_bytes"]
        allowed_types = configuration.values["attachment.allowed_mime_types"]
        expiry_seconds = configuration.values["attachment.upload_expiry_seconds"]
        if (
            not isinstance(max_size, int)
            or isinstance(max_size, bool)
            or not isinstance(expiry_seconds, int)
            or isinstance(expiry_seconds, bool)
            or not isinstance(allowed_types, list)
            or not all(isinstance(item, str) for item in allowed_types)
        ):
            raise AttachmentValidationError("生效附件配置类型无效")
        return max_size, frozenset(cast(list[str], allowed_types)), expiry_seconds

    async def _require_conversation(self, conversation_id: UUID) -> None:
        conversation = await self._conversation_repository.get_conversation_for_user(
            conversation_id, self._identity.user_id
        )
        if conversation is None:
            raise AttachmentNotFoundError(f"会话不存在：{conversation_id}")

    async def _reject_and_delete(self, attachment: Attachment, reason: str) -> None:
        await self._repository.reject_attachment(attachment.id, reason=reason)
        await self._object_storage.delete_object(attachment.object_key)

    @staticmethod
    def _normalize_name(value: str) -> str:
        normalized = unicodedata.normalize("NFC", value.strip().replace("\\", "/"))
        name = PurePosixPath(normalized).name
        if not name or name in {".", ".."} or any(ord(character) < 32 for character in name):
            raise AttachmentValidationError("附件文件名无效")
        return name[:255]

    @staticmethod
    def _safe_object_name(name: str) -> str:
        suffix = PurePosixPath(name).suffix.lower()
        safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,16}", suffix) else ""
        return f"content{safe_suffix}"

    @staticmethod
    def _validate_extension(name: str, content_type: str) -> None:
        allowed_extensions = {
            "image/png": {".png"},
            "image/jpeg": {".jpg", ".jpeg"},
            "image/webp": {".webp"},
            "image/gif": {".gif"},
            "text/plain": {".txt", ".log"},
            "text/markdown": {".md", ".markdown"},
            "text/csv": {".csv"},
            "application/json": {".json"},
            "application/pdf": {".pdf"},
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
        }
        suffix = PurePosixPath(name).suffix.casefold()
        if suffix not in allowed_extensions.get(content_type, set()):
            raise AttachmentValidationError("附件扩展名与声明媒体类型不匹配")
