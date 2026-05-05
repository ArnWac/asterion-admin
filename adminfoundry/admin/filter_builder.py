from sqlalchemy import or_
import sqlalchemy.types as sqltypes
from adminfoundry.admin.model_admin import ModelAdmin


def _coerce_value(value: str, sa_type: object) -> object:
    """Convert a string query param to the column's native Python type."""
    if isinstance(sa_type, sqltypes.Boolean):
        return value.lower() in ("true", "1", "yes")
    if isinstance(sa_type, (sqltypes.Integer, sqltypes.BigInteger, sqltypes.SmallInteger)):
        return int(value)
    if isinstance(sa_type, (sqltypes.Float, sqltypes.Numeric)):
        return float(value)
    return value


class FilterBuilder:
    def build_filters(self, model_admin: ModelAdmin, params: dict) -> list:
        """Exact-match filters for each `filter_fields` key found in params."""
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(model_admin.model)
        col_types = {c.name: c.type for c in mapper.columns}

        filters = []
        for field in model_admin.filter_fields:
            raw = params.get(field)
            if raw is None:
                continue
            col = getattr(model_admin.model, field, None)
            if col is None:
                continue
            value = _coerce_value(raw, col_types.get(field))
            filters.append(col == value)
        return filters

    def build_search(self, model_admin: ModelAdmin, q: str | None):
        """ILIKE search across all `search_fields`."""
        if not q or not model_admin.search_fields:
            return None
        conditions = []
        for field in model_admin.search_fields:
            col = getattr(model_admin.model, field, None)
            if col is not None:
                conditions.append(col.ilike(f"%{q}%"))
        return or_(*conditions) if conditions else None

    def build_ordering(self, model_admin: ModelAdmin, order_by: str | None):
        """ORDER BY clause.  Prefix field name with '-' for DESC."""
        order_col = order_by or (model_admin.ordering[0] if model_admin.ordering else None)
        if not order_col:
            return None
        desc = order_col.startswith("-")
        col_name = order_col.lstrip("-")
        col = getattr(model_admin.model, col_name, None)
        if col is None:
            return None
        return col.desc() if desc else col.asc()


filter_builder = FilterBuilder()
