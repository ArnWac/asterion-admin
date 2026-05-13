"""Backward-compat: adminfoundry.webhooks shims onto adminfoundry.extensions.webhooks."""
import adminfoundry
from adminfoundry import webhooks
from adminfoundry.extensions import webhooks as ext_webhooks


def test_top_level_webhooks_import():
    assert webhooks.register is ext_webhooks.register
    assert webhooks.clear is ext_webhooks.clear


def test_webhooks_module_shim_same_callables():
    from adminfoundry.webhooks import register, clear
    assert register is ext_webhooks.register
    assert clear is ext_webhooks.clear


def test_webhooks_clear_resets_targets():
    ext_webhooks.clear()
    assert ext_webhooks._targets == []


def test_adminfoundry_namespace_still_exposes_webhooks():
    assert adminfoundry.webhooks is webhooks
