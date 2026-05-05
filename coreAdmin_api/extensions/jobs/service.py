import json
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from coreAdmin_api.extensions.jobs.models import Job, JobStatus


class JobService:

    async def create_job(
        self,
        db: AsyncSession,
        job_type: str,
        initiator_id: uuid.UUID,
        model_name: str | None = None,
        action_name: str | None = None,
        tenant_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        input_data: dict | None = None,
    ) -> Job:
        if idempotency_key:
            existing = await self.get_by_idempotency_key(db, idempotency_key)
            if existing:
                return existing

        job = Job(
            job_type=job_type,
            status=JobStatus.pending,
            initiator_id=initiator_id,
            model_name=model_name,
            action_name=action_name,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            input_data=json.dumps(input_data) if input_data else None,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job

    async def update_status(
        self,
        db: AsyncSession,
        job: Job,
        new_status: JobStatus,
        progress: int | None = None,
        result_summary: str | None = None,
        failure_summary: str | None = None,
        output_data: dict | None = None,
    ) -> Job:
        job.status = new_status
        if progress is not None:
            job.progress = progress
        if result_summary is not None:
            job.result_summary = result_summary
        if failure_summary is not None:
            job.failure_summary = failure_summary
        if output_data is not None:
            job.output_data = json.dumps(output_data)
        await db.commit()
        await db.refresh(job)
        return job

    async def get_by_idempotency_key(self, db: AsyncSession, key: str) -> Job | None:
        result = await db.execute(select(Job).where(Job.idempotency_key == key))
        return result.scalar_one_or_none()

    async def get_by_id(self, db: AsyncSession, job_id: uuid.UUID) -> Job | None:
        result = await db.execute(select(Job).where(Job.id == job_id))
        return result.scalar_one_or_none()


job_service = JobService()
