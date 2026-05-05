"""Import/Export extension — admin data movement flows.

Provides: dry-run import validation, confirmed import, permission-scoped export,
          row-level error reporting, protected-field filtering on output.

Enable by adding ImportExportExtension() to CoreAdminConfig.extensions.
Requires JobsExtension to also be registered (provides Job model and schemas).
"""
from adminfoundry.extensions import ExtensionBase


class ImportExportExtension(ExtensionBase):
    name = "import_export"
    version = "0.1.0"
    is_optional = True

    def get_capabilities(self) -> dict:
        return {
            "import_dry_run": True,
            "import_commit": True,
            "export_scoped": True,
            "row_level_errors": True,
        }

    def startup_check(self) -> None:
        try:
            from adminfoundry.extensions.import_export.service import ImportExportService  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                f"ImportExportExtension missing dependency: {exc}"
            ) from exc


__all__ = ["ImportExportExtension"]
