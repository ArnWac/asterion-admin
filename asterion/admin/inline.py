"""Inline admin primitive + transactional write helper.

An :class:`InlineAdmin` describes a child model that should be edited
inline with its parent admin. Declared statically on a parent admin's
``inlines`` list::

    class CommentInline(InlineAdmin):
        model = Comment
        fk_name = "post_id"
        fields = ["author", "body", "is_public"]
        readonly_fields = ["created_at"]
        extra = 1
        can_delete = True
        ordering = ["created_at"]

    class PostAdmin(ModelAdmin):
        model = Post
        inlines = [CommentInline]

C1 covers the declaration + contract surface only. The transactional
parent/child create+update+delete plumbing lands in C2.

Class-level attribute semantics mirror :class:`ModelAdmin`:

* ``model`` — the SQLAlchemy declarative class for the child rows.
* ``fk_name`` — the column on ``model`` that points back at the
  parent. Must be a real column name; the framework asserts it
  exists at validation time.
* ``fields`` — order matters; the UI renders rows in this column
  order. ``[]`` means "all writable non-protected columns" — same
  default rule as a standalone ModelAdmin.
* ``readonly_fields`` — columns the user can see but not edit
  inside the inline row (typically timestamps).
* ``extra`` — how many blank rows the UI pre-renders for new
  entries. ``0`` means "no blank rows, user clicks Add".
* ``max_num`` — hard cap on rows per parent (``None`` = unlimited).
* ``can_delete`` — whether the inline shows a per-row delete
  control.
* ``ordering`` — list of column names; client-side sort key (or
  server-side query order in C2).

Subclass-default isolation works the same way ``ModelAdmin`` handles
it: ``__init_subclass__`` re-points every mutable default at a fresh
empty list / mapping per subclass so two inlines never share state.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from asterion.admin.context import AdminContext
    from asterion.admin.policy import AdminPolicy


class InlineAdmin:
    """Static declaration of one inline child relationship.

    Defaults reproduce the legacy "no override" behavior — see the
    module docstring for the per-attribute contract.
    """

    model: type
    fk_name: str | None = None

    fields: list[str] = []
    readonly_fields: list[str] = []
    ordering: list[str] = []

    extra: int = 0
    max_num: int | None = None
    can_delete: bool = True

    #: Optional :class:`~asterion.admin.policy.AdminPolicy` enforced
    #: on each inline child row independently of the parent's policy
    #: (Roadmap 2.2 / Gap-Analysis §4 "permission checks per inline").
    #: ``None`` means "inherit the parent's gate" — the parent admin's
    #: own policy already decided whether the whole save is allowed, so
    #: a child without its own policy adds no further restriction.
    #: ``can_create`` is consulted for new rows, ``can_update_object``
    #: for edits, ``can_delete_object`` for removals.
    policy: AdminPolicy | None = None

    #: Display label shown above the inline section. Defaults to the
    #: child model's class name (pluralized) at contract-build time.
    label: str | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for attr in ("fields", "readonly_fields", "ordering"):
            if attr not in cls.__dict__:
                setattr(cls, attr, [])

    @property
    def model_name(self) -> str:
        return self.model.__tablename__

    @property
    def display_label(self) -> str:
        if self.label:
            return self.label
        return self.model.__name__


# ---------------------------------------------------------------------------
# Transactional writer (C2)
# ---------------------------------------------------------------------------


def _resolve_inline_instance(entry: type[InlineAdmin] | InlineAdmin) -> InlineAdmin:
    return entry() if isinstance(entry, type) else entry


def _inline_index(model_admin) -> dict[str, InlineAdmin]:
    """Map ``child_tablename → InlineAdmin instance`` for a parent admin.

    The key is the child model's ``__tablename__`` — that's the
    discriminator on the wire (payload's ``inlines.<table_name>``)."""
    out: dict[str, InlineAdmin] = {}
    for entry in getattr(model_admin, "inlines", []) or []:
        inline = _resolve_inline_instance(entry)
        out[inline.model_name] = inline
    return out


def _writable_columns_for_inline(inline: InlineAdmin) -> set[str]:
    """The columns a write payload may touch for one inline row.

    Strictly the inline's ``fields`` list (when declared) minus
    ``readonly_fields``. When ``fields`` is empty, fall back to all
    columns on the child model except the fk column itself."""
    from sqlalchemy import inspect as sa_inspect

    declared = list(getattr(inline, "fields", []) or [])
    readonly = set(getattr(inline, "readonly_fields", []) or [])

    if declared:
        return {name for name in declared if name not in readonly}

    mapper = sa_inspect(inline.model)
    fk_name = getattr(inline, "fk_name", None)
    return {col.name for col in mapper.columns if col.name != fk_name and col.name not in readonly}


def _coerce_pk(model, raw: Any):
    """Use the framework's existing PK coercion so the wire-format
    accepts the same shape the top-level CRUD route does."""
    from asterion.crud.query import coerce_primary_key_value

    return coerce_primary_key_value(model, str(raw))


async def _fetch_inline_record(
    session: AsyncSession,
    inline: InlineAdmin,
    raw_id: Any,
):
    from sqlalchemy import select

    from asterion.crud.query import primary_key_column

    pk_col = primary_key_column(inline.model)
    pk_value = _coerce_pk(inline.model, raw_id)
    result = await session.execute(select(inline.model).where(pk_col == pk_value))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inline {inline.model_name} row {raw_id!r} not found.",
        )
    return row


def _parent_pk_value(parent: Any) -> Any:
    """The value to stamp into the inline's fk column.

    The framework currently assumes single-column primary keys on
    parents (same assumption the CRUD route makes). Composite-key
    parents would need explicit inline mapping."""
    from sqlalchemy import inspect as sa_inspect

    mapper = sa_inspect(parent.__class__)
    pk_cols = list(mapper.primary_key)
    if not pk_cols:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Parent {parent.__class__.__name__} has no primary key.",
        )
    return getattr(parent, pk_cols[0].name)


def split_parent_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Pull the ``inlines`` block out of a parent payload.

    Returns ``(parent_payload, inline_payload)``. The parent payload
    no longer contains the ``inlines`` key. The inline payload is
    a ``{child_tablename: [row, ...]}`` mapping; missing or
    ``None`` ``inlines`` keys yield an empty dict.
    """
    if "inlines" not in payload:
        return dict(payload), {}
    inline_block = payload.get("inlines") or {}
    if not isinstance(inline_block, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="'inlines' must be an object keyed by child table name.",
        )
    parent_payload = {k: v for k, v in payload.items() if k != "inlines"}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for tablename, rows in inline_block.items():
        if not isinstance(rows, list):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"inlines['{tablename}'] must be a list of row objects.",
            )
        normalized[tablename] = list(rows)
    return parent_payload, normalized


async def fetch_inline_children(
    session: AsyncSession,
    parent: Any,
    model_admin,
) -> dict[str, list[dict[str, Any]]]:
    """Load and serialize the inline child rows for one parent.

    Returns a mapping ``{child_tablename: [row_dict, ...]}`` ready to
    drop into the parent's serialized payload under the ``inlines``
    key. Empty when the admin has no inlines configured.

    The function honors per-inline ``ordering`` (column-name list with
    ``-`` prefix for descending). Rows are serialized via the same
    ``Serializer`` the top-level read path uses, so protected fields
    on the child model don't leak. ``fk_name`` columns are kept on the
    wire — they identify which parent the row belongs to and the UI
    needs them to round-trip an update.
    """
    from sqlalchemy import select

    from asterion.schemas.serialization.serializer import serialize_records

    index = _inline_index(model_admin)
    if not index:
        return {}

    parent_id = _parent_pk_value(parent)
    out: dict[str, list[dict[str, Any]]] = {}

    for tablename, inline in index.items():
        fk_name = inline.fk_name
        if not fk_name:
            out[tablename] = []
            continue

        fk_col = getattr(inline.model, fk_name, None)
        if fk_col is None:
            out[tablename] = []
            continue

        stmt = select(inline.model).where(fk_col == parent_id)

        # Apply per-inline ordering (descending with ``-`` prefix).
        for raw in list(getattr(inline, "ordering", []) or []):
            descending = raw.startswith("-")
            name = raw[1:] if descending else raw
            col = getattr(inline.model, name, None)
            if col is None:
                continue
            stmt = stmt.order_by(col.desc() if descending else col.asc())

        # Mirror a tiny `ModelAdmin` shape to the serializer — the
        # serializer reads ``all_protected`` + ``calculated_fields``,
        # which inlines don't carry, so an inline-shaped proxy keeps
        # the call site small.
        from types import SimpleNamespace

        rows = (await session.execute(stmt)).scalars().all()
        # Apply globally-protected fields (e.g. ``hashed_password``) to
        # inline rows too — a misconfigured inline must not leak a
        # framework-level secret. Per-inline ``protected_fields`` would
        # land here later; the C1 surface doesn't expose it yet.
        from asterion.security.protected_fields import get_registry

        proxy = SimpleNamespace(
            all_protected=get_registry().as_frozenset(),
            calculated_fields={},
        )
        out[tablename] = serialize_records(list(rows), proxy)  # type: ignore[arg-type]

    return out


async def _enforce_inline_policy(
    inline: InlineAdmin,
    *,
    action: str,
    obj: Any | None,
    ctx: AdminContext | None,
    tablename: str,
) -> None:
    """Run the inline's own :class:`AdminPolicy` gate for one row.

    No-op when the inline has no policy or no ctx was supplied — the
    parent admin's policy already decided whether the save is allowed
    at all, so a child without its own policy adds no restriction.

    ``action`` is ``"create" | "update" | "delete"``; ``obj`` is the
    child row for update/delete and ``None`` for create.
    """
    if ctx is None:
        return
    policy = getattr(inline, "policy", None)
    if policy is None:
        return
    if action == "create":
        allowed = await policy.can_create(ctx)
    elif action == "update":
        allowed = await policy.can_update_object(obj, ctx)
    else:  # delete
        allowed = await policy.can_delete_object(obj, ctx)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Inline '{tablename}' {action} not permitted for this caller.",
        )


async def process_inline_writes(
    session: AsyncSession,
    parent: Any,
    model_admin,
    inline_payload: Mapping[str, list[dict[str, Any]]],
    *,
    ctx: AdminContext | None = None,
) -> None:
    """Apply inline writes for one parent record.

    Per-row dispatch:
    * row carries ``"_delete": True`` and ``"id"`` → delete that
      child row.
    * row carries ``"id"`` (without ``_delete``) → update that child.
    * row has no ``id`` → create a new child stamped with the
      parent's primary key.

    The whole call runs inside the caller's session — the caller
    owns the transaction. Any exception bubbles up and the outer
    transaction rolls back (parent + children atomic).

    ``ctx`` (Roadmap 2.2) enables per-inline :class:`AdminPolicy`
    enforcement: each child row is checked against the inline's own
    ``policy`` (``can_create`` / ``can_update_object`` /
    ``can_delete_object``) before the write. ``None`` skips all policy
    checks — legacy / non-HTTP callers behave as before.

    Unknown table names in the payload raise 422 — silent ignore
    would hide schema typos.
    """
    if not inline_payload:
        return
    index = _inline_index(model_admin)

    unknown = set(inline_payload) - set(index)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Unknown inline target(s).",
                "fields": sorted(unknown),
            },
        )

    for tablename, rows in inline_payload.items():
        inline = index[tablename]
        writable = _writable_columns_for_inline(inline)
        fk_name = inline.fk_name
        if not fk_name:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Inline '{tablename}' has no fk_name configured.",
            )
        parent_id = _parent_pk_value(parent)
        max_num = getattr(inline, "max_num", None)
        if max_num is not None and len(rows) > max_num:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Inline '{tablename}' exceeds max_num={max_num}.",
            )

        for raw_row in rows:
            if not isinstance(raw_row, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Inline '{tablename}' row must be an object.",
                )
            row = dict(raw_row)
            wants_delete = bool(row.pop("_delete", False))
            row_id = row.pop("id", None)

            if wants_delete:
                if row_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=f"Inline '{tablename}' delete requires an id.",
                    )
                if not inline.can_delete:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Inline '{tablename}' does not allow deletion.",
                    )
                child = await _fetch_inline_record(session, inline, row_id)
                await _enforce_inline_policy(
                    inline, action="delete", obj=child, ctx=ctx, tablename=tablename
                )
                await session.delete(child)
                continue

            unknown_cols = set(row) - writable
            if unknown_cols:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "message": f"Inline '{tablename}' has unknown fields.",
                        "fields": sorted(unknown_cols),
                    },
                )

            if row_id is None:
                # Create.
                await _enforce_inline_policy(
                    inline, action="create", obj=None, ctx=ctx, tablename=tablename
                )
                row[fk_name] = parent_id
                child = inline.model(**row)
                session.add(child)
            else:
                # Update — fetch first so the policy can inspect the row.
                child = await _fetch_inline_record(session, inline, row_id)
                await _enforce_inline_policy(
                    inline, action="update", obj=child, ctx=ctx, tablename=tablename
                )
                for k, v in row.items():
                    setattr(child, k, v)

    await session.flush()
