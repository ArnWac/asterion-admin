from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from asterion.security.protected_fields import (
    DEFAULT_PROTECTED_FIELDS,
    get_registry,
)

if TYPE_CHECKING:
    from asterion.admin.context import AdminContext
    from asterion.admin.fieldset import Fieldset
    from asterion.admin.inline import InlineAdmin
    from asterion.admin.policy import AdminPolicy

#: Backward-compatible alias for the default seed. New code should call
#: :func:`asterion.security.protected_fields.get_registry` directly
#: (the registry may have extension-contributed fields beyond this set).
GLOBALLY_PROTECTED: frozenset[str] = DEFAULT_PROTECTED_FIELDS

AUTO_FIELDS: frozenset[str] = frozenset({"id", "created_at", "updated_at"})


class ModelAdmin:
    """MVP admin configuration for a SQLAlchemy model.

    Example::

        class UserAdmin(ModelAdmin):
            model = User
            list_display = ["email", "full_name", "is_active"]
            search_fields = ["email", "full_name"]
            ordering = ["email"]
            readonly_fields = ["id", "created_at", "updated_at"]
    """

    model: type

    label: str | None = None
    label_plural: str | None = None
    description: str | None = None

    list_display: list[str] = []
    search_fields: list[str] = []
    ordering: list[str] = []

    readonly_fields: list[str] = []
    protected_fields: list[str] = []

    actions: list[Any] = []

    #: Columns that may be filtered via query parameters. Each entry
    #: is a column name on the model. The list view accepts
    #: ``?filter_<name>=<value>`` for any column listed here and applies
    #: them as ``column == value`` (with type coercion). Unknown
    #: filters are rejected with 422. See D1 in the roadmap.
    filter_fields: list[str] = []

    #: Optional form-layout grouping. Empty list means "render the
    #: form flat" — see :class:`asterion.admin.fieldset.Fieldset`
    #: for the per-section structure and validation rules.
    fieldsets: list[Fieldset] = []

    #: How the built-in form lays out :attr:`fieldsets` (Roadmap 5.4):
    #: ``"sections"`` (default — collapsible blocks) or ``"tabs"`` (a tab
    #: bar, one tab per fieldset). Ignored when no fieldsets are declared.
    #: Surfaced on ``ModelContractMeta.form_layout``; an unrecognized
    #: value falls back to ``"sections"`` in the contract builder.
    form_layout: str = "sections"

    #: Optional per-field widget override (Roadmap 5.4). Maps a field name
    #: to a built-in widget hint that replaces the adapter-derived one on
    #: ``FieldMeta.widget`` — e.g. ``{"bio": "textarea"}`` to render a
    #: String column as a multi-line box. The built-in UI understands
    #: ``"textarea"`` and ``"select"`` (the latter needs ``choices``);
    #: unknown names pass through for a custom client to interpret.
    widgets: dict[str, str] = {}

    #: Optional per-field placeholder text (Roadmap 5.4). Maps a field
    #: name to the placeholder string the form input should show when
    #: empty. Surfaced on ``FieldMeta.placeholder`` in the contract;
    #: fields without an entry get ``None`` (renderer shows no
    #: placeholder). Unknown field names are ignored by the builder.
    placeholders: dict[str, str] = {}

    #: Columns editable inline in the list view (Roadmap 5.5). Each name
    #: must be in :attr:`list_display` and be a writable field (not a
    #: primary key, read-only, protected, hidden, or calculated column);
    #: the contract builder drops any that don't qualify. The built-in UI
    #: renders these cells as inputs and saves edits via the normal
    #: per-row update endpoint, so all validation / permission / audit
    #: rules apply unchanged.
    list_editable: list[str] = []

    #: Optional list-view date drill-down (Roadmap 5.5). Names a
    #: ``Date``/``DateTime`` column; the list view then offers a
    #: year → month → day filter over it. Surfaced on
    #: ``ModelContractMeta.date_hierarchy`` (dropped if the column is
    #: missing or not a date type); the list endpoint accepts
    #: ``?dh=YYYY[-MM[-DD]]`` to filter to that period.
    date_hierarchy: str | None = None

    #: Optional dependent-field choices (Roadmap 5.4). Maps a dependent
    #: (select) field to a controlling field plus the allowed choice values
    #: per controlling value::
    #:
    #:     field_dependencies = {
    #:         "state": {
    #:             "field": "country",
    #:             "options": {"US": ["CA", "NY"], "DE": ["BY", "BE"]},
    #:         },
    #:     }
    #:
    #: The form narrows the dependent ``<select>`` to the options matching
    #: the controlling field's current value. ``field`` must reference an
    #: existing field; malformed/dangling rules are dropped by the contract
    #: builder. Only meaningful for fields rendered as a select.
    field_dependencies: dict[str, dict] = {}

    #: Optional list-view badge styling (Roadmap 5.5). Maps a column
    #: name to a ``{value: style}`` table; the list view renders matching
    #: cell values as a colored badge instead of plain text::
    #:
    #:     list_badges = {
    #:         "status": {"published": "success", "draft": "neutral",
    #:                    "archived": "danger"},
    #:     }
    #:
    #: ``style`` must be one of the fixed vocabulary
    #: (``neutral``/``success``/``warning``/``danger``/``info``); unknown
    #: styles are dropped by the contract builder. Values are matched by
    #: their stringified form, so ints / bools / enums work too.
    list_badges: dict[str, dict] = {}

    #: Optional conditional-visibility rules (Roadmap 5.4). Maps a
    #: *dependent* field name to a rule that references another field::
    #:
    #:     field_conditions = {
    #:         "vat_id": {"field": "is_business", "equals": True},
    #:         "shipping_note": {"field": "ship_method", "in": ["air", "sea"]},
    #:     }
    #:
    #: The dependent field is shown only while the rule holds; the UI
    #: hides it and drops it from the submitted payload otherwise. A rule
    #: must carry ``field`` plus exactly one of ``equals`` / ``in``, and
    #: ``field`` must reference an existing (visible) field — malformed or
    #: dangling rules are dropped by the contract builder so a typo
    #: degrades to "always visible" rather than a 500. Conditionally
    #: hidden fields should be nullable, since a hidden field submits no
    #: value.
    field_conditions: dict[str, dict] = {}

    #: Optional :class:`~asterion.admin.policy.AdminPolicy` instance
    #: layered on top of the permission-key checks. ``None`` means
    #: "permission keys alone decide" (legacy / quickstart behavior).
    #: Set on subclasses to enforce object-level rules.
    policy: AdminPolicy | None = None

    #: Child models to edit inline with the parent record. Each entry
    #: is an :class:`~asterion.admin.inline.InlineAdmin` subclass
    #: or instance. C1 exposes them through the contract; C2 will add
    #: the transactional parent/child CRUD plumbing.
    inlines: list[type[InlineAdmin] | InlineAdmin] = []

    calculated_fields: dict[str, Callable[[Any], Any]] = {}

    #: Optional column used as this model's human-readable label when it is the
    #: *target* of a foreign-key picker (FK dropdowns). When a form on another
    #: model renders a FK pointing here, the picker fetches ``{id, label}`` pairs
    #: and shows ``label`` instead of the raw id. ``None`` falls back to the
    #: :meth:`label_field` heuristic (first common label column, else a
    #: ``list_display`` column, else the primary key).
    display_field: str | None = None

    #: Columns tried in order by :meth:`label_field` when ``display_field`` is
    #: unset. Deliberately small + conventional; an app names its own column via
    #: ``display_field`` when none of these fit.
    LABEL_FIELD_CANDIDATES: tuple[str, ...] = (
        "name",
        "title",
        "label",
        "display_name",
        "email",
        "slug",
        "username",
        "code",
        "key",
    )

    #: Whether this resource appears in the admin sidebar nav. ``False`` keeps
    #: the resource fully routable (CRUD, contract, permission keys) but hides
    #: it from the sidebar — for tables managed through a dedicated UI rather
    #: than the generic list (e.g. ``TenantRolePermission``, edited via the
    #: per-role permission picker). Surfaced in the contract; the sidebar
    #: filters on it.
    show_in_nav: bool = True

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        for attr in (
            "list_display",
            "search_fields",
            "ordering",
            "readonly_fields",
            "protected_fields",
            "actions",
            "filter_fields",
            "fieldsets",
            "inlines",
            "list_editable",
        ):
            if attr not in cls.__dict__:
                setattr(cls, attr, [])
        if "calculated_fields" not in cls.__dict__:
            cls.calculated_fields = {}
        if "placeholders" not in cls.__dict__:
            cls.placeholders = {}
        if "widgets" not in cls.__dict__:
            cls.widgets = {}
        if "field_conditions" not in cls.__dict__:
            cls.field_conditions = {}
        if "list_badges" not in cls.__dict__:
            cls.list_badges = {}
        if "field_dependencies" not in cls.__dict__:
            cls.field_dependencies = {}

    # ------------------------------------------------------------------
    # List-view reference labels
    # ------------------------------------------------------------------

    async def resolve_list_labels(
        self,
        objs: list[Any],
        *,
        session: Any,
        ctx: AdminContext | None = None,
    ) -> dict[str, dict[str, str]]:
        """Human-readable labels for reference (id) columns in the list view.

        Return ``{column_name: {raw_value_str: label}}``. The list endpoint
        attaches a ``"<column>__label"`` key to each serialized row so the UI
        renders the label instead of the raw id (the raw value stays available
        under the original key).

        Resolve in **batch** — one query per related table for the whole page
        (``WHERE id IN (...)``), never one query per row — so a list view with
        reference columns stays O(1) queries, not O(n). ``objs`` is the page of
        ORM rows; ``session`` is the same request-scoped (tenant-aware) session
        the rows were loaded from, so cross-schema lookups (e.g. a tenant row's
        ``membership_id`` → a public ``users.email``) work through its
        ``search_path``.

        Default: no labels.
        """
        return {}

    # ------------------------------------------------------------------
    # Lifecycle hooks (B1)
    # ------------------------------------------------------------------
    #
    # All hooks are no-op coroutines by default — subclasses override
    # only the ones they need. The CRUD/Action/Import routers will call
    # them in B2; defining them here first lets app code start writing
    # business logic against a stable signature.
    #
    # Source-agnostic contract: hooks fire for every mutation path
    # (CRUD API, admin UI, bulk actions, import, jobs, webhooks) so
    # app-side invariants don't depend on which router triggered the
    # change.

    async def before_validate(
        self,
        data: dict[str, Any],
        ctx: AdminContext,
    ) -> dict[str, Any]:
        """Pre-validation data tweak hook.

        Fires for both create and update paths, before any schema or
        permission checks. Return the (possibly modified) payload —
        the framework feeds the return value into validation.
        """
        return data

    async def validate_create(
        self,
        data: dict[str, Any],
        ctx: AdminContext,
    ) -> None:
        """Raise to reject the create payload. Default: accept anything
        the schema already validated. Custom checks (uniqueness scoped
        to the tenant, cross-field invariants) go here."""
        return None

    async def before_create(
        self,
        data: dict[str, Any],
        ctx: AdminContext,
    ) -> dict[str, Any]:
        """Last chance to mutate the payload before the row is built.

        Use for server-side defaults that can't be expressed as
        ``column.default`` (e.g. picking the current tenant id, hashing
        an incoming password). Return the payload that the framework
        will feed to the model constructor.
        """
        return data

    async def after_create(
        self,
        obj: Any,
        ctx: AdminContext,
    ) -> None:
        """Post-commit hook. The row exists in the DB; ``obj`` is
        attached to the session. Use for side effects: outbound
        webhooks, search-index updates, notifications. B2 will move
        audit-row writes into a framework-supplied default."""
        return None

    async def validate_update(
        self,
        obj: Any,
        data: dict[str, Any],
        ctx: AdminContext,
    ) -> None:
        """Update-only validation. ``obj`` is the persisted row;
        ``data`` is the patch. Raise to reject. Use for transitions
        that depend on the current state (e.g. only published posts
        can be archived)."""
        return None

    async def before_update(
        self,
        obj: Any,
        data: dict[str, Any],
        ctx: AdminContext,
    ) -> dict[str, Any]:
        """Last chance to mutate the patch before it is applied to
        ``obj``."""
        return data

    async def after_update(
        self,
        obj: Any,
        changes: dict[str, Any],
        ctx: AdminContext,
    ) -> None:
        """Post-commit hook for updates. ``changes`` is the diff that
        was applied (keys → new values)."""
        return None

    async def before_delete(
        self,
        obj: Any,
        ctx: AdminContext,
    ) -> None:
        """Pre-delete hook. Raise to refuse deletion (e.g. soft-delete
        only, or "cannot delete with active children")."""
        return None

    async def after_delete(
        self,
        obj: Any,
        ctx: AdminContext,
    ) -> None:
        """Post-delete hook. ``obj`` is the (now-detached) row that
        was just removed; useful for cascade-style cleanup of external
        resources."""
        return None

    @property
    def all_protected(self) -> frozenset[str]:
        """Combined set of protected field names for this admin.

        Reads from the live :class:`ProtectedFieldRegistry` (so any
        extension-contributed fields are included) merged with this
        admin's own ``protected_fields``.
        """
        return get_registry().as_frozenset() | frozenset(self.protected_fields)

    @property
    def label_field(self) -> str:
        """Resolve the column used as this model's human-readable label.

        Priority: explicit :attr:`display_field` (when it's a real column) →
        first of :attr:`LABEL_FIELD_CANDIDATES` present on the model → first
        non-PK name in :attr:`list_display` that is a real column → the primary
        key. Always returns a real column name, so a FK picker can fall back to
        showing the raw id when nothing more readable exists.
        """
        from sqlalchemy import inspect as sa_inspect

        mapper = sa_inspect(self.model)
        column_names = {col.name for col in mapper.columns}
        pk = mapper.primary_key
        pk_name = pk[0].name if len(pk) == 1 else None

        if self.display_field and self.display_field in column_names:
            return self.display_field
        for candidate in self.LABEL_FIELD_CANDIDATES:
            if candidate in column_names:
                return candidate
        for name in self.list_display:
            if name in column_names and name != pk_name:
                return name
        return pk_name or next(iter(column_names))

    @property
    def model_name(self) -> str:
        return self.model.__tablename__

    @property
    def display_label(self) -> str:
        return self.label or self.model.__name__

    @property
    def display_label_plural(self) -> str:
        return self.label_plural or f"{self.display_label}s"
