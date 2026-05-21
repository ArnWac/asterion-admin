"""Admin registration for the multi-tenant issue-tracker demo.

Everything visible in the admin UI is registered here explicitly —
``enable_builtin_admins=False`` in app.py, so nothing is auto-installed
behind the scenes. The same pattern is used in
``examples/basic_single/admin_config.py``.

Registered admins:

* ``ProjectAdmin``, ``TicketAdmin`` — the demo's tenant-scoped models
  (plus the custom ``CloseTicketsAction``).
* ``UserAdmin``, ``TenantAdmin``, ``TenantMembershipAdmin``,
  ``AuditLogAdmin``, ``ImpersonationLogAdmin`` — framework global tables,
  defined in ``global_admins.py`` next to this file.
* ``TenantRoleAdmin``, ``TenantRolePermissionAdmin``,
  ``TenantMembershipRoleAdmin`` — tenant-local RBAC, imported directly
  from ``adminfoundry.builtins.admin``. Their tables live inside each
  tenant schema (provisioned by ``bootstrap_tenant``).
"""

from __future__ import annotations

from adminfoundry import AdminRegistry, ModelAdmin
from adminfoundry.actions import BulkDeleteAction
from adminfoundry.builtins.admin import (
    TenantMembershipRoleAdmin,
    TenantRoleAdmin,
    TenantRolePermissionAdmin,
)
from examples.multi_tenant.global_admins import register_global_admins
from examples.multi_tenant.models import Project, Ticket


class CloseTicketsAction(BulkDeleteAction):
    """Mark every selected ticket as ``closed`` instead of deleting it."""

    name = "close"
    label = "Close selected"

    async def execute(self, records, session, user):
        for ticket in records:
            ticket.status = "closed"
        await session.flush()
        return {
            "summary": f"Closed {len(records)} ticket(s)",
            "affected": len(records),
        }


class ProjectAdmin(ModelAdmin):
    model = Project
    label = "Project"
    label_plural = "Projects"
    description = "Tenant-scoped projects."

    list_display = ["key", "name", "created_at"]
    search_fields = ["key", "name", "description"]
    ordering = ["key"]
    readonly_fields = ["id", "created_at", "updated_at"]
    actions = [BulkDeleteAction()]


class TicketAdmin(ModelAdmin):
    model = Ticket
    label = "Ticket"
    label_plural = "Tickets"
    description = "Tenant-scoped tickets, one row per issue."

    list_display = ["title", "status", "priority", "assignee", "created_at"]
    search_fields = ["title", "description", "assignee"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]
    actions = [BulkDeleteAction(), CloseTicketsAction()]


def register(registry: AdminRegistry) -> None:
    register_global_admins(registry)
    registry.register(TenantRoleAdmin)
    registry.register(TenantRolePermissionAdmin)
    registry.register(TenantMembershipRoleAdmin)
    registry.register(ProjectAdmin)
    registry.register(TicketAdmin)
