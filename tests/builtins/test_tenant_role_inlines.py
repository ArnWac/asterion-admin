"""Theme C: the builtin ``TenantRoleAdmin`` bundles permission + member
assignment as inlines so one "Edit" on the role writes all three models.

Covers the contract surface (the two inlines are present, point at the
right child tables / fk columns). The transactional write path is the
shared inline plumbing already verified in ``tests/contract`` /
``tests/crud``; here we only assert the builtin wiring."""

from __future__ import annotations

from asterion.builtins.admin import (
    TenantMembershipRoleAdmin,
    TenantRoleAdmin,
)
from asterion.contract.service import build_model_contract


def test_tenant_role_admin_exposes_permission_and_member_inlines():
    contract = build_model_contract(TenantRoleAdmin())
    by_model = {inline.model: inline for inline in contract.inlines}

    assert "tenant_role_permissions" in by_model
    assert "tenant_membership_roles" in by_model

    perms = by_model["tenant_role_permissions"]
    assert perms.fk_name == "role_id"
    assert perms.label == "Permissions"
    assert "permission_key" in perms.fields

    members = by_model["tenant_membership_roles"]
    assert members.fk_name == "role_id"
    assert members.label == "Members"
    assert "membership_id" in members.fields


def test_role_inlines_use_dual_list_transfer_widget():
    """Theme F: both assignment inlines render as a Django-style transfer
    widget over their single assignment column."""
    contract = build_model_contract(TenantRoleAdmin())
    by_model = {inline.model: inline for inline in contract.inlines}

    perms = by_model["tenant_role_permissions"]
    assert perms.widget == "dual_list"
    assert perms.value_field == "permission_key"

    members = by_model["tenant_membership_roles"]
    assert members.widget == "dual_list"
    assert members.value_field == "membership_id"


def test_membership_role_admin_hidden_from_nav():
    """The dedicated table view stays routable (and keeps its email picker)
    but is off the sidebar — member assignment is managed via the role inline."""
    assert TenantMembershipRoleAdmin.show_in_nav is False
