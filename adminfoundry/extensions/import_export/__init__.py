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
``pip install adminfoundry[xlsx]``. Without it, ``format=xlsx`` returns
501 with a helpful message; CSV stays available.

Opt in by passing :func:`register` to ``create_admin``::

    from adminfoundry.extensions.import_export import register as import_export

    app = create_admin(
        config=config,
        register=register_my_admins,
        extensions=[import_export],
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

from adminfoundry.extensions.import_export.router import (
    EXPORT_AUDIT_ACTION,
    IMPORT_AUDIT_ACTION,
    MAX_EXPORT_ROWS,
    MAX_IMPORT_ROWS,
    SUPPORTED_EXPORT_FORMATS,
    register,
)

__all__ = [
    "EXPORT_AUDIT_ACTION",
    "IMPORT_AUDIT_ACTION",
    "MAX_EXPORT_ROWS",
    "MAX_IMPORT_ROWS",
    "SUPPORTED_EXPORT_FORMATS",
    "register",
]
