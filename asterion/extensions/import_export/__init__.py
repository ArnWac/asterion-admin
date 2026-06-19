"""Import/Export extension — CSV always, XLSX when openpyxl is installed.

Mounts two endpoints per registered ModelAdmin:

* ``GET  /api/v1/admin/{resource}/_export?format={csv|xlsx}``
* ``POST /api/v1/admin/{resource}/_import`` (multipart upload)

Export reuses the existing list permission (``admin.<resource>.list``),
search, and ordering machinery, and serializes whatever ``list_display``
declares (falling back to every visible field). Import reuses
``admin.<resource>.create`` and runs every row through the same
``clean_write_payload`` validator the regular create endpoint uses, so
hidden / read-only / unknown columns are rejected the same way.

XLSX format requires ``openpyxl`` — install with
``pip install asterion-admin[xlsx]``. Without it, ``format=xlsx`` returns
501 with a helpful message; CSV stays available.

Opt in by passing the extension instance to ``create_admin``::

    from asterion import create_admin
    from asterion.extensions.import_export import ImportExportExtension

    app = create_admin(
        config=config,
        register=register_my_admins,
        extensions=[ImportExportExtension()],
    )

Not in MVP:

* Asynchronous / pre-signed exports. Synchronous streaming is fine for
  the row counts an admin UI realistically deals with; the cap is
  :data:`MAX_EXPORT_ROWS`.
* Import dry-run mode. The import is row-by-row best-effort and returns
  a per-row error report; users can preview by uploading a copy first.
* Formats other than CSV/XLSX (JSON, JSONL, Parquet). Add when asked.
"""

from __future__ import annotations

from fastapi import FastAPI

from asterion.extensions.base import AdminExtension
from asterion.extensions.context import ExtensionContext
from asterion.extensions.import_export.router import (
    EXPORT_AUDIT_ACTION,
    IMPORT_AUDIT_ACTION,
    MAX_EXPORT_ROWS,
    MAX_IMPORT_ROWS,
    SUPPORTED_EXPORT_FORMATS,
    available_formats,
)
from asterion.extensions.import_export.router import (
    router as _import_export_router,
)


class ImportExportExtension(AdminExtension):
    """CSV (always) + XLSX (with openpyxl) export and import endpoints."""

    name = "import_export"

    def register_contract_contributions(self, registry) -> None:
        """Advertise import/export availability under the ``import_export``
        contract namespace.

        Without this, the built-in UI can only learn the endpoints exist
        by clicking a button and getting a 404. Publishing the capability
        (and the formats actually usable in this install) lets the UI gate
        the Import / Export controls on real availability.
        """
        formats = list(available_formats())
        registry.add(
            "import_export",
            {
                "export_formats": formats,
                "import_formats": formats,
                "max_export_rows": MAX_EXPORT_ROWS,
                "max_import_rows": MAX_IMPORT_ROWS,
            },
        )

    def register_routes(self, app: FastAPI, ctx: ExtensionContext) -> None:
        app.include_router(
            _import_export_router,
            prefix=ctx.config.admin_api_prefix,
            tags=["admin-import-export"],
        )


__all__ = [
    "EXPORT_AUDIT_ACTION",
    "IMPORT_AUDIT_ACTION",
    "MAX_EXPORT_ROWS",
    "MAX_IMPORT_ROWS",
    "SUPPORTED_EXPORT_FORMATS",
    "ImportExportExtension",
    "available_formats",
]
