import uuid
from datetime import datetime
from adminfoundry.admin.model_admin import ModelAdmin


def _serialize_value(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


class Serializer:
    def serialize(self, obj: object, model_admin: ModelAdmin) -> dict:
        excluded = model_admin.all_protected
        result: dict = {}
        for col in obj.__table__.columns:  # type: ignore[attr-defined]
            if col.name in excluded:
                continue
            result[col.name] = _serialize_value(getattr(obj, col.name))

        # Include inline relation data (already loaded via lazy="selectin" or similar)
        for attr in getattr(model_admin, "inline_fields", []):
            related = getattr(obj, attr, None)
            if related is None:
                result[attr] = None
            elif isinstance(related, list):
                result[attr] = [
                    {
                        col.name: _serialize_value(getattr(rel_obj, col.name))
                        for col in rel_obj.__table__.columns
                        if col.name not in excluded
                    }
                    for rel_obj in related
                ]
            else:
                result[attr] = {
                    col.name: _serialize_value(getattr(related, col.name))
                    for col in related.__table__.columns
                    if col.name not in excluded
                }
        return result

    def serialize_many(self, objs: list, model_admin: ModelAdmin) -> list[dict]:
        return [self.serialize(obj, model_admin) for obj in objs]


serializer = Serializer()
