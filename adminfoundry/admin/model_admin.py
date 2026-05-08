# Global protected fields — NEVER exposed regardless of per-admin config
GLOBALLY_PROTECTED: frozenset[str] = frozenset({
    "hashed_password",
    "password",
    "pin_hash",
    "shared_secret",
    "tenant_salt",
    "setup_code",
    "qr_bootstrap_token",
})

# Fields auto-managed by the DB — excluded from create schemas
AUTO_FIELDS: frozenset[str] = frozenset({"id", "created_at", "updated_at"})


class ModelAdmin:
    """
    Base class for admin configuration of a SQLAlchemy model.

    Usage::

        class UserAdmin(ModelAdmin):
            model = User
            label = "User"
            list_display = ["email", "full_name", "is_active"]
            readonly_fields = ["id", "created_at", "updated_at"]
    """

    model: type
    list_display: list[str] = []
    search_fields: list[str] = []
    filter_fields: list[str] = []
    # Range filters: ?field__gte=X&field__lte=Y
    range_filter_fields: list[str] = []
    # Enum/multi-value filters: ?field__in=a,b,c
    enum_filter_fields: list[str] = []
    ordering: list[str] = []
    # readonly_fields: excluded from create/update schemas; mutation → 422
    readonly_fields: list[str] = []
    # protected_fields: per-admin extra fields to hide (adds to GLOBALLY_PROTECTED)
    protected_fields: list[str] = []
    # tenant_scoped: True → filter by tenant_id when MULTI_TENANT=True
    tenant_scoped: bool = False

    # --- Phase 6: UI contract metadata ---
    # Human-readable labels; defaults derived from model class name if unset
    label: str | None = None
    label_plural: str | None = None
    description: str | None = None
    # Action descriptors for capability and action metadata exposure
    # Each entry: {"name": str, "label": str, "danger": bool, "confirm": bool,
    #              "bulk": bool, "single": bool}
    actions: list[dict] = []
    # lookup_field: field used as display label in relation-selection widgets.
    # Defaults to list_display[0] if unset; falls back to id.
    lookup_field: str | None = None

    # --- Phase 10: fine-grained authorization ---
    # {field_name: {"view_roles": list[str]|None, "edit_roles": list[str]|None}}
    # None = unrestricted; [] = deny non-superadmin; ["role"] = requires that role
    field_policies: dict = {}
    # callable(user) -> SQLAlchemy WHERE clause | None — scopes list queries
    record_filter: object = None
    # callable(user, record) -> bool — per-record check for detail/update/delete
    record_access: object = None
    # {action_name: {"roles": list[str]|None}}
    action_policies: dict = {}
    # True (default) = require_superadmin; False = policy-based access via access_roles
    admin_only: bool = True
    # roles that grant base CRUD access when admin_only=False
    access_roles: list = []

    # Phase 11: action names that should be executed asynchronously (via job system)
    async_actions: list[str] = []

    # Phase 13: require approval workflow before applying changes
    requires_approval: bool = False

    # Inline-editable relationships (SQLAlchemy relationship attr names)
    inline_fields: list[str] = []
    # Fields editable directly in the list view
    list_editable: list[str] = []
    # Where to redirect after a successful create: "list" | "detail"
    create_redirect: str = "list"
    # Per-field URL to fetch choices from (renders a <select> instead of text input)
    field_choices_urls: dict[str, str] = {}
    # True: render permission matrix section in detail/edit view (for role-like models)
    permission_matrix: bool = False

    # Extra virtual fields only present in the create form (not model columns).
    # Dict of {field_name: python_type}, e.g. {"password": str}.
    # Use before_create() to transform them before the object is saved.
    extra_create_fields: dict = {}

    def field_permission(self, user, field_name: str, record) -> "FieldPolicy | None":
        """Override to return a FieldPolicy based on the current record state.

        Return None to fall back to role-based field_policies config.
        Example: make a field read-only once a record is in 'published' state.
        """
        return None

    @classmethod
    def before_create(cls, data: dict) -> dict:
        """Hook called with validated create data before the model instance is built.
        Override to transform fields (e.g. hash a plain-text password).
        Must return the (possibly mutated) data dict."""
        return data

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Ensure class-level list attrs are NOT shared between subclasses
        for attr in (
            "list_display",
            "search_fields",
            "filter_fields",
            "range_filter_fields",
            "enum_filter_fields",
            "ordering",
            "readonly_fields",
            "protected_fields",
            "actions",
            "access_roles",
            "async_actions",
            "inline_fields",
            "list_editable",
        ):
            if attr not in cls.__dict__:
                setattr(cls, attr, [])
        for attr in ("field_policies", "action_policies", "field_choices_urls"):
            if attr not in cls.__dict__:
                setattr(cls, attr, {})

    @property
    def all_protected(self) -> frozenset[str]:
        return GLOBALLY_PROTECTED | frozenset(self.protected_fields)

    @property
    def model_name(self) -> str:
        return self.model.__tablename__

    @property
    def display_label(self) -> str:
        return self.label or self.model.__name__

    @property
    def display_label_plural(self) -> str:
        return self.label_plural or f"{self.display_label}s"
