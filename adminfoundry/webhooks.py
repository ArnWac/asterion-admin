"""Backward-compat shim — the implementation lives in adminfoundry.extensions.webhooks."""
from adminfoundry.extensions.webhooks import *  # noqa: F401,F403
from adminfoundry.extensions.webhooks import _targets, _WebhookTarget  # noqa: F401
