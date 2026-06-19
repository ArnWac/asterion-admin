"""Tests for ModelAdmin base class."""

from __future__ import annotations

from asterion.registry import ModelAdmin


class Thing:
    __tablename__ = "things"


class ThingAdmin(ModelAdmin):
    model = Thing
    list_display = ["id", "name"]
    search_fields = ["name"]
    readonly_fields = ["id", "created_at"]


def test_model_name_property():
    admin = ThingAdmin()
    assert admin.model_name == "things"


def test_display_label_defaults_to_class_name():
    admin = ThingAdmin()
    assert admin.display_label == "Thing"


def test_display_label_uses_label_if_set():
    class LabeledAdmin(ModelAdmin):
        model = Thing
        label = "My Things"

    admin = LabeledAdmin()
    assert admin.display_label == "My Things"


def test_all_protected_includes_globally_protected():
    from asterion.registry.admin import GLOBALLY_PROTECTED

    admin = ThingAdmin()
    assert "hashed_password" in admin.all_protected
    assert GLOBALLY_PROTECTED.issubset(admin.all_protected)


def test_list_attrs_not_shared_between_subclasses():
    class A(ModelAdmin):
        model = Thing

    class B(ModelAdmin):
        model = Thing

    A().list_display.append("extra")
    assert "extra" not in B().list_display
