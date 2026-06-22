"""The multi_tenant example's sensitive global-log admins are read-only.

``ImpersonationLog`` / ``AuditLog`` are append-only records written by the
framework, never edited through the admin. The example attaches
:class:`~asterion.admin.policy.ReadOnlyPolicy` so create/update/delete return
403 at the CRUD route — guarding against, e.g., a tenant ``owner`` whose
``admin.*`` wildcard would otherwise match ``admin.impersonation_logs.delete``
and let them wipe cross-tenant impersonation records.

This pins the policy's presence: ``readonly_fields`` alone only disables the
detail form, it does NOT block the DELETE endpoint. (The policy's actual
403 behaviour is covered by tests/admin/test_readonly_policy.py.)
"""

from __future__ import annotations

from asterion.admin.policy import ReadOnlyPolicy
from examples.multi_tenant.global_admins import AuditLogAdmin, ImpersonationLogAdmin


def test_impersonation_log_admin_is_read_only():
    assert isinstance(ImpersonationLogAdmin.policy, ReadOnlyPolicy)


def test_audit_log_admin_is_read_only():
    assert isinstance(AuditLogAdmin.policy, ReadOnlyPolicy)
