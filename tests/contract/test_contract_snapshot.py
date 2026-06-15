"""Contract snapshot (Roadmap stabilization P1).

The contract is the single UI truth. This test freezes the *whole* shape
of ``build_model_contract()`` for a representative admin so that any change
to the wire format is deliberate — the snapshot (and, for breaking shape
changes, ``contract_version``) must be updated on purpose, not by accident.

It also re-asserts the protected-field invariant: a protected column never
appears anywhere in the contract.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase

from adminfoundry.actions import AdminAction
from adminfoundry.admin.fieldset import Fieldset
from adminfoundry.contract.service import CONTRACT_VERSION, build_model_contract
from adminfoundry.registry import ModelAdmin


class _Base(DeclarativeBase):
    pass


class Article(_Base):
    __tablename__ = "snap_articles"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False, doc="Public title.")
    body = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, server_default="draft")
    secret_token = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class _Publish(AdminAction):
    name = "publish"
    label = "Publish"

    async def execute(self, records, session, user):  # pragma: no cover
        return {"summary": "ok", "affected": len(records)}


class ArticleAdmin(ModelAdmin):
    model = Article
    label = "Article"
    label_plural = "Articles"
    list_display = ["id", "title", "status", "created_at"]
    search_fields = ["title"]
    ordering = ["-created_at"]
    readonly_fields = ["status"]
    protected_fields = ["secret_token"]
    actions = [_Publish()]
    fieldsets = [Fieldset("Content", fields=["title", "body"])]
    placeholders = {"title": "e.g. Hello world"}
    list_badges = {"status": {"draft": "neutral", "published": "success"}}
    date_hierarchy = "created_at"
    list_editable = ["title"]
    widgets = {"body": "textarea"}


EXPECTED: dict = {
    "contract_version": "2",
    "resource": "snap_articles",
    "label": "Article",
    "label_plural": "Articles",
    "description": None,
    "fields": [
        {
            "name": "id",
            "type": "integer",
            "primary_key": True,
            "read_only": True,
            "hidden": False,
            "nullable": False,
            "calculated": False,
            "widget": None,
            "required": False,
            "help_text": None,
            "placeholder": None,
            "condition": None,
            "validation": {},
            "metadata": {},
            "field_permission": "write",
            "dependency": None,
        },
        {
            "name": "title",
            "type": "string",
            "primary_key": False,
            "read_only": False,
            "hidden": False,
            "nullable": False,
            "calculated": False,
            "widget": None,
            "required": True,
            "help_text": "Public title.",
            "placeholder": "e.g. Hello world",
            "condition": None,
            "validation": {"max_length": 200},
            "metadata": {},
            "field_permission": "write",
            "dependency": None,
        },
        {
            "name": "body",
            "type": "string",
            "primary_key": False,
            "read_only": False,
            "hidden": False,
            "nullable": False,
            "calculated": False,
            "widget": "textarea",
            "required": True,
            "help_text": None,
            "placeholder": None,
            "condition": None,
            "validation": {},
            "metadata": {},
            "field_permission": "write",
            "dependency": None,
        },
        {
            "name": "status",
            "type": "string",
            "primary_key": False,
            "read_only": True,
            "hidden": False,
            "nullable": False,
            "calculated": False,
            "widget": None,
            "required": False,
            "help_text": None,
            "placeholder": None,
            "condition": None,
            "validation": {"max_length": 20},
            "metadata": {},
            "field_permission": "write",
            "dependency": None,
        },
        {
            "name": "created_at",
            "type": "datetime",
            "primary_key": False,
            "read_only": False,
            "hidden": False,
            "nullable": False,
            "calculated": False,
            "widget": None,
            "required": False,
            "help_text": None,
            "placeholder": None,
            "condition": None,
            "validation": {},
            "metadata": {},
            "field_permission": "write",
            "dependency": None,
        },
    ],
    "crud_actions": ["list", "read", "create", "update", "delete"],
    "admin_actions": [{"name": "publish", "label": "Publish"}],
    "capabilities": {
        "create": True,
        "update": True,
        "delete": True,
        "bulk_actions": ["publish"],
    },
    "relations": [],
    "fieldsets": [
        {
            "label": "Content",
            "fields": ["title", "body"],
            "collapsed": False,
            "description": None,
        }
    ],
    "form_layout": "sections",
    "inlines": [],
    "filters": [],
    "list_badges": {"status": {"draft": "neutral", "published": "success"}},
    "date_hierarchy": "created_at",
    "list_editable": ["title"],
    "list_display": ["id", "title", "status", "created_at"],
    "search_fields": ["title"],
    "ordering": ["-created_at"],
}


def test_contract_version_is_pinned():
    assert CONTRACT_VERSION == "2"


def test_full_contract_snapshot():
    """If this fails, the contract shape changed. Update EXPECTED (and bump
    contract_version for a breaking change) — on purpose."""
    dump = build_model_contract(ArticleAdmin()).model_dump()
    assert dump == EXPECTED


def test_protected_field_absent_from_snapshot():
    dump = build_model_contract(ArticleAdmin()).model_dump()
    names = [f["name"] for f in dump["fields"]]
    assert "secret_token" not in names
    assert "secret_token" not in str(dump)
