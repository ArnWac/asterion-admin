"""ExtensionContext — the controlled handle extensions receive during setup.

Passed to every ``register_*`` hook on :class:`AdminExtension`. Carries
references to the config and the four extension-side registries
(permissions / contract / navigation / protected fields). Extensions
must not stash this object beyond their hook call — it's intended as a
short-lived setup-time handle, not a long-lived dependency.

Notably ABSENT from this context:

* ``app`` / ``FastAPI`` — only ``register_routes`` gets the app, and as
  a separate argument. Keeping the app out of the generic context
  discourages extensions from monkey-patching middleware or overriding
  core routes from non-route hooks.
* ``AdminRegistry`` — the ModelAdmin registry. Extensions that need to
  read what models are registered should do so via ``register_routes``'s
  ``app.state.adminfoundry.registry`` instead. Most extensions don't
  need this and should not be tempted by easy access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

# TYPE_CHECKING imports keep this module dependency-free at import time.
# The registries themselves import from ``adminfoundry.extensions.errors``,
# and that triggers loading ``adminfoundry.extensions.__init__`` — which
# pulls in ``base.py`` which pulls in THIS module — cycle. Forward
# references + ``from __future__ import annotations`` mean the dataclass
# annotations stay as strings at runtime, so no circular import happens.
if TYPE_CHECKING:
    from adminfoundry.authz.registry import PermissionRegistry
    from adminfoundry.contract.contributions import ContractContributionRegistry
    from adminfoundry.core.config import CoreAdminConfig
    from adminfoundry.security.protected_fields import ProtectedFieldRegistry
    from adminfoundry.ui.admin_pages import AdminPageRegistry
    from adminfoundry.ui.navigation import NavigationRegistry


@dataclass(frozen=True, slots=True)
class ExtensionContext:
    config: CoreAdminConfig
    permissions: PermissionRegistry
    contract: ContractContributionRegistry
    navigation: NavigationRegistry
    protected_fields: ProtectedFieldRegistry
    admin_pages: AdminPageRegistry
    logger: logging.Logger
