"""Exception hierarchy for storage backends (Roadmap P4).

Backends raise these — callers (FileField adapter, ``/storage`` router)
catch them and translate to HTTP responses. Concrete backends should
NOT leak driver-specific exceptions (``botocore.exceptions.ClientError``,
``OSError``, etc.); they should wrap them in one of these.
"""

from __future__ import annotations


class StorageError(Exception):
    """Root for any storage-backend failure."""


class ObjectNotFound(StorageError):
    """Raised by ``get`` / ``stat`` / ``delete`` when the key is missing.

    ``delete`` returning ``False`` is the idempotent-success path; this
    is reserved for ``get``/``stat`` where the absence is a real error
    the caller wants to translate to 404.
    """


class StorageRejected(StorageError):
    """Backend refused the write (quota, ACL, validation).

    Distinguishable from a transient ``StorageError`` so the upload
    router can return 4xx instead of 5xx.
    """
