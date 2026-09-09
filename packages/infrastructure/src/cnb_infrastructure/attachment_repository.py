"""附件元数据的内存与 PostgreSQL 持久化实现。"""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cnb_application import AttachmentConflictError
from cnb_domain import Attachment, AttachmentStatus, Message
from cnb_infrastructure.models import (
    AttachmentModel,
    ConversationMember,
    ConversationModel,
)


class MemoryAttachmentRepository:
    """不依赖对象存储的附件元数据仓储，用于测试与本地开发。"""

    def __init__(self) -> None:
        self._attachments: dict[UUID, Attachment] = {}
        self._lock = asyncio.Lock()

    async def create_attachment(self, attachment: Attachment) -> Attachment:
        async with self._lock:
            self._attachments[attachment.id] = attachment
            return attachment

    async def get_attachment_for_user(
        self, attachment_id: UUID, user_id: UUID
    ) -> Attachment | None:
        async with self._lock:
            item = self._attachments.get(attachment_id)
            return item if item is not None and item.owner_id == user_id else None

    async def list_attachments(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> tuple[Attachment, ...]:
        async with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._attachments.values()
                        if item.conversation_id == conversation_id
                        and item.owner_id == user_id
                        and item.status is not AttachmentStatus.DELETED
                    ),
                    key=lambda item: (item.created_at, item.id),
                )
            )

    async def mark_attachment_ready(self, attachment_id: UUID) -> Attachment:
        async with self._lock:
            item = self._required(attachment_id)
            now = datetime.now(UTC)
            updated = replace(item, status=AttachmentStatus.READY, uploaded_at=now)
            self._attachments[attachment_id] = updated
            return updated

    async def reject_attachment(self, attachment_id: UUID, *, reason: str) -> Attachment:
        async with self._lock:
            item = self._required(attachment_id)
            updated = replace(item, status=AttachmentStatus.REJECTED, validation_error=reason)
            self._attachments[attachment_id] = updated
            return updated

    async def attach_to_message(
        self, *, attachment_ids: Sequence[UUID], message: Message, user_id: UUID
    ) -> tuple[Attachment, ...]:
        async with self._lock:
            result: list[Attachment] = []
            now = datetime.now(UTC)
            for attachment_id in attachment_ids:
                item = self._required(attachment_id)
                if item.owner_id != user_id or item.conversation_id != message.conversation_id:
                    raise AttachmentConflictError("附件不属于当前用户或会话")
                if item.client_message_id != message.client_message_id:
                    raise AttachmentConflictError("附件不属于当前消息草稿")
                if item.status is AttachmentStatus.ATTACHED and item.message_id == message.id:
                    result.append(item)
                    continue
                if item.status is not AttachmentStatus.READY:
                    raise AttachmentConflictError("附件尚未就绪")
                updated = replace(
                    item,
                    status=AttachmentStatus.ATTACHED,
                    message_id=message.id,
                    attached_at=now,
                )
                self._attachments[attachment_id] = updated
                result.append(updated)
            return tuple(result)

    async def mark_attachment_deleted(self, attachment_id: UUID, *, user_id: UUID) -> Attachment:
        async with self._lock:
            item = self._required(attachment_id)
            if item.owner_id != user_id:
                raise AttachmentConflictError("当前用户无权操作该附件")
            if item.status is AttachmentStatus.ATTACHED:
                raise AttachmentConflictError("已随消息发送的附件不能删除")
            updated = replace(
                item,
                status=AttachmentStatus.DELETED,
                deleted_at=datetime.now(UTC),
            )
            self._attachments[attachment_id] = updated
            return updated

    async def list_expired_attachments(
        self, *, before: datetime, limit: int
    ) -> tuple[Attachment, ...]:
        async with self._lock:
            return tuple(
                item
                for item in sorted(
                    self._attachments.values(), key=lambda value: (value.expires_at, value.id)
                )
                if item.status in {AttachmentStatus.PENDING, AttachmentStatus.REJECTED}
                and item.expires_at < before
            )[:limit]

    def _required(self, attachment_id: UUID) -> Attachment:
        try:
            return self._attachments[attachment_id]
        except KeyError as error:
            raise AttachmentConflictError(f"附件不存在：{attachment_id}") from error

    def expire_for_test(self, attachment_id: UUID, *, expires_at: datetime) -> None:
        """仅供无基础设施测试推进附件时钟。"""
        self._attachments[attachment_id] = replace(
            self._required(attachment_id), expires_at=expires_at
        )


class SqlAlchemyAttachmentRepository:
    """使用 PostgreSQL 行锁维护附件状态和消息绑定。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_attachment(self, attachment: Attachment) -> Attachment:
        async with self._session_factory() as session, session.begin():
            row = AttachmentModel(
                id=attachment.id,
                tenant_id=attachment.tenant_id,
                owner_id=attachment.owner_id,
                conversation_id=attachment.conversation_id,
                client_message_id=attachment.client_message_id,
                message_id=attachment.message_id,
                original_name=attachment.original_name,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
                sha256=attachment.sha256,
                object_key=attachment.object_key,
                status=attachment.status.value,
                validation_error=attachment.validation_error,
                created_at=attachment.created_at,
                expires_at=attachment.expires_at,
            )
            session.add(row)
            await session.flush()
            return self._attachment(row)

    async def get_attachment_for_user(
        self, attachment_id: UUID, user_id: UUID
    ) -> Attachment | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(AttachmentModel).where(
                    AttachmentModel.id == attachment_id,
                    AttachmentModel.owner_id == user_id,
                )
            )
            return None if row is None else self._attachment(row)

    async def list_attachments(
        self, *, conversation_id: UUID, user_id: UUID
    ) -> tuple[Attachment, ...]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AttachmentModel)
                .join(
                    ConversationMember,
                    ConversationMember.conversation_id == AttachmentModel.conversation_id,
                )
                .join(ConversationModel, ConversationModel.id == AttachmentModel.conversation_id)
                .where(
                    AttachmentModel.conversation_id == conversation_id,
                    AttachmentModel.owner_id == user_id,
                    ConversationMember.user_id == user_id,
                    ConversationModel.deleted_at.is_(None),
                    AttachmentModel.status != AttachmentStatus.DELETED.value,
                )
                .order_by(AttachmentModel.created_at, AttachmentModel.id)
            )
            return tuple(self._attachment(row) for row in rows)

    async def mark_attachment_ready(self, attachment_id: UUID) -> Attachment:
        async with self._session_factory() as session, session.begin():
            row = await self._locked(attachment_id, session)
            row.status = AttachmentStatus.READY.value
            row.uploaded_at = datetime.now(UTC)
            await session.flush()
            return self._attachment(row)

    async def reject_attachment(self, attachment_id: UUID, *, reason: str) -> Attachment:
        async with self._session_factory() as session, session.begin():
            row = await self._locked(attachment_id, session)
            row.status = AttachmentStatus.REJECTED.value
            row.validation_error = reason
            await session.flush()
            return self._attachment(row)

    async def attach_to_message(
        self, *, attachment_ids: Sequence[UUID], message: Message, user_id: UUID
    ) -> tuple[Attachment, ...]:
        async with self._session_factory() as session, session.begin():
            result: list[Attachment] = []
            now = datetime.now(UTC)
            for attachment_id in attachment_ids:
                row = await self._locked(attachment_id, session)
                if (
                    row.owner_id != user_id
                    or row.conversation_id != message.conversation_id
                    or row.client_message_id != message.client_message_id
                ):
                    raise AttachmentConflictError("附件不属于当前用户、会话或消息草稿")
                if row.status == AttachmentStatus.ATTACHED.value and row.message_id == message.id:
                    result.append(self._attachment(row))
                    continue
                if row.status != AttachmentStatus.READY.value:
                    raise AttachmentConflictError("附件尚未就绪")
                row.status = AttachmentStatus.ATTACHED.value
                row.message_id = message.id
                row.attached_at = now
                result.append(self._attachment(row))
            await session.flush()
            return tuple(result)

    async def mark_attachment_deleted(self, attachment_id: UUID, *, user_id: UUID) -> Attachment:
        async with self._session_factory() as session, session.begin():
            row = await self._locked(attachment_id, session)
            if row.owner_id != user_id:
                raise AttachmentConflictError("当前用户无权操作该附件")
            if row.status == AttachmentStatus.ATTACHED.value:
                raise AttachmentConflictError("已随消息发送的附件不能删除")
            row.status = AttachmentStatus.DELETED.value
            row.deleted_at = datetime.now(UTC)
            await session.flush()
            return self._attachment(row)

    async def list_expired_attachments(
        self, *, before: datetime, limit: int
    ) -> tuple[Attachment, ...]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AttachmentModel)
                .where(
                    AttachmentModel.expires_at < before,
                    AttachmentModel.status.in_(
                        [AttachmentStatus.PENDING.value, AttachmentStatus.REJECTED.value]
                    ),
                )
                .order_by(AttachmentModel.expires_at, AttachmentModel.id)
                .limit(limit)
            )
            return tuple(self._attachment(row) for row in rows)

    @staticmethod
    async def _locked(attachment_id: UUID, session: AsyncSession) -> AttachmentModel:
        row = await session.scalar(
            select(AttachmentModel).where(AttachmentModel.id == attachment_id).with_for_update()
        )
        if row is None:
            raise AttachmentConflictError(f"附件不存在：{attachment_id}")
        return row

    @staticmethod
    def _attachment(row: AttachmentModel) -> Attachment:
        return Attachment(
            id=row.id,
            tenant_id=row.tenant_id,
            owner_id=row.owner_id,
            conversation_id=row.conversation_id,
            client_message_id=row.client_message_id,
            message_id=row.message_id,
            original_name=row.original_name,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            object_key=row.object_key,
            status=AttachmentStatus(row.status),
            validation_error=row.validation_error,
            created_at=row.created_at,
            expires_at=row.expires_at,
            uploaded_at=row.uploaded_at,
            attached_at=row.attached_at,
            deleted_at=row.deleted_at,
        )
