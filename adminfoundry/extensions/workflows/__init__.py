"""Workflows extension — approval and reversible admin changes.

Provides: submit-for-review, approve, reject, revert flows with full audit
          linkage; policy-gated approval actions; metadata-driven discovery.

Enable by adding WorkflowsExtension() to CoreAdminConfig.extensions.
"""
from adminfoundry.extensions import ExtensionBase


class WorkflowsExtension(ExtensionBase):
    name = "workflows"
    version = "0.1.0"
    is_optional = True

    def get_routers(self) -> list:
        from adminfoundry.extensions.workflows.router import router
        return [router]

    def get_models(self) -> list:
        from adminfoundry.extensions.workflows.models import ChangeRequest
        return [ChangeRequest]

    def get_capabilities(self) -> dict:
        return {
            "submit_for_review": True,
            "approve_change": True,
            "reject_change": True,
            "revert_change": True,
            "requires_approval_metadata": True,
        }

    def startup_check(self) -> None:
        try:
            from adminfoundry.extensions.workflows.router import router  # noqa: F401
            from adminfoundry.extensions.workflows.service import WorkflowService  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                f"WorkflowsExtension missing dependency: {exc}"
            ) from exc


__all__ = ["WorkflowsExtension"]
