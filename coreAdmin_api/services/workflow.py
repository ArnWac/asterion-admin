import json
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coreAdmin_api.admin.registry import admin_site
from coreAdmin_api.admin.serializer import serializer
from coreAdmin_api.models.change_request import ChangeRequest, ChangeRequestStatus


class WorkflowService:

    async def submit_change_request(
        self,
        db: AsyncSession,
        model_name: str,
        operation: str,
        requester_id: uuid.UUID,
        proposed_data: dict | None = None,
        object_id: str | None = None,
        reason: str | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> ChangeRequest:
        original_data = None
        if object_id and operation in ("update", "delete"):
            model_admin = admin_site.get(model_name)
            if model_admin is not None:
                obj = (
                    await db.execute(
                        select(model_admin.model).where(model_admin.model.id == object_id)
                    )
                ).scalar_one_or_none()
                if obj is not None:
                    original_data = json.dumps(serializer.serialize(obj, model_admin))

        cr = ChangeRequest(
            model_name=model_name,
            object_id=str(object_id) if object_id else None,
            operation=operation,
            requester_id=requester_id,
            status=ChangeRequestStatus.pending,
            reason=reason,
            proposed_data=json.dumps(proposed_data) if proposed_data else None,
            original_data=original_data,
            tenant_id=tenant_id,
        )
        db.add(cr)
        await db.commit()
        await db.refresh(cr)
        return cr

    async def review(
        self,
        db: AsyncSession,
        change_request_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        action: str,
        reason: str | None = None,
    ) -> ChangeRequest:
        cr = await self._get(db, change_request_id)
        if cr is None:
            raise ValueError("Change request not found")
        if cr.status != ChangeRequestStatus.pending:
            raise ValueError(f"Change request is already '{cr.status}'")

        cr.reviewer_id = reviewer_id
        if action == "approve":
            cr.status = ChangeRequestStatus.approved
        else:
            cr.status = ChangeRequestStatus.rejected
            cr.rejection_reason = reason
        await db.commit()
        await db.refresh(cr)

        if action == "approve":
            await self._apply(db, cr)

        return cr

    async def _apply(self, db: AsyncSession, cr: ChangeRequest) -> None:
        model_admin = admin_site.get(cr.model_name)
        if model_admin is None:
            return

        proposed = json.loads(cr.proposed_data) if cr.proposed_data else {}
        # Strip protected and readonly fields before applying
        protected = model_admin.all_protected
        readonly_set = set(model_admin.readonly_fields)
        safe = {k: v for k, v in proposed.items() if k not in protected and k not in readonly_set}

        if cr.operation == "create":
            obj = model_admin.model(**safe)
            db.add(obj)
        elif cr.operation == "update" and cr.object_id:
            obj = (
                await db.execute(
                    select(model_admin.model).where(model_admin.model.id == cr.object_id)
                )
            ).scalar_one_or_none()
            if obj is not None:
                for k, v in safe.items():
                    setattr(obj, k, v)
        elif cr.operation == "delete" and cr.object_id:
            obj = (
                await db.execute(
                    select(model_admin.model).where(model_admin.model.id == cr.object_id)
                )
            ).scalar_one_or_none()
            if obj is not None:
                await db.delete(obj)

        cr.status = ChangeRequestStatus.applied
        await db.commit()

    async def revert(
        self,
        db: AsyncSession,
        change_request_id: uuid.UUID,
        reverter_id: uuid.UUID,
        reason: str,
    ) -> ChangeRequest:
        """Restore original data — creates a new auditable state; does not mutate history."""
        cr = await self._get(db, change_request_id)
        if cr is None:
            raise ValueError("Change request not found")
        if cr.status != ChangeRequestStatus.applied:
            raise ValueError(f"Cannot revert change request with status '{cr.status}'")
        if not cr.original_data:
            raise ValueError("No original data available for revert")

        model_admin = admin_site.get(cr.model_name)
        if model_admin is None:
            raise ValueError(f"Model '{cr.model_name}' not found")

        original = json.loads(cr.original_data)
        protected = model_admin.all_protected
        readonly_set = set(model_admin.readonly_fields)

        if cr.operation == "update" and cr.object_id:
            obj = (
                await db.execute(
                    select(model_admin.model).where(model_admin.model.id == cr.object_id)
                )
            ).scalar_one_or_none()
            if obj is not None:
                for k, v in original.items():
                    if k not in protected and k not in readonly_set:
                        setattr(obj, k, v)

        cr.status = ChangeRequestStatus.reverted
        await db.commit()
        await db.refresh(cr)
        return cr

    async def _get(self, db: AsyncSession, cr_id: uuid.UUID) -> ChangeRequest | None:
        result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        return result.scalar_one_or_none()


workflow_service = WorkflowService()
