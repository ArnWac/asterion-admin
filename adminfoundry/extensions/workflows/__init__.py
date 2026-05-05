"""Workflows extension — approval and reversible admin changes.

Tier: commercial
Provides: submit-for-review, approve, reject, revert flows with full audit
          linkage; policy-gated approval actions; metadata-driven discovery.

Enable via CoreAdminConfig(enable_workflows=True) or by adding
WorkflowsExtension() to config.extensions.
"""
from adminfoundry.extensions import ExtensionBase


class WorkflowsExtension(ExtensionBase):
    name = "workflows"
    version = "0.1.0"
    tier = "commercial"
    is_optional = True

    def get_routers(self) -> list:
        from adminfoundry.routers.workflow import router
        return [router]

    def get_models(self) -> list:
        from adminfoundry.models.change_request import ChangeRequest
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
            from adminfoundry.routers.workflow import router  # noqa: F401
            from adminfoundry.services.workflow import WorkflowService  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                f"WorkflowsExtension missing dependency: {exc}"
            ) from exc


__all__ = ["WorkflowsExtension"]
