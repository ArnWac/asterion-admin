"""Extension lifecycle orchestration.

Two entry points:

* :func:`run_setup_phase` — synchronous. Called from ``create_admin()``
  between user-side ``register()`` and the framework's core route
  installation. Walks every extension through the documented hook
  sequence and freezes the affected registries afterwards.

* :func:`compose_lifespan` — async context manager builder. Wraps any
  user-supplied lifespan with the extension ``startup`` / ``shutdown``
  pair so async resources (DB pools, OAuth JWKS clients, background
  jobs) start when the app boots and tear down when it exits. Failures
  during shutdown are logged but never re-raised — one broken extension
  must not block the others.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from asterion.extensions.context import ExtensionContext
from asterion.extensions.registry import ExtensionRegistry
from asterion.ui.admin_pages import mirror_pages_into_navigation

logger = logging.getLogger(__name__)

#: Type alias for FastAPI's lifespan context manager factory shape.
LifespanFactory = Callable[[FastAPI], "AsyncIterator[None]"]


def run_setup_phase(
    extensions: ExtensionRegistry,
    ctx: ExtensionContext,
    app: FastAPI,
) -> tuple[type, ...]:
    """Walk every extension through the synchronous setup hooks.

    Order matches the AdminExtension docstring:

    1. ``configure``
    2. ``register_permissions``
    3. ``register_protected_fields``
    4. ``register_contract_contributions``
    5. ``register_admin_pages``
    6. ``register_navigation``
    7. mirror permission-bearing admin pages into navigation
    8. ``register_models``       ← side-effect: forces import + collects classes
    9. ``register_routes``       ← only step that gets ``app``

    After this returns, every extension-side registry is frozen. The
    flattened tuple of extension-contributed model classes is returned
    so the caller can stash it on :class:`AdminRuntime`.
    """
    # Step 1: configure — give every extension a chance to reject the
    # config before we mount routes.
    for ext in extensions:
        ext.configure(ctx.config)

    # Steps 2-6: contributions into the extension-side registries.
    for ext in extensions:
        ext.register_permissions(ctx.permissions)
    for ext in extensions:
        ext.register_protected_fields(ctx.protected_fields)
    for ext in extensions:
        ext.register_contract_contributions(ctx.contract)
    for ext in extensions:
        ext.register_admin_pages(ctx.admin_pages)
    for ext in extensions:
        ext.register_navigation(ctx.navigation)

    # Step 7: mirror admin pages into navigation. Must run AFTER both
    # registries are populated and BEFORE either is frozen below, so a
    # page's sidebar entry appears without a separate register_navigation
    # call. See asterion/ui/admin_pages.py.
    mirror_pages_into_navigation(
        ctx.admin_pages,
        ctx.navigation,
        ui_path=ctx.config.admin_ui_path,
    )

    # Step 8: model declarations. Calling the hook forces the import of
    # the extension's model module, which is what actually attaches
    # the ``Table`` objects to the shared metadata. We flatten the
    # results into a single tuple for runtime introspection.
    collected_models: list[type] = []
    for ext in extensions:
        for model in ext.register_models():
            collected_models.append(model)

    # Step 9: routes. ``app`` is intentionally only available here.
    for ext in extensions:
        ext.register_routes(app, ctx)

    # Freeze every registry that took contributions. Subsequent attempts
    # to register (e.g. at request time) raise RegistryFrozenError.
    ctx.permissions.freeze()
    ctx.protected_fields.freeze()
    ctx.contract.freeze()
    ctx.navigation.freeze()
    ctx.admin_pages.freeze()
    extensions.freeze()

    return tuple(collected_models)


def compose_lifespan(
    extensions: ExtensionRegistry,
    user_lifespan: LifespanFactory | None,
) -> LifespanFactory:
    """Build a lifespan that runs extension ``startup`` / ``shutdown``
    around a (possibly absent) user-supplied lifespan."""

    @asynccontextmanager
    async def composed(app: FastAPI) -> AsyncIterator[None]:
        # Startup in registration order. If one fails we still try to
        # shut down whatever DID start, so resources don't leak.
        started: list = []
        try:
            for ext in extensions:
                await ext.startup(app)
                started.append(ext)

            if user_lifespan is not None:
                async with _to_async_cm(user_lifespan, app):
                    yield
            else:
                yield
        finally:
            # Shutdown in reverse order; never raise.
            for ext in reversed(started):
                try:
                    await ext.shutdown(app)
                except Exception:
                    logger.warning(
                        "extension shutdown failed for %s",
                        ext.name,
                        exc_info=True,
                    )

    return composed


def _to_async_cm(factory: LifespanFactory, app: FastAPI):
    """FastAPI lifespan factories are usually already async context
    managers (decorated with @asynccontextmanager); calling them yields
    the manager. Some test code passes a raw async generator function —
    handle both shapes by going through AsyncExitStack only when needed."""
    result = factory(app)
    # If it's already a context manager (has __aenter__), use it directly.
    if hasattr(result, "__aenter__"):
        return result

    # Otherwise wrap an async iterator into a context manager.
    @asynccontextmanager
    async def _wrap():
        # The result is an AsyncIterator; iterate once.
        agen = result
        await agen.__anext__()
        try:
            yield
        finally:
            try:
                await agen.__anext__()
            except StopAsyncIteration:
                pass

    _ = AsyncExitStack()
    return _wrap()
