import uuid
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from coreAdmin_api.admin.schema_builder import schema_builder
from coreAdmin_api.admin.serializer import serializer
from coreAdmin_api.authz.policy_engine import policy_engine
from coreAdmin_api.extensions.jobs.schemas import ImportResult, ImportRowResult


class ImportExportService:

    async def run_import(
        self,
        db: AsyncSession,
        model_admin,
        rows: list[dict],
        dry_run: bool,
        current_user,
        token_payload: dict,
        job_id: uuid.UUID | None = None,
    ) -> ImportResult:
        create_schema = schema_builder.build_create_schema(model_admin)
        results: list[ImportRowResult] = []
        success_count = 0
        error_count = 0

        for idx, row in enumerate(rows):
            errors: list[str] = []
            protected = model_admin.all_protected
            readonly_set = set(model_admin.readonly_fields)

            for key in row:
                if key in protected:
                    errors.append(f"Field '{key}' is protected")
                elif key in readonly_set:
                    errors.append(f"Field '{key}' is readonly")
                else:
                    fp = policy_engine.evaluate_field(current_user, model_admin, key, token_payload)
                    if not fp.can_edit:
                        errors.append(f"Field '{key}' is not editable")

            validated = None
            if not errors:
                try:
                    validated = create_schema.model_validate(row)
                except ValidationError as exc:
                    for e in exc.errors():
                        loc = ".".join(str(x) for x in e["loc"])
                        errors.append(f"{loc}: {e['msg']}")

            if errors:
                error_count += 1
                results.append(ImportRowResult(row_index=idx, success=False, errors=errors))
                continue

            if not dry_run and validated is not None:
                try:
                    obj = model_admin.model(**validated.model_dump(exclude_none=True))
                    db.add(obj)
                    await db.flush()
                    results.append(ImportRowResult(
                        row_index=idx, success=True,
                        data=serializer.serialize(obj, model_admin),
                    ))
                    success_count += 1
                except IntegrityError as exc:
                    await db.rollback()
                    error_count += 1
                    results.append(ImportRowResult(
                        row_index=idx, success=False, errors=[str(exc.orig)],
                    ))
                    continue
            else:
                success_count += 1
                results.append(ImportRowResult(row_index=idx, success=True))

        if not dry_run and success_count > 0:
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                for r in results:
                    if r.success:
                        r.success = False
                        r.errors = ["Commit failed: " + str(exc.orig)]
                error_count = len(rows)
                success_count = 0

        return ImportResult(
            dry_run=dry_run,
            total=len(rows),
            success_count=success_count,
            error_count=error_count,
            rows=results,
            job_id=job_id,
        )

    async def run_export(
        self,
        db: AsyncSession,
        model_admin,
        current_user,
        token_payload: dict,
        q: str | None = None,
    ) -> list[dict]:
        from sqlalchemy import select
        from coreAdmin_api.admin.filter_builder import filter_builder

        stmt = select(model_admin.model)

        search = filter_builder.build_search(model_admin, q)
        if search is not None:
            stmt = stmt.where(search)

        rf = policy_engine.get_record_filter(current_user, model_admin, token_payload)
        if rf is not None:
            stmt = stmt.where(rf)

        items = (await db.execute(stmt)).scalars().all()

        result = []
        for obj in items:
            row = serializer.serialize(obj, model_admin)
            for field_name in list(row.keys()):
                fp = policy_engine.evaluate_field(current_user, model_admin, field_name, token_payload)
                if not fp.can_view:
                    del row[field_name]
            result.append(row)

        return result


import_export_service = ImportExportService()
