"""Jobs extension — asynchronous admin operation support.

Provides: async action execution, job status tracking, retry semantics,
          idempotency key protection, audit linkage.

Enable by adding JobsExtension() to CoreAdminConfig.extensions.
"""
from adminfoundry.extensions import ExtensionBase


class JobsExtension(ExtensionBase):
    name = "jobs"
    version = "0.1.0"
    is_optional = True

    def get_routers(self) -> list:
        from adminfoundry.extensions.jobs.router import router
        return [router]

    def get_models(self) -> list:
        from adminfoundry.extensions.jobs.models import Job
        return [Job]

    def get_capabilities(self) -> dict:
        return {
            "async_actions": True,
            "job_tracking": True,
            "job_retry": True,
            "job_idempotency": True,
        }

    def startup_check(self) -> None:
        try:
            from adminfoundry.extensions.jobs.router import router  # noqa: F401
            from adminfoundry.extensions.jobs.service import JobService  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(f"JobsExtension missing dependency: {exc}") from exc


__all__ = ["JobsExtension"]
