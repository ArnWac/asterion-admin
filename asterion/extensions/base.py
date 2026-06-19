"""Base class for an asterion extension.

An :class:`AdminExtension` is the supported way to bundle optional
functionality (routes, permissions, contract fragments, navigation,
protected fields, startup/shutdown hooks) into the framework.

The lifecycle, in order, called by ``create_admin()``:

1. ``configure(config)`` — synchronous validation pass. Extensions
   raise here if config is wrong (missing keys, conflicting flags).
2. ``register_permissions(ctx.permissions)``
3. ``register_protected_fields(ctx.protected_fields)``
4. ``register_contract_contributions(ctx.contract)``
5. ``register_admin_pages(ctx.admin_pages)``
6. ``register_navigation(ctx.navigation)``
7. Framework mirrors permission-bearing admin pages into navigation.
8. ``register_routes(app, ctx)`` — only step that gets ``app``.
9. **Framework freezes all registries.**
8. Lifespan starts: ``startup(app)`` called per extension, in
   registration order.
9. Requests served.
10. Lifespan ends: ``shutdown(app)`` called per extension, in REVERSE
    registration order.

Every hook except the ``name`` attribute has a no-op default — most
extensions implement only one or two methods. Subclasses set the
``name`` class attribute, which is the registry key and must be unique
across all extensions configured on a single app.

Example::

    class ImportExportExtension(AdminExtension):
        name = "import_export"

        def register_routes(self, app, ctx):
            app.include_router(my_router, prefix=ctx.config.admin_api_prefix)
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI

from asterion.core.config import CoreAdminConfig
from asterion.extensions.context import ExtensionContext


class AdminExtension:
    """Concrete base class with no-op defaults.

    Subclass and override only the hooks you need. The class doubles as
    the Protocol — duck-typed objects with the same method signatures
    work just as well, but subclassing is the supported path.
    """

    #: Unique name in the extension registry. Subclasses MUST override.
    name: str = ""

    # ---- configuration / validation ----

    def configure(self, config: CoreAdminConfig) -> None:
        """Synchronous validation pass — raise to abort startup."""

    # ---- registry contributions ----

    def register_permissions(self, registry) -> None:
        """Register namespaced permission keys."""

    def register_protected_fields(self, registry) -> None:
        """Register field names that must never appear in API responses,
        contract metadata, or audit/log output."""

    def register_contract_contributions(self, registry) -> None:
        """Add namespaced fragments to the admin contract."""

    def register_navigation(self, registry) -> None:
        """Add permission-gated nav items to the admin UI."""

    def register_admin_pages(self, registry) -> None:
        """Register custom :class:`~asterion.ui.admin_pages.AdminPage`
        entries (Roadmap 5.6) — pluggable pages outside the CRUD schema.

        The framework mirrors every registered page that declares a
        ``permission`` into the navigation registry, so a page usually
        does not need a separate ``register_navigation`` call.
        """

    def register_models(self) -> Iterable[type[Any]]:
        """Declare ORM model classes this extension contributes.

        The framework returns an iterable of model classes (typically
        :class:`asterion.models.base.GlobalBase` subclasses). They
        register themselves with the shared metadata at *class*
        definition time — this hook's job is to:

        1. **Force the import.** The framework calls this during setup,
           so referring to ``MyModel`` here makes Python evaluate the
           model module and attach the ``Table`` to
           ``GlobalBase.metadata``. Without that, tests that call
           ``metadata.create_all`` and migrations that autogenerate
           wouldn't see the table.
        2. **Document ownership.** The framework stores the returned
           classes on the runtime, so tooling can answer "which models
           came from which extension" without grep.

        Default returns an empty tuple. Extensions that ship database
        models override::

            def register_models(self):
                from asterion.extensions.auth_oauth import models
                return (models.ExternalIdentity,)

        Migration generation: the user is responsible for importing
        their configured extensions in ``migrations/shared/env.py`` so
        autogenerate sees the tables. The framework cannot do this
        automatically because the extension list lives on the
        ``create_admin()`` call, not in a global registry.
        """
        return ()

    def register_routes(self, app: FastAPI, ctx: ExtensionContext) -> None:
        """Mount routes / sub-routers on ``app``.

        This is the only hook that receives ``app`` directly. Run-time
        routes (CRUD, contract, actions) are mounted by the framework
        AFTER this hook, so extension routes that use a static path
        segment (``/{resource}/_export``) win over the dynamic CRUD
        ``/{resource}/{id}`` route — matching the previous behaviour.
        """

    # ---- lifespan ----

    async def startup(self, app: FastAPI) -> None:
        """Async setup at application startup (after registries are frozen)."""

    async def shutdown(self, app: FastAPI) -> None:
        """Async teardown at application shutdown.

        Failures are logged but do not propagate — one extension's
        broken shutdown must not block the others from running.
        """

    # ---- bookkeeping ----

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Don't enforce ``name`` here — let ExtensionRegistry.register
        # raise with the better error when someone forgets it. This
        # keeps importing the class cheap.

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"asterion.extensions.{self.name or self.__class__.__name__}")
