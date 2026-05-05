import uuid
from datetime import datetime
from adminfoundry.admin.model_admin import ModelAdmin


class Serializer:
    def serialize(self, obj: object, model_admin: ModelAdmin) -> dict:
        excluded = model_admin.all_protected
        result: dict = {}
        for col in obj.__table__.columns:  # type: ignore[attr-defined]
            if col.name in excluded:
                continue
            value = getattr(obj, col.name)
            if isinstance(value, uuid.UUID):
                value = str(value)
            elif isinstance(value, datetime):
                value = value.isoformat()
            result[col.name] = value
        return result

    def serialize_many(self, objs: list, model_admin: ModelAdmin) -> list[dict]:
        return [self.serialize(obj, model_admin) for obj in objs]


serializer = Serializer()
