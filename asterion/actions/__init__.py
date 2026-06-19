"""Admin action descriptors and the canonical BulkDeleteAction.

Subclass :class:`AdminAction` and add the instance to ``ModelAdmin.actions``
to expose a custom action at ``POST /api/v1/admin/{resource}/_actions/{action_name}``.

Actions must use ``session.flush()`` to materialize changes. The request
session's transaction is committed by ``get_async_session`` after the action
returns successfully, so calling ``commit()`` inside an action would
short-circuit the request's transaction lifecycle.

Two dispatch styles coexist:

* **Legacy** — override :meth:`AdminAction.execute(records, session, user)`.
  Existing actions in the wild keep working unchanged.
* **Typed (C3)** — set ``input_schema = MyPydanticModel`` and override
  :meth:`AdminAction.run(objects, data, ctx)`. The router validates the
  request body's ``data`` field through the pydantic model before
  dispatch. The router prefers ``run`` when the subclass has overridden
  it; otherwise it falls back to ``execute``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from pydantic import BaseModel

    from asterion.admin.context import AdminContext


class AdminAction:
    """Base class for an admin action.

    Subclasses set ``name``, ``label`` and implement either
    :meth:`execute` (legacy signature) or :meth:`run` (typed C3
    signature). The router picks whichever the subclass overrides.

    ``confirm`` — UI hint that the action should prompt before firing.
    Pure metadata; the framework does not enforce it.

    ``bulk`` — distinguishes bulk-style actions (one button operating
    on multiple selected rows) from single-record actions. Pure
    metadata for now; future row-level actions will toggle it.

    ``input_schema`` — pydantic model class describing the action's
    extra inputs. When set, the request body must include a ``data``
    object that validates against this model; ``run`` then receives
    the validated pydantic instance via the ``data`` argument.
    """

    name: str = ""
    label: str = ""
    confirm: bool = False
    bulk: bool = True
    input_schema: type[BaseModel] | None = None

    async def execute(
        self,
        records: list[Any],
        session: AsyncSession,
        user: Any,
    ) -> dict[str, Any]:
        """Legacy entry point.

        Subclasses that override this keep the v1 contract. The router
        invokes this when the subclass does not also override
        :meth:`run`. Raising :class:`NotImplementedError` here keeps an
        action defined with neither method honest — the routes will
        surface a 500 rather than silently no-op.
        """
        raise NotImplementedError(f"Action {self.name!r} has no execute() implementation")

    async def run(
        self,
        objects: list[Any],
        data: Any,
        ctx: AdminContext,
    ) -> dict[str, Any]:
        """Typed entry point introduced in C3.

        Default delegates to :meth:`execute` so existing callers see
        no behaviour change. New actions override this directly and
        receive:

        * ``objects`` — same list of fetched rows.
        * ``data`` — validated pydantic instance (when
          ``input_schema`` is set) or an empty dict.
        * ``ctx`` — the full :class:`AdminContext`, replacing the
          legacy ``user`` argument and giving access to tenant,
          permissions, request, etc.

        The default implementation forwards to ``execute`` for
        backward compatibility — subclasses that override this method
        do not need to call ``super().run``.
        """
        # Pull the session out of ctx.request for backward compat —
        # legacy execute() expects (records, session, user). Custom
        # run() implementations don't need this branch.
        from asterion.db.dependencies import get_async_session

        # ctx-less call path: action is being invoked outside an HTTP
        # request and the caller provided neither a session nor a
        # legacy user. Raise so the bug is visible rather than
        # silently returning an empty result.
        raise NotImplementedError(
            f"Action {self.name!r} does not implement run() — the default "
            "forwards to execute() but the router should have dispatched there directly."
        )

    def to_dict(self) -> dict[str, Any]:
        """Wire-format descriptor for the contract.

        ``input_schema`` is serialized as JSON schema (pydantic's
        ``model_json_schema()``) when present, so clients can render
        a form without learning a separate schema dialect.
        """
        out: dict[str, Any] = {"name": self.name, "label": self.label}
        out["confirm"] = bool(self.confirm)
        out["bulk"] = bool(self.bulk)
        if self.input_schema is not None:
            try:
                out["input_schema"] = self.input_schema.model_json_schema()
            except Exception:  # pragma: no cover — pydantic schema failure
                out["input_schema"] = None
        else:
            out["input_schema"] = None
        return out


class BulkDeleteAction(AdminAction):
    name = "delete"
    label = "Delete selected"
    confirm = True

    async def execute(
        self,
        records: list[Any],
        session: AsyncSession,
        user: Any,
    ) -> dict[str, Any]:
        for record in records:
            await session.delete(record)
        await session.flush()
        return {
            "summary": f"Deleted {len(records)} record(s)",
            "affected": len(records),
        }


def uses_typed_run(action: AdminAction) -> bool:
    """Whether the action's subclass overrides :meth:`run`.

    Used by the action router to pick the dispatch style. We compare
    the unbound function on the class — if a subclass replaced it,
    the identity differs from :class:`AdminAction.run`.
    """
    return type(action).run is not AdminAction.run


__all__ = [
    "AdminAction",
    "BulkDeleteAction",
    "uses_typed_run",
]
