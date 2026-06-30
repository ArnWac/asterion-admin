"""Contract building service: introspects ModelAdmin and SQLAlchemy models.

A3 routed column-type detection through
:mod:`asterion.fields` — the inline ``_field_type`` switch is gone.
A4 promotes the most-used adapter hints (``widget``, ``required``,
``help_text``) into first-class :class:`FieldMeta` fields and adds a
per-request :class:`CapabilitiesMeta` block driven by the caller's
permission set. A5 adds a :class:`RelationMeta` list, introspected from
``mapper.relationships``, so the UI can render lookup widgets and inline
hints without making a separate metadata round-trip.

Contract version stays at ``"2"`` for the A5 additions because the
new ``relations`` field has a safe default (``[]``); clients that ignore
unknown fields are unaffected.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Mapper

from asterion.fields import FieldRegistry, build_default_registry
from asterion.registry.admin import AUTO_FIELDS, ModelAdmin

if TYPE_CHECKING:
    from asterion.registry import AdminRegistry

CONTRACT_VERSION = "2"

CRUD_ACTIONS: tuple[str, ...] = ("list", "read", "create", "update", "delete")

#: Fixed badge-style vocabulary for ``ModelAdmin.list_badges`` (Roadmap
#: 5.5). Bounded so the built-in UI ships a known set of CSS classes;
#: styles outside this set are dropped by the contract builder.
ALLOWED_BADGE_STYLES: frozenset[str] = frozenset(
    {"neutral", "success", "warning", "danger", "info"}
)

#: Wire-format values for :attr:`ModelContractMeta.scope`.
ModelScope = Literal["tenant", "global"]


def resolve_model_scope(model_admin: ModelAdmin) -> ModelScope:
    """Whether the admin's model lives in a tenant schema or the public schema.

    Derived from the SQLAlchemy declarative base: subclasses of
    :class:`~asterion.models.base.TenantBase` (i.e. ``TenantModel``) are
    tenant-scoped — their tables only exist inside a tenant schema;
    everything else (``GlobalModel`` / ``GlobalBase``, or any other base) is
    treated as global/public. Drives context-aware sidebar filtering in
    multi-tenant mode: tenant-scoped resources are only reachable while a
    tenant is active, public resources only outside one. ``"global"`` is the
    safe default — a model that isn't tenant-scoped stays visible in the
    public (superadmin) view rather than disappearing.
    """
    from asterion.models.base import TenantBase

    return "tenant" if issubclass(model_admin.model, TenantBase) else "global"


def resolve_date_hierarchy(model_admin: ModelAdmin) -> str | None:
    """Validate ``ModelAdmin.date_hierarchy`` for the contract (Roadmap 5.5).

    Returns the field name only when it's a real ``Date``/``DateTime``
    column on the model; otherwise ``None`` so a typo or non-date column
    degrades to "no drill-down" rather than shipping an unusable hint.
    """
    from sqlalchemy import Date, DateTime

    field = getattr(model_admin, "date_hierarchy", None)
    if not field:
        return None
    try:
        mapper: Mapper[Any] = sa_inspect(model_admin.model)
        column = mapper.columns[field]
    except KeyError:
        return None
    return field if isinstance(column.type, (Date, DateTime)) else None


def build_list_editable(model_admin: ModelAdmin, field_metas: list[FieldMeta]) -> list[str]:
    """Resolve ``ModelAdmin.list_editable`` for the wire (Roadmap 5.5).

    Keeps only names that are in ``list_display`` AND correspond to a
    writable field in the contract — primary keys, read-only, hidden,
    calculated, and protected columns (which never reach ``field_metas``)
    are dropped so the UI never offers an inline editor the update
    endpoint would reject. Declaration order is preserved.
    """
    declared = list(getattr(model_admin, "list_editable", []) or [])
    if not declared:
        return []
    list_display = set(model_admin.list_display)
    by_name = {f.name: f for f in field_metas}
    out: list[str] = []
    for name in declared:
        meta = by_name.get(name)
        if meta is None or name not in list_display:
            continue
        if meta.primary_key or meta.read_only or meta.calculated or meta.hidden:
            continue
        out.append(name)
    return out


def build_list_badges(model_admin: ModelAdmin) -> dict[str, dict[str, str]]:
    """Normalize ``ModelAdmin.list_badges`` for the wire (Roadmap 5.5).

    Stringifies every value key (so ints / bools / enums match the
    rendered cell text) and drops any mapping whose style isn't in
    :data:`ALLOWED_BADGE_STYLES`. Columns that end up empty are omitted.
    """
    raw = getattr(model_admin, "list_badges", {}) or {}
    out: dict[str, dict[str, str]] = {}
    for column, value_map in raw.items():
        if not isinstance(value_map, dict):
            continue
        normalized = {
            str(value): style
            for value, style in value_map.items()
            if isinstance(style, str) and style in ALLOWED_BADGE_STYLES
        }
        if normalized:
            out[column] = normalized
    return out


class FieldMeta(BaseModel):
    name: str
    type: str
    primary_key: bool = False
    read_only: bool = False
    hidden: bool = False
    nullable: bool = False
    calculated: bool = False
    #: UI widget hint promoted from the field adapter's metadata.
    #: ``None`` means "renderer picks a sensible default for ``type``".
    widget: str | None = None
    #: True when the create path requires this field. False for nullable
    #: columns, columns with a default / server_default, primary keys
    #: (server-generated), read-only fields, calculated fields. Update
    #: schemas treat every field as optional regardless.
    required: bool = False
    #: Human-readable help text. Sourced from SQLAlchemy ``Column.doc``
    #: when available; ``None`` otherwise.
    help_text: str | None = None
    #: Placeholder text for the form input when empty (Roadmap 5.4).
    #: Sourced from ``ModelAdmin.placeholders``; ``None`` means the
    #: renderer shows no placeholder.
    placeholder: str | None = None
    #: Conditional-visibility rule (Roadmap 5.4). ``None`` means the
    #: field is always shown. When present it is a normalized dict of the
    #: shape ``{"field": "<other>", "equals": v}`` or
    #: ``{"field": "<other>", "in": [...]}``; the UI shows this field only
    #: while the referenced field's value satisfies the rule, and omits it
    #: from the submitted payload otherwise.
    condition: dict[str, Any] | None = None
    #: Adapter-supplied or admin-supplied validation hints (e.g.
    #: ``{"min_length": 1, "max_length": 200}``). Empty when the adapter
    #: did not contribute any.
    validation: dict[str, Any] = {}
    #: Remaining adapter hints that did not get promoted to first-class
    #: fields (``choices``, ``foreign_key``, future widget extras).
    #: Pre-A4 clients can still read everything through this dict.
    metadata: dict[str, Any] = {}
    #: Per-caller field permission decided by the admin's
    #: :class:`~asterion.admin.policy.AdminPolicy.field_permission`.
    #: ``"write"`` when no policy is attached (legacy / quickstart
    #: default — the contract behaves as it always has). Clients can
    #: branch on this to disable inputs for ``"read"`` fields and hide
    #: them entirely for ``"hidden"``. Roadmap 2.4.
    field_permission: str = "write"
    #: Dependent-choice rule (Roadmap 5.4). ``None`` when the field's
    #: choices are static. When present:
    #: ``{"field": "<controlling>", "options": {value: [allowed, …]}}`` —
    #: the UI narrows this field's ``<select>`` to the options matching the
    #: controlling field's current value.
    dependency: dict[str, Any] | None = None


class AdminActionMeta(BaseModel):
    name: str
    label: str
    #: UI hint that the action should prompt before firing. Pure metadata —
    #: the framework does not enforce it; the list view renders a
    #: ``confirm()`` for bulk actions / a "are you sure" affordance for rows.
    confirm: bool = False
    #: ``True`` for bulk-style actions (operate on a multi-row selection via
    #: the bulk dropdown), ``False`` for single-row actions the list view
    #: renders as a per-row icon button. Defaults ``True`` to match the
    #: historical bulk-only behaviour.
    bulk: bool = True
    #: Optional glyph name for the per-row icon button (``None`` → the UI
    #: picks a generic action icon). See :attr:`AdminAction.icon`.
    icon: str | None = None
    #: JSON schema for the action's typed input (``AdminAction.input_schema``
    #: rendered via ``model_json_schema()``), or ``None`` when the action
    #: takes no extra input. The UI renders a form dialog from this before
    #: dispatching the action.
    input_schema: dict[str, Any] | None = None


RelationKind = Literal["belongs_to", "has_many", "many_to_many"]


class RelationMeta(BaseModel):
    """One SQLAlchemy ``relationship()`` mapping rendered for the UI.

    * ``name`` — the attribute on the source model (e.g. ``"tenant"``,
      ``"posts"``).
    * ``kind`` — ``belongs_to`` (MANYTOONE), ``has_many`` (ONETOMANY),
      or ``many_to_many`` (MANYTOMANY).
    * ``target`` — the target model's ``__tablename__``. Always present,
      even when the target is not registered as a ModelAdmin.
    * ``target_registered`` — True when the target table is in the
      :class:`~asterion.registry.AdminRegistry`. UIs can use this to
      decide whether to render a lookup link.
    * ``local_columns`` — column names on the SOURCE side that
      participate in the join (typically the FK columns for
      ``belongs_to`` / the PK columns for ``has_many``).
    * ``remote_columns`` — column names on the TARGET side (mirror of
      ``local_columns``).
    * ``secondary`` — assoc table name for ``many_to_many``; ``None``
      for plain joins.
    """

    name: str
    kind: RelationKind
    target: str
    target_registered: bool = False
    local_columns: list[str] = []
    remote_columns: list[str] = []
    secondary: str | None = None


class InlineMeta(BaseModel):
    """Wire-format for one :class:`~asterion.admin.inline.InlineAdmin`.

    Mirrors what the UI needs to render the inline section:

    * ``model`` — child table name (matches ``ModelContractMeta.resource``
      for the child admin if the child is registered).
    * ``fk_name`` — column on the child that points at the parent;
      None when the inline did not declare one (the framework will
      try to infer it in C2).
    * ``label`` — section header.
    * ``fields`` — ordered list of column names to render.
    * ``readonly_fields`` — subset of ``fields`` that are non-editable.
    * ``ordering`` — sort order for existing rows.
    * ``extra``, ``max_num`` — UI capacity hints.
    * ``can_delete`` — whether the row delete control is shown.
    * ``widget`` — ``"dual_list"`` renders a transfer widget over
      ``value_field`` instead of the add-row table (Theme F); ``None``
      keeps the table.
    * ``value_field`` — the assignment column for ``widget="dual_list"``
      (the value moved between the two lists). ``None`` for table inlines.
    """

    model: str
    fk_name: str | None = None
    label: str
    fields: list[str] = []
    readonly_fields: list[str] = []
    ordering: list[str] = []
    extra: int = 0
    max_num: int | None = None
    can_delete: bool = True
    widget: str | None = None
    value_field: str | None = None


class FieldsetMeta(BaseModel):
    """Wire-format for one :class:`~asterion.admin.fieldset.Fieldset`.

    ``fields`` is the post-filter list — fields that didn't exist on
    the model, or that were filtered by ``protected_fields``, are
    dropped during build so the client can trust every name to be
    renderable.
    """

    label: str
    fields: list[str] = []
    collapsed: bool = False
    description: str | None = None


class FilterMeta(BaseModel):
    """Wire-format for one filterable column.

    The contract surfaces the field name and the underlying column
    type so the client can render a sensible widget (text input for
    strings, dropdown for booleans, date picker for datetimes).
    """

    name: str
    type: str
    label: str | None = None


class CapabilitiesMeta(BaseModel):
    """What the current caller is allowed to do with this resource.

    Computed from the caller's permission set when one is supplied to
    :func:`build_model_contract`. With no permission set (e.g.
    schema-only contract dumps, tests), all capabilities default to
    ``True`` so the UI does not silently disable buttons because of a
    missing context.
    """

    create: bool = True
    update: bool = True
    delete: bool = True
    bulk_actions: list[str] = []


class ModelContractMeta(BaseModel):
    contract_version: str
    resource: str
    label: str
    label_plural: str
    description: str | None = None
    #: Schema scope of the backing model (Phase A): ``"tenant"`` for models
    #: that live inside a tenant schema (``TenantModel``), ``"global"`` for
    #: public-schema models. The full-contract endpoint filters the sidebar by
    #: this in multi-tenant mode so a resource only appears where it's actually
    #: reachable. Defaults to ``"global"`` so pre-Phase-A clients (and
    #: single-tenant apps that ignore the field) behave exactly as before.
    scope: ModelScope = "global"
    #: Whether the resource appears in the sidebar nav. ``False`` hides it
    #: while keeping it routable (e.g. a table managed via a dedicated UI).
    show_in_nav: bool = True
    #: "Exactly one row per tenant" resource (e.g. an organization profile /
    #: settings page). When ``True`` the UI renders a settings page and the nav
    #: entry jumps straight into the single row's detail instead of a one-row
    #: list. Pairs with ``capabilities`` (create blocked once a row exists,
    #: delete always blocked). Defaults ``False`` so existing clients are
    #: unaffected. See :attr:`asterion.registry.ModelAdmin.singleton`.
    singleton: bool = False
    fields: list[FieldMeta]
    crud_actions: list[str]
    admin_actions: list[AdminActionMeta]
    capabilities: CapabilitiesMeta
    relations: list[RelationMeta] = []
    fieldsets: list[FieldsetMeta] = []
    #: How the UI should lay out ``fieldsets`` (Roadmap 5.4): ``"sections"``
    #: (collapsible blocks) or ``"tabs"`` (a tab bar). ``"sections"`` is the
    #: safe default for clients that don't special-case tabs.
    form_layout: str = "sections"
    inlines: list[InlineMeta] = []
    filters: list[FilterMeta] = []
    #: List-view badge styling (Roadmap 5.5): ``{column: {value: style}}``
    #: where ``style`` is one of :data:`ALLOWED_BADGE_STYLES`. Values are
    #: stringified so the UI can match them against rendered cell text.
    list_badges: dict[str, dict[str, str]] = {}
    #: Date drill-down column (Roadmap 5.5). The name of a Date/DateTime
    #: column the list view filters by year/month/day, or ``None`` when
    #: unset or the named column is missing / not a date type.
    date_hierarchy: str | None = None
    #: Columns the UI may edit inline in the list (Roadmap 5.5). A subset
    #: of ``list_display`` containing only writable fields; saved through
    #: the normal per-row update endpoint.
    list_editable: list[str] = []
    list_display: list[str]
    search_fields: list[str]
    ordering: list[str]


# ---------------------------------------------------------------------------
# Per-field helpers
# ---------------------------------------------------------------------------


def _column_is_required(col, *, readonly_set: set[str]) -> bool:
    """Required = create-path mandatory.

    A column is required iff: not nullable, no Python-side default, no
    server-side default, not a primary key (server-generated), not in
    the admin's readonly_fields, and not in the framework-generated
    AUTO_FIELDS (``id``, ``created_at``, ``updated_at``).
    """
    if col.primary_key:
        return False
    if col.name in readonly_set:
        return False
    if col.name in AUTO_FIELDS:
        return False
    if col.default is not None or col.server_default is not None:
        return False
    return not bool(col.nullable)


def _column_help_text(col) -> str | None:
    """Pick up SQLAlchemy ``Column.doc`` as the help text.

    ``Column.doc`` exists for exactly this purpose — it does not affect
    the schema and rides through to the contract for free. Admins that
    want richer help text per field will gain a ``help_texts`` mapping
    on :class:`ModelAdmin` in a later phase.
    """
    doc = getattr(col, "doc", None)
    if doc:
        return str(doc)
    return None


def _split_widget_and_validation(
    metadata: dict[str, Any],
) -> tuple[str | None, dict[str, Any], dict[str, Any]]:
    """Separate the adapter metadata into promoted top-level fields and
    the leftover ``metadata`` dict.

    ``widget`` and any ``validation``-shaped keys (``min_length``,
    ``max_length``, ``minimum``, ``maximum``, ``pattern``) get promoted.
    Everything else stays in ``metadata`` so adapter authors can ship
    bespoke hints without coordinating with this module.
    """
    promoted_widget = metadata.get("widget")
    validation_keys = {"min_length", "max_length", "minimum", "maximum", "pattern"}
    validation = {k: v for k, v in metadata.items() if k in validation_keys}
    leftover = {k: v for k, v in metadata.items() if k != "widget" and k not in validation_keys}
    return promoted_widget, validation, leftover


def _field_meta_from_adapter(
    col,
    *,
    registry: FieldRegistry,
    readonly_set: set[str],
    field_permission: str = "write",
    placeholders: dict[str, str] | None = None,
) -> FieldMeta:
    """Run a column through the registry and turn the resulting
    :class:`FieldContract` into the wire-format :class:`FieldMeta`.

    The registry's ``read_only`` flag only reflects column-level
    constraints (primary keys). Admin-level overrides via
    ``ModelAdmin.readonly_fields`` are layered on top here, because
    only the caller knows about the admin.

    ``field_permission`` is the per-caller decision from
    :meth:`AdminPolicy.field_permission` (Roadmap 2.4). Defaults to
    ``"write"`` for the legacy / no-policy path.
    """
    placeholders = placeholders or {}
    adapter = registry.find_adapter(col)
    if adapter is None:
        # build_default_registry includes StringAdapter as the universal
        # fallback, so this branch is only reachable if a caller passes
        # an empty registry. Fall through to a minimal entry rather than
        # raising — keeps the contract endpoint resilient against a
        # misconfigured registry.
        return FieldMeta(
            name=col.name,
            type="string",
            primary_key=bool(col.primary_key),
            read_only=bool(col.primary_key)
            or col.name in readonly_set
            or field_permission == "read",
            hidden=False,
            nullable=bool(col.nullable),
            calculated=False,
            widget=None,
            required=_column_is_required(col, readonly_set=readonly_set),
            help_text=_column_help_text(col),
            placeholder=placeholders.get(col.name),
            validation={},
            metadata={},
            field_permission=field_permission,
        )

    contract = adapter.build_contract(col)
    widget, validation, leftover_metadata = _split_widget_and_validation(dict(contract.metadata))

    return FieldMeta(
        name=contract.name,
        type=contract.type,
        primary_key=contract.primary_key,
        read_only=contract.read_only or contract.name in readonly_set or field_permission == "read",
        hidden=contract.hidden,
        nullable=contract.nullable,
        calculated=contract.calculated,
        widget=widget,
        required=_column_is_required(col, readonly_set=readonly_set),
        help_text=_column_help_text(col),
        placeholder=placeholders.get(contract.name),
        validation=validation,
        metadata=leftover_metadata,
        field_permission=field_permission,
    )


def _normalize_condition(raw: Any, valid_names: set[str]) -> dict[str, Any] | None:
    """Validate a single conditional-visibility rule (Roadmap 5.4).

    Returns the normalized ``{"field", "equals"|"in"}`` dict, or ``None``
    when the rule is malformed or references a field that isn't in
    ``valid_names`` (so a typo degrades to "always visible" rather than
    shipping a dangling rule the UI can't evaluate).
    """
    if not isinstance(raw, dict):
        return None
    ref = raw.get("field")
    if not isinstance(ref, str) or ref not in valid_names:
        return None
    has_equals = "equals" in raw
    has_in = "in" in raw
    if has_equals == has_in:  # need exactly one
        return None
    if has_equals:
        return {"field": ref, "equals": raw["equals"]}
    if not isinstance(raw["in"], (list, tuple)):
        return None
    return {"field": ref, "in": list(raw["in"])}


def _normalize_dependency(raw: Any, valid_names: set[str]) -> dict[str, Any] | None:
    """Validate a dependent-choice rule (Roadmap 5.4).

    Shape: ``{"field": <controlling>, "options": {value: [allowed, …]}}``.
    Returns the normalized dict (keys + values stringified) or ``None``
    when malformed or the controlling field doesn't exist, so a typo
    degrades to "static choices" rather than shipping an unusable rule.
    """
    if not isinstance(raw, dict):
        return None
    ref = raw.get("field")
    options = raw.get("options")
    if not isinstance(ref, str) or ref not in valid_names:
        return None
    if not isinstance(options, dict):
        return None
    norm: dict[str, list[str]] = {}
    for key, allowed in options.items():
        if isinstance(allowed, (list, tuple)):
            norm[str(key)] = [str(v) for v in allowed]
    return {"field": ref, "options": norm}


def _column_field_metas(
    model_admin: ModelAdmin,
    *,
    registry: FieldRegistry,
    perms: dict[str, str],
    readonly_set: set[str],
    placeholders: dict[str, str],
) -> list[FieldMeta]:
    """Mapped-column fields, skipping protected and ``"hidden"``-policy columns."""
    mapper: Mapper[Any] = sa_inspect(model_admin.model)
    protected = model_admin.all_protected
    fields: list[FieldMeta] = []
    for col in mapper.columns:
        if col.name in protected:
            continue
        perm = perms.get(col.name, "write")
        if perm == "hidden":
            continue
        fields.append(
            _field_meta_from_adapter(
                col,
                registry=registry,
                readonly_set=readonly_set,
                field_permission=perm,
                placeholders=placeholders,
            )
        )
    return fields


def _calculated_field_metas(
    model_admin: ModelAdmin,
    *,
    perms: dict[str, str],
    placeholders: dict[str, str],
) -> list[FieldMeta]:
    """Calculated (inherently read-only) fields, skipping ``"hidden"`` ones."""
    fields: list[FieldMeta] = []
    for fname in model_admin.calculated_fields:
        perm = perms.get(fname, "write")
        if perm == "hidden":
            continue
        fields.append(
            FieldMeta(
                name=fname,
                type="string",
                primary_key=False,
                read_only=True,
                hidden=False,
                nullable=True,
                calculated=True,
                widget=None,
                required=False,
                help_text=None,
                placeholder=placeholders.get(fname),
                validation={},
                metadata={},
                field_permission="read",  # calculated fields are inherently read-only
            )
        )
    return fields


def _stamp_field_conditions(fields: list[FieldMeta], model_admin: ModelAdmin) -> None:
    """Conditional-visibility rules (Roadmap 5.4). Stamped after the field list
    exists so a rule can only reference a field that's actually in the contract;
    dangling / malformed rules are dropped."""
    conditions = dict(getattr(model_admin, "field_conditions", {}) or {})
    if not conditions:
        return
    valid_names = {f.name for f in fields}
    for f in fields:
        raw = conditions.get(f.name)
        if raw is None:
            continue
        norm = _normalize_condition(raw, valid_names)
        if norm is not None:
            f.condition = norm


def _stamp_widget_overrides(fields: list[FieldMeta], model_admin: ModelAdmin) -> None:
    """Per-field widget override (Roadmap 5.4) — replaces the adapter's widget
    hint when the admin names a real field."""
    widget_overrides = dict(getattr(model_admin, "widgets", {}) or {})
    if not widget_overrides:
        return
    for f in fields:
        override = widget_overrides.get(f.name)
        if isinstance(override, str) and override:
            f.widget = override


def _stamp_field_dependencies(fields: list[FieldMeta], model_admin: ModelAdmin) -> None:
    """Dependent-choice rules (Roadmap 5.4) — stamped after the field list exists
    so the controlling field can be validated against it."""
    dependencies = dict(getattr(model_admin, "field_dependencies", {}) or {})
    if not dependencies:
        return
    valid_names = {f.name for f in fields}
    for f in fields:
        raw = dependencies.get(f.name)
        if raw is None:
            continue
        norm = _normalize_dependency(raw, valid_names)
        if norm is not None:
            f.dependency = norm


def build_field_metadata(
    model_admin: ModelAdmin,
    *,
    registry: FieldRegistry | None = None,
    field_permissions: dict[str, str] | None = None,
) -> list[FieldMeta]:
    """Build the list of :class:`FieldMeta` for one admin.

    ``registry`` defaults to a fresh
    :func:`~asterion.fields.build_default_registry` — sufficient for
    every column type used in core. Routers that have an
    extension-augmented registry on the runtime pass it in to expose
    extension-contributed adapters.

    ``field_permissions`` (Roadmap 2.4) maps column name → ``"read" |
    "write" | "hidden"`` (the string values of
    :class:`asterion.admin.policy.FieldPermission`). Columns mapped
    to ``"hidden"`` are dropped from the output entirely — same shape
    as a protected field. Other fields receive the value in
    :attr:`FieldMeta.field_permission`; ``"read"`` also forces
    ``read_only=True``. ``None`` means "no per-caller policy was run"
    and every field falls through to the default ``"write"``.

    Assembled in two phases: build the column + calculated fields, then stamp the
    post-hoc rules (conditions / widget overrides / dependencies) that may only
    reference fields already present.
    """
    if registry is None:
        registry = build_default_registry()
    perms = field_permissions or {}
    readonly_set = set(model_admin.readonly_fields)
    placeholders = dict(getattr(model_admin, "placeholders", {}) or {})

    fields = _column_field_metas(
        model_admin,
        registry=registry,
        perms=perms,
        readonly_set=readonly_set,
        placeholders=placeholders,
    )
    fields.extend(_calculated_field_metas(model_admin, perms=perms, placeholders=placeholders))

    _stamp_field_conditions(fields, model_admin)
    _stamp_widget_overrides(fields, model_admin)
    _stamp_field_dependencies(fields, model_admin)
    return fields


async def compute_field_permissions(
    model_admin: ModelAdmin,
    ctx: Any | None,
) -> dict[str, str]:
    """Pre-compute the per-field permission map for one admin + caller.

    This is the single place (Roadmap 2.1) that unifies the three
    field-visibility mechanisms into one decision per field:

    1. :func:`asterion.admin.policy.static_field_permission`
       interprets ``protected_fields`` (→ HIDDEN) and
       ``readonly_fields`` / auto columns (→ READ).
    2. when an :class:`AdminPolicy` is attached and a ctx is supplied,
       its async :meth:`field_permission` runs per field.
    3. the two are combined with :meth:`FieldPermission.strictest` — a
       policy can tighten but never loosen the static class.

    Lives outside :func:`build_field_metadata` because the policy hop
    is async and the contract builders are deliberately sync. Routers
    call this first, then hand the resulting string map to
    ``build_model_contract`` / ``build_field_metadata``.

    Always returns a populated map (one entry per column + calculated
    field) so the contract's ``field_permission`` reflects readonly
    columns as ``"read"`` even with no policy attached.
    """
    from asterion.admin.policy import FieldPermission, static_field_permission

    policy = getattr(model_admin, "policy", None)
    run_policy = policy is not None and ctx is not None

    async def _resolve(field_name: str) -> str:
        static = static_field_permission(model_admin, field_name)
        if not run_policy:
            return static.value
        assert policy is not None  # guaranteed by run_policy
        dynamic = await policy.field_permission(field_name, None, ctx)
        if not isinstance(dynamic, FieldPermission):
            dynamic = FieldPermission(str(dynamic))
        return FieldPermission.strictest(static, dynamic).value

    mapper: Mapper[Any] = sa_inspect(model_admin.model)
    out: dict[str, str] = {}
    for col in mapper.columns:
        out[col.name] = await _resolve(col.name)
    for fname in getattr(model_admin, "calculated_fields", {}) or {}:
        out[fname] = await _resolve(fname)
    return out


def _admin_action_meta(action) -> AdminActionMeta:
    name = str(getattr(action, "name", None) or getattr(action, "__name__", "unknown"))
    label = str(getattr(action, "label", None) or name.replace("_", " ").title())
    icon = getattr(action, "icon", None)
    schema_cls = getattr(action, "input_schema", None)
    input_schema: dict[str, Any] | None = None
    if schema_cls is not None:
        try:
            input_schema = schema_cls.model_json_schema()
        except Exception:  # pragma: no cover — defensive against a bad schema
            input_schema = None
    return AdminActionMeta(
        name=name,
        label=label,
        confirm=bool(getattr(action, "confirm", False)),
        bulk=bool(getattr(action, "bulk", True)),
        icon=str(icon) if isinstance(icon, str) and icon else None,
        input_schema=input_schema,
    )


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def _has(permissions: Collection[str] | None, resource: str, action: str) -> bool:
    """Permission check for capability computation.

    Returns ``True`` when ``permissions`` is None (no caller context —
    we cannot narrow). Otherwise consults the wildcard-aware matcher
    from :mod:`asterion.authz.permissions`.
    """
    if permissions is None:
        return True
    from asterion.authz.permissions import has_permission, permission_key

    return has_permission(permissions, permission_key(resource, action))


def _build_capabilities(
    model_admin: ModelAdmin,
    *,
    permissions: Collection[str] | None,
    singleton_full: bool = False,
) -> CapabilitiesMeta:
    resource = model_admin.model_name
    bulk_actions: list[str] = []
    for action in model_admin.actions:
        action_name = getattr(action, "name", None)
        if not action_name:
            continue
        if _has(permissions, resource, action_name):
            bulk_actions.append(action_name)

    # A resource-level read-only policy (e.g. ReadOnlyPolicy on audit /
    # impersonation admins) overrides the permission-key answer: the write
    # gates 403 at the route regardless of the caller's keys, so the contract
    # must report no create/update/delete and the UI hides those controls.
    # ``disable_create`` / ``disable_delete`` (NoCreateDeletePolicy) tighten
    # only their own capability — an editable admin that still hides New/Delete.
    policy = getattr(model_admin, "policy", None)
    read_only = bool(getattr(policy, "read_only", False))
    no_create = read_only or bool(getattr(policy, "disable_create", False))
    no_delete = read_only or bool(getattr(policy, "disable_delete", False))

    # Singleton default (only when no explicit policy owns the decision): delete
    # is always hidden, and create is hidden once the single row exists
    # (``singleton_full`` — resolved by the contract router via a row count). The
    # route enforces the same; this just keeps the UI controls in sync.
    if getattr(model_admin, "singleton", False) and policy is None:
        no_delete = True
        if singleton_full:
            no_create = True

    return CapabilitiesMeta(
        create=(not no_create) and _has(permissions, resource, "create"),
        update=(not read_only) and _has(permissions, resource, "update"),
        delete=(not no_delete) and _has(permissions, resource, "delete"),
        bulk_actions=[] if read_only else bulk_actions,
    )


# ---------------------------------------------------------------------------
# Fieldsets
# ---------------------------------------------------------------------------


def build_fieldset_metadata(model_admin: ModelAdmin) -> list[FieldsetMeta]:
    """Translate ``ModelAdmin.fieldsets`` to wire-format DTOs.

    Per-fieldset filtering rules:

    * Drop fields that aren't real columns on the model and aren't
      declared as ``calculated_fields``. A misconfigured fieldset that
      names a stale field degrades to a partial section rather than
      breaking the contract.
    * Drop fields filtered by ``protected_fields`` / globally protected
      fields. Same rule as ``build_field_metadata`` — the section
      cannot make a hidden field visible by listing it.
    * Preserve declaration order; deduplicate names within a section
      (a duplicate would cause the renderer to mount the same widget
      twice).
    """
    declared = list(getattr(model_admin, "fieldsets", []) or [])
    if not declared:
        return []

    mapper: Mapper[Any] = sa_inspect(model_admin.model)
    column_names = {col.name for col in mapper.columns}
    calculated_names = set(getattr(model_admin, "calculated_fields", {}) or {})
    valid_names = column_names | calculated_names
    protected = model_admin.all_protected

    metas: list[FieldsetMeta] = []
    for fs in declared:
        seen: set[str] = set()
        filtered: list[str] = []
        for name in fs.fields:
            if name in seen:
                continue
            if name not in valid_names:
                continue
            if name in protected:
                continue
            seen.add(name)
            filtered.append(name)
        metas.append(
            FieldsetMeta(
                label=fs.label,
                fields=filtered,
                collapsed=bool(fs.collapsed),
                description=fs.description,
            )
        )
    return metas


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def build_filter_metadata(
    model_admin: ModelAdmin,
    *,
    registry: FieldRegistry | None = None,
) -> list[FilterMeta]:
    """Translate ``ModelAdmin.filter_fields`` to wire-format DTOs.

    Each filterable column's wire-format ``type`` is the same string
    the regular FieldMeta uses (``"string"``, ``"integer"``,
    ``"boolean"``, ...), sourced from the field adapter so the client
    can render the appropriate widget. Unknown columns are silently
    dropped to keep the contract endpoint resilient against admin
    misconfiguration."""
    declared = list(getattr(model_admin, "filter_fields", []) or [])
    if not declared:
        return []

    if registry is None:
        registry = build_default_registry()

    mapper: Mapper[Any] = sa_inspect(model_admin.model)
    by_name = {col.name: col for col in mapper.columns}

    out: list[FilterMeta] = []
    for name in declared:
        col = by_name.get(name)
        if col is None:
            continue
        adapter = registry.find_adapter(col)
        type_str = adapter.build_contract(col).type if adapter is not None else "string"
        out.append(FilterMeta(name=name, type=type_str))
    return out


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------


def _resolve_inline_instance(entry):
    """Accept either an InlineAdmin subclass or an instance.

    Mirrors the AdminRegistry's flexibility for ModelAdmin entries."""
    if isinstance(entry, type):
        return entry()
    return entry


def _inline_default_fields(inline) -> list[str]:
    """Empty ``inline.fields`` means "all writable columns" — same
    default rule as a top-level ModelAdmin. Skip the foreign key
    column itself; it's set implicitly to the parent's id."""
    declared = list(getattr(inline, "fields", []) or [])
    if declared:
        return declared

    mapper = sa_inspect(inline.model)
    fk_name = getattr(inline, "fk_name", None)
    return [col.name for col in mapper.columns if col.name != fk_name]


def build_inline_metadata(model_admin: ModelAdmin) -> list[InlineMeta]:
    """Translate ``ModelAdmin.inlines`` to wire-format DTOs.

    Per-inline normalization rules:

    * Inline can be declared as a class or an instance; both resolve
      to an instance here.
    * Empty ``fields`` list defaults to all columns except the
      ``fk_name`` column itself.
    * Unknown ``readonly_fields`` (not on the child model) are kept
      as-is — the UI may flag them, but we don't validate hard at
      contract build (matches Fieldset behaviour).
    """
    declared = list(getattr(model_admin, "inlines", []) or [])
    if not declared:
        return []

    metas: list[InlineMeta] = []
    for entry in declared:
        inline = _resolve_inline_instance(entry)
        fields = _inline_default_fields(inline)
        widget = getattr(inline, "widget", None)
        # The dual-list widget needs a single assignment column; default to the
        # first declared field when the inline didn't name one explicitly.
        value_field = getattr(inline, "value_field", None)
        if not value_field and widget == "dual_list":
            value_field = fields[0] if fields else None
        metas.append(
            InlineMeta(
                model=inline.model_name,
                fk_name=getattr(inline, "fk_name", None),
                label=inline.display_label,
                fields=fields,
                readonly_fields=list(getattr(inline, "readonly_fields", []) or []),
                ordering=list(getattr(inline, "ordering", []) or []),
                extra=int(getattr(inline, "extra", 0)),
                max_num=getattr(inline, "max_num", None),
                can_delete=bool(getattr(inline, "can_delete", True)),
                widget=widget if isinstance(widget, str) and widget else None,
                value_field=value_field,
            )
        )
    return metas


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


def _relation_kind(rel) -> RelationKind | None:
    """Map a SQLAlchemy relationship direction to our wire-format kind.

    Returns ``None`` for relationship directions we don't render (none
    exist in stock SA, but a custom direction would otherwise crash the
    contract endpoint)."""
    from sqlalchemy.orm.interfaces import MANYTOMANY, MANYTOONE, ONETOMANY

    if rel.direction is MANYTOONE:
        return "belongs_to"
    if rel.direction is ONETOMANY:
        return "has_many"
    if rel.direction is MANYTOMANY:
        return "many_to_many"
    return None


def _secondary_name(rel) -> str | None:
    secondary = rel.secondary
    if secondary is None:
        return None
    name = getattr(secondary, "name", None)
    return name if name is not None else str(secondary)


def build_relation_metadata(
    model_admin: ModelAdmin,
    *,
    admin_registry: AdminRegistry | None = None,
) -> list[RelationMeta]:
    """Introspect the model's relationships and return wire-format DTOs.

    The ``admin_registry`` is consulted only for the
    ``target_registered`` flag; callers without registry access can
    still get the structural relation list, just with
    ``target_registered=False`` everywhere.
    """
    mapper: Mapper[Any] = sa_inspect(model_admin.model)
    registered_tables: set[str] = set()
    if admin_registry is not None:
        registered_tables = set(admin_registry.model_names())

    relations: list[RelationMeta] = []
    for rel in mapper.relationships:
        kind = _relation_kind(rel)
        if kind is None:
            continue
        target_cls = rel.entity.class_
        target_table = getattr(target_cls, "__tablename__", target_cls.__name__)
        local_columns = sorted({c.name for c in rel.local_columns})
        remote_columns = sorted({c.name for c in rel.remote_side})

        relations.append(
            RelationMeta(
                name=rel.key,
                kind=kind,
                target=target_table,
                target_registered=target_table in registered_tables,
                local_columns=local_columns,
                remote_columns=remote_columns,
                secondary=_secondary_name(rel),
            )
        )

    return relations


def build_model_contract(
    model_admin: ModelAdmin,
    *,
    registry: FieldRegistry | None = None,
    permissions: Collection[str] | None = None,
    admin_registry: AdminRegistry | None = None,
    field_permissions: dict[str, str] | None = None,
    singleton_full: bool = False,
) -> ModelContractMeta:
    """Render the contract for one admin.

    ``permissions`` is the caller's permission-key set (typically
    ``ctx.permissions`` from :class:`~asterion.admin.context.AdminContext`).
    When supplied, the ``capabilities`` block reflects what THIS caller
    can do; when ``None``, all capabilities default to True so cache-
    friendly schema-only consumers still get a usable shape.

    ``field_permissions`` (Roadmap 2.4) is a pre-computed map of
    column name → ``"read" | "write" | "hidden"``. Routers compute it
    once via :func:`compute_field_permissions` (async, calls the
    admin's :class:`AdminPolicy`) before invoking this sync builder.
    None means "no per-caller policy ran" — every field falls back to
    the default ``"write"``.

    ``singleton_full`` (only meaningful for a :attr:`ModelAdmin.singleton`
    resource) is True when the single row already exists; the contract router
    resolves it with a row count so ``capabilities.create`` is hidden once the
    settings row has been created.
    """
    field_metas = build_field_metadata(
        model_admin, registry=registry, field_permissions=field_permissions
    )
    return ModelContractMeta(
        contract_version=CONTRACT_VERSION,
        resource=model_admin.model_name,
        label=model_admin.display_label,
        label_plural=model_admin.display_label_plural,
        description=model_admin.description,
        scope=resolve_model_scope(model_admin),
        show_in_nav=bool(getattr(model_admin, "show_in_nav", True)),
        singleton=bool(getattr(model_admin, "singleton", False)),
        fields=field_metas,
        crud_actions=list(CRUD_ACTIONS),
        admin_actions=[_admin_action_meta(a) for a in model_admin.actions],
        capabilities=_build_capabilities(
            model_admin, permissions=permissions, singleton_full=singleton_full
        ),
        relations=build_relation_metadata(model_admin, admin_registry=admin_registry),
        fieldsets=build_fieldset_metadata(model_admin),
        form_layout=(
            "tabs" if getattr(model_admin, "form_layout", "sections") == "tabs" else "sections"
        ),
        inlines=build_inline_metadata(model_admin),
        filters=build_filter_metadata(model_admin, registry=registry),
        list_badges=build_list_badges(model_admin),
        date_hierarchy=resolve_date_hierarchy(model_admin),
        list_editable=build_list_editable(model_admin, field_metas),
        list_display=list(model_admin.list_display),
        search_fields=list(model_admin.search_fields),
        ordering=list(model_admin.ordering),
    )
