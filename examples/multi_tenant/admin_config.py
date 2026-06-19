"""Admin registration for the multi-tenant issue-tracker demo.

Like the single-tenant example, the app-specific admins here are
deliberately exhaustive — ``TicketAdmin`` and ``ProjectAdmin`` together
exercise the full ``ModelAdmin`` surface (badges, conditional/dependent
fields, fieldsets/tabs, widgets, inline edit, protected field, an
object-level policy, calculated fields, an inline child).

Registered admins:

* ``ProjectAdmin``, ``TicketAdmin`` — tenant-scoped models (+ the custom
  ``CloseTicketsAction``, + ``TicketInline`` under projects).
* framework global tables — from ``global_admins.py``.
* tenant-local RBAC admins — from ``asterion.builtins.admin``.
"""

from __future__ import annotations

from typing import Any

from asterion import AdminRegistry, ModelAdmin
from asterion.actions import BulkDeleteAction
from asterion.admin.context import AdminContext
from asterion.admin.fieldset import Fieldset
from asterion.admin.inline import InlineAdmin
from asterion.admin.policy import AdminPolicy
from asterion.builtins.admin import (
    TenantMembershipRoleAdmin,
    TenantRoleAdmin,
    TenantRolePermissionAdmin,
)
from examples.multi_tenant.global_admins import register_global_admins
from examples.multi_tenant.models import Project, Ticket, TicketStatus


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


class TicketPolicy(AdminPolicy):
    """Object-level rule: only closed tickets may be deleted."""

    async def can_delete_object(self, obj: Any, ctx: AdminContext) -> bool:
        return getattr(obj, "status", None) in ("closed", TicketStatus.closed)


def _description_words(obj: Ticket) -> int:
    return len((obj.description or "").split())


class TicketInline(InlineAdmin):
    model = Ticket
    fk_name = "project_id"
    fields = ["title", "status", "priority", "assignee"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]
    extra = 1
    can_delete = True


class ProjectAdmin(ModelAdmin):
    model = Project
    label = "Project"
    label_plural = "Projects"
    description = "Tenant-scoped projects, with their tickets edited inline."

    list_display = ["key", "name", "created_at"]
    search_fields = ["key", "name", "description"]
    ordering = ["key"]
    readonly_fields = ["id", "created_at", "updated_at"]
    date_hierarchy = "created_at"

    fieldsets = [
        Fieldset("Project", fields=["name", "key", "description"]),
    ]
    placeholders = {"key": "e.g. WEB", "name": "Project name"}
    widgets = {"description": "textarea"}

    actions = [BulkDeleteAction()]
    inlines = [TicketInline]
    calculated_fields = {"slug": lambda obj: (obj.key or "").lower()}


class TicketAdmin(ModelAdmin):
    model = Ticket
    label = "Ticket"
    label_plural = "Tickets"
    description = "Tenant-scoped tickets — a tour of the full ModelAdmin surface."

    # --- list view ---
    list_display = ["title", "status", "priority", "category", "assignee", "created_at"]
    search_fields = ["title", "description", "assignee"]
    ordering = ["-created_at"]
    filter_fields = ["status", "priority", "category"]
    date_hierarchy = "created_at"
    list_badges = {
        "status": {"open": "info", "in_progress": "warning", "closed": "success"},
        "priority": {"low": "neutral", "normal": "info", "high": "warning", "urgent": "danger"},
    }
    list_editable = ["title", "assignee"]  # inline text edit

    # --- field access ---
    readonly_fields = ["id", "created_at", "updated_at"]
    protected_fields = ["secret_ref"]
    policy = TicketPolicy()

    # --- form layout ---
    form_layout = "tabs"
    fieldsets = [
        Fieldset("Overview", fields=["title", "description", "project_id"]),
        Fieldset("Triage", fields=["status", "priority", "category", "component", "assignee"]),
        Fieldset("Resolution", fields=["resolution", "secret_ref"], collapsed=True),
    ]
    placeholders = {"title": "Short summary", "assignee": "email or name"}
    widgets = {"description": "textarea", "resolution": "textarea"}
    field_conditions = {
        "resolution": {"field": "status", "equals": "closed"},
    }
    field_dependencies = {
        "component": {
            "field": "category",
            "options": {
                "bug": ["api", "ui", "db"],
                "feature": ["integration", "reporting"],
                "chore": ["ci", "docs"],
            },
        },
    }

    # --- behaviour ---
    actions = [BulkDeleteAction(), CloseTicketsAction()]
    calculated_fields = {"description_words": _description_words}


def register(registry: AdminRegistry) -> None:
    register_global_admins(registry)
    registry.register(TenantRoleAdmin)
    registry.register(TenantRolePermissionAdmin)
    registry.register(TenantMembershipRoleAdmin)
    registry.register(ProjectAdmin)
    registry.register(TicketAdmin)
