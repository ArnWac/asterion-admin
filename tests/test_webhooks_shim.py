"""Webhooks live in adminfoundry.extensions.webhooks — not in the core namespace."""
from adminfoundry.extensions import webhooks as ext_webhooks


def test_webhooks_extension_has_register_and_clear():
    assert callable(ext_webhooks.register)
    assert callable(ext_webhooks.clear)


def test_webhooks_clear_resets_targets():
    ext_webhooks.clear()
    assert ext_webhooks._targets == []


def test_webhooks_not_in_core_namespace():
    import adminfoundry
    assert not hasattr(adminfoundry, "webhooks"), (
        "webhooks must not be a top-level adminfoundry attribute — use adminfoundry.extensions.webhooks"
    )
