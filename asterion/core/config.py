# asterion/core/config.py

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields, replace
from typing import Any, Literal, TypeVar, cast, get_args

TenantResolution = Literal["header", "subdomain"]
DateFormat = Literal["locale", "iso", "eu", "us", "custom"]
Environment = Literal["development", "test", "production"]
UserMode = Literal["builtin", "external"]
AuditPIIMode = Literal["redact", "hash", "keep"]

T = TypeVar("T", bound=str)

MIN_PRODUCTION_SECRET_LENGTH = 32


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    normalized = value.strip().lower()

    if normalized in {"1", "true", "yes", "on"}:
        return True

    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"Invalid boolean value for {name}: {value!r}. "
        "Expected one of: true/false, 1/0, yes/no, on/off."
    )


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer value for {name}: {value!r}.") from exc


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid float value for {name}: {value!r}.") from exc


def _env_optional_int(name: str) -> int | None:
    """Parse an optional int env var. Unset (or empty) yields ``None``."""
    value = os.getenv(name)

    if value is None or value.strip() == "":
        return None

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer value for {name}: {value!r}.") from exc


def _env_required(name: str) -> str:
    value = os.getenv(name)

    if value is None or not value.strip():
        raise ValueError(f"Required environment variable is missing: {name}")

    return value.strip()


def _env_optional(name: str, default: str) -> str:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip()


def _env_literal(
    name: str,
    default: T,
    allowed: tuple[T, ...],
) -> T:
    value = cast(T, os.getenv(name, default))

    if value not in allowed:
        raise ValueError(f"Invalid value for {name}: {value!r}. Expected one of {allowed}.")

    return value


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    parts = tuple(p.strip() for p in value.split(",") if p.strip())
    return parts


_VALID_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET")


@dataclass(slots=True, frozen=True)
class CoreAdminConfig:
    database_url: str
    secret_key: str

    app_title: str = "asterion"
    debug: bool = False

    auth_api_prefix: str = "/api/v1/auth"
    admin_api_prefix: str = "/api/v1/admin"
    root_api_prefix: str = "/api/v1/root"
    admin_ui_path: str = "/admin"

    jwt_algorithm: str = "HS256"
    #: Optional JWT ``iss`` / ``aud`` hardening (Review R8). When set, every
    #: token the framework mints carries the claim and every decode validates
    #: it (a token with a missing/wrong ``iss``/``aud`` is rejected). ``None``
    #: (the default) keeps the pre-R8 behaviour — no claim set, none checked —
    #: which is fine for a single-service, single-secret deployment. Set these
    #: once tokens are shared across services/audiences.
    #:
    #: Rotation caveat: changing or **unsetting** these invalidates every
    #: already-issued token (a token still carrying ``aud`` is rejected once
    #: decode no longer expects it, and vice-versa) — i.e. it forces a global
    #: re-login. Roll the value at a low-traffic window, or accept the
    #: short-lived churn (access tokens expire quickly; refresh tokens force a
    #: fresh login).
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    access_token_expire_minutes: int = 60
    #: Refresh-token lifetime (Roadmap 3.1). Default 7 days. The
    #: refresh token is long-lived and exchanged at ``/auth/refresh``
    #: for a fresh access+refresh pair (rotation); the old refresh
    #: token is revoked on each exchange.
    refresh_token_expire_minutes: int = 60 * 24 * 7
    #: Password-reset token lifetime (Roadmap 3.3). Default 30 minutes
    #: — short because the link grants account access.
    password_reset_token_expire_minutes: int = 30
    #: Password-reset request throttle (per email, per window). The reset
    #: endpoint always returns 202 (anti-enumeration); this caps how many reset
    #: emails / tokens a single address can trigger before further requests are
    #: rejected with 429 — blunting email-bombing and token-generation abuse.
    #: Every request is counted (existent account or not), so the throttle never
    #: reveals whether an account exists. It is a **separate** counter from the
    #: login limiter.
    password_reset_rate_limit_max: int = 5
    password_reset_rate_limit_window_seconds: int = 15 * 60
    #: Member-invite token lifetime. Default 7 days — an invited user
    #: needs longer than a reset to notice the email and set a password.
    #: Shares the single-use ``password_reset_tokens`` machinery; the
    #: invitee completes onboarding at ``/auth/password-reset/confirm``,
    #: which also activates the account.
    invite_token_expire_minutes: int = 60 * 24 * 7
    password_min_length: int = 8
    #: Check new passwords against the Have I Been Pwned breach corpus (G21,
    #: NIST 800-63B). **Off by default** — it makes an external network call (via
    #: k-anonymity: only a 5-char SHA-1 prefix leaves the process, never the
    #: password). Fails open: if HIBP is unreachable the check is skipped, so an
    #: outage can't block password resets. Needs httpx installed.
    password_hibp_check: bool = False
    #: Timeout (seconds) for the HIBP breach lookup when ``password_hibp_check``.
    password_hibp_timeout_seconds: float = 3.0

    #: Filesystem root for :class:`LocalFileStorage` (Roadmap P4).
    #: When set, ``create_admin`` auto-wires a ``LocalFileStorage`` at
    #: this path as ``runtime.storage`` so :class:`FileField` works
    #: out-of-the-box. When ``None``, the app either passes an explicit
    #: ``storage=`` to ``create_admin`` or doesn't use file fields at
    #: all — accessing ``runtime.storage`` then raises a clear error.
    storage_root: str | None = None
    #: Maximum upload size in bytes accepted by the ``/storage/upload``
    #: route (Roadmap P4). Default 25 MiB — covers typical admin
    #: documents/images without letting a malicious client OOM the
    #: server. Per-FileField caps can tighten this further.
    storage_max_upload_bytes: int = 25 * 1024 * 1024

    enable_builtin_ui: bool = True
    enable_builtin_admins: bool = True
    enable_multi_tenant: bool = True

    #: Enable the superadmin impersonation feature: the ``POST {root}/impersonate``
    #: route and the admin-UI "Impersonate" button on a user's detail page.
    #: Impersonation is always superadmin-only (the route rejects impersonation
    #: tokens) and writes an ``ImpersonationLog`` + audit entry. Defaults to True,
    #: preserving the pre-0.1.12 behaviour where the route was always mounted.
    #: Set False to drop the route entirely — e.g. a single-tenant app with no
    #: support-impersonation workflow that doesn't want the surface at all.
    enable_impersonation: bool = True

    #: Require a non-empty ``reason`` on every impersonation request (G9). When
    #: ``True`` (default, governance-friendly) a ``POST {root}/impersonate``
    #: without a reason is rejected with 422, and the reason is persisted on the
    #: ``ImpersonationLog`` row + the audit ``changes`` so support access to
    #: another user's (e.g. employee) data always carries a documented purpose.
    #: Set ``False`` to keep the reason optional (pre-G9 behaviour).
    impersonation_require_reason: bool = True

    #: Default audit-log retention in days (G3). The ``asterion audit prune``
    #: command deletes audit rows older than this from the public ``audit_logs``
    #: and — with ``--all-tenants`` on PostgreSQL — from every tenant schema's
    #: ``tenant_audit_logs``. Storage limitation (Art. 5) per tenant.
    audit_retention_days: int = 90

    #: Retention period (days) after which a *deactivated* user is automatically
    #: anonymised by ``privacy retention-run`` (G2 stage 2). Measured from
    #: ``User.deactivated_at``. ``None`` (default) → no auto-anonymisation; users
    #: are only anonymised manually (``user anonymize`` / ``DELETE {root}/users``).
    #: Set this only above any legal minimum-retention period (e.g. payroll /
    #: working-time records) — see docs/DATA_RETENTION.md.
    user_anonymize_after_days: int | None = None

    #: How PII values in the audit ``changes`` diff are handled (G7). The audit
    #: writer always strips *secret* keys; this controls *personal* data of
    #: fields classified in the PII registry (``email``, ``full_name``, …).
    #: ``"redact"`` (default, data-minimising) masks the value, ``"hash"``
    #: replaces it with a short SHA-256 tag (equal values stay correlatable
    #: without revealing them), ``"keep"`` retains the raw value (opt-out).
    audit_pii_mode: AuditPIIMode = "redact"

    #: Whether the audit ``changes`` diff keeps the *values* of fields classified
    #: ``BEHAVIORAL`` in the PII registry (G5 — employee-monitoring guard). ``False``
    #: (default, minimal) suppresses those values so the audit trail can't become a
    #: continuous value-level monitoring record of staff without an explicit
    #: decision (§26 BDSG / Art. 88); the row still records *that* the field
    #: changed. ``True`` keeps the diffs. Framework default fields aren't
    #: ``BEHAVIORAL`` — this bites only fields an app classifies as such.
    audit_behavioral_detail: bool = False

    tenant_resolution: TenantResolution = "header"
    tenant_header_name: str = "X-Tenant-Slug"
    #: How long (seconds) a resolved tenant is cached per process (Review R9).
    #: The cache holds the tenant's ``is_active`` / ``allowed_cidrs``, so this
    #: bounds how long a deactivation or CIDR change can be served stale by a
    #: given worker. Lower it for faster propagation; ``0`` disables caching
    #: (a DB hit per request). Cross-process changes (e.g. the CLI) propagate
    #: within this window; same-process mutations can call
    #: ``asterion.tenancy.resolver.invalidate_tenant`` for immediate effect.
    tenant_cache_ttl_seconds: int = 30

    default_language: str = "en"
    default_date_format: DateFormat = "locale"
    default_date_pattern: str = "%Y-%m-%d %H:%M"
    default_show_timezone: bool = False

    # --- PR-4: operational baseline ---
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_pre_ping: bool = True

    #: asyncpg server-side prepared-statement cache size for the PostgreSQL engine.
    #: Schema-per-tenant switches ``search_path`` on pooled connections, but asyncpg
    #: keys its prepared-statement cache by SQL *text* (not by ``search_path``): a plan
    #: prepared while connected to tenant A's schema is reused when the same pooled
    #: connection later serves tenant B, whose tables have different OIDs — raising
    #: ``InvalidCachedStatementError``. Disabling the cache (``0``) is the documented
    #: fix for ``search_path`` / PgBouncer setups. ``None`` (default) means *auto*: ``0``
    #: when :attr:`enable_multi_tenant` is on (schema-per-tenant is then the norm),
    #: otherwise asyncpg's own default is kept. Set an explicit int to override either
    #: way. Ignored for SQLite. See :meth:`resolved_statement_cache_size`.
    db_statement_cache_size: int | None = None

    log_level: str = "INFO"
    log_json: bool = False

    cors_origins: tuple[str, ...] = ()
    cors_allow_credentials: bool = False
    cors_allow_methods: tuple[str, ...] = (
        "GET",
        "POST",
        "PATCH",
        "DELETE",
        "OPTIONS",
    )
    cors_allow_headers: tuple[str, ...] = (
        "Authorization",
        "Content-Type",
        "X-Tenant-Slug",
    )

    security_headers_enabled: bool = True
    #: Optional ``Content-Security-Policy`` header value (Review R14). ``None``
    #: (default) emits no CSP. Emitted only when ``security_headers_enabled``.
    #:
    #: **With the bundled UI (G10):** include the literal token ``{nonce}`` in
    #: your ``script-src`` and the framework mints a fresh per-request nonce,
    #: substitutes it into the header, and stamps the UI's inline ``<script>``
    #: blocks with a matching nonce — so a strict policy covers the bundled UI's
    #: own scripts while still blocking injected ones. Recommended:
    #: ``"default-src 'self'; script-src 'self' 'nonce-{nonce}'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"``
    #:
    #: **API-first (no bundled UI):** any strict static policy works, e.g.
    #: ``"default-src 'self'; frame-ancestors 'none'"`` (no ``{nonce}`` needed).
    content_security_policy: str | None = None
    #: Number of trusted reverse-proxy hops in front of the app (Review R16).
    #: ``0`` (default) → the real client IP is the direct peer
    #: (``request.client.host``) and ``X-Forwarded-For`` is **ignored** (never
    #: trust a client-supplied header). Set to N when behind N trusted proxies
    #: (e.g. ``1`` behind a single nginx): the client IP is then the N-th entry
    #: from the right of ``X-Forwarded-For``. Affects the tenant IP allowlist
    #: and the audit ``ip_address``.
    trusted_proxy_count: int = 0
    #: Include the client IP in the login rate-limit key (Review R15). ``False``
    #: (default) keys on email only; ``True`` keys on ``(email, ip)`` so one
    #: source can't lock a victim out everywhere. Opt-in because it also resets
    #: the counter per IP. The IP honours ``trusted_proxy_count``.
    login_rate_limit_by_ip: bool = False
    #: Gate the admin surface by superadmin when there is no tenant context
    #: (single-tenant deployments / root scope). ``True`` (default, secure):
    #: without a tenant-role system to authorize against, only superadmins may
    #: use the admin CRUD/actions/import-export endpoints — any other
    #: authenticated, active account is rejected with 403. ``False`` restores
    #: the legacy behaviour where any authenticated caller has full access.
    single_tenant_require_superadmin: bool = True

    # --- PR-10: production guards ---
    environment: Environment = "development"

    #: Identity stack flavour. ``"builtin"`` keeps the framework's own
    #: JWT + SQLAlchemy User stack as the authoritative auth/user
    #: provider — the quickstart default. ``"external"`` says "the
    #: app is wiring its own ``auth_provider`` / ``user_provider``";
    #: ``create_admin`` rejects an external-mode start that doesn't
    #: pass at least an :class:`AuthProvider` so the misconfiguration
    #: is loud instead of silently falling back to the builtin login
    #: page. See the v1-providers roadmap and Gap-Analysis §9.
    user_mode: UserMode = "builtin"

    @classmethod
    def from_env(cls, **overrides: Any) -> CoreAdminConfig:
        config = cls(
            database_url=_env_required("ASTERION_DATABASE_URL"),
            secret_key=_env_required("ASTERION_SECRET_KEY"),
            app_title=_env_optional("ASTERION_APP_TITLE", "asterion"),
            debug=_env_bool("ASTERION_DEBUG", False),
            auth_api_prefix=_env_optional(
                "ASTERION_AUTH_API_PREFIX",
                "/api/v1/auth",
            ),
            admin_api_prefix=_env_optional(
                "ASTERION_ADMIN_API_PREFIX",
                "/api/v1/admin",
            ),
            root_api_prefix=_env_optional(
                "ASTERION_ROOT_API_PREFIX",
                "/api/v1/root",
            ),
            admin_ui_path=_env_optional(
                "ASTERION_ADMIN_UI_PATH",
                "/admin",
            ),
            jwt_algorithm=_env_optional(
                "ASTERION_JWT_ALGORITHM",
                "HS256",
            ),
            jwt_issuer=os.getenv("ASTERION_JWT_ISSUER") or None,
            jwt_audience=os.getenv("ASTERION_JWT_AUDIENCE") or None,
            access_token_expire_minutes=_env_int(
                "ASTERION_ACCESS_TOKEN_EXPIRE_MINUTES",
                60,
            ),
            refresh_token_expire_minutes=_env_int(
                "ASTERION_REFRESH_TOKEN_EXPIRE_MINUTES",
                60 * 24 * 7,
            ),
            password_reset_token_expire_minutes=_env_int(
                "ASTERION_PASSWORD_RESET_TOKEN_EXPIRE_MINUTES",
                30,
            ),
            password_reset_rate_limit_max=_env_int(
                "ASTERION_PASSWORD_RESET_RATE_LIMIT_MAX",
                5,
            ),
            password_reset_rate_limit_window_seconds=_env_int(
                "ASTERION_PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS",
                15 * 60,
            ),
            invite_token_expire_minutes=_env_int(
                "ASTERION_INVITE_TOKEN_EXPIRE_MINUTES",
                60 * 24 * 7,
            ),
            password_min_length=_env_int(
                "ASTERION_PASSWORD_MIN_LENGTH",
                8,
            ),
            password_hibp_check=_env_bool(
                "ASTERION_PASSWORD_HIBP_CHECK",
                False,
            ),
            password_hibp_timeout_seconds=_env_float(
                "ASTERION_PASSWORD_HIBP_TIMEOUT_SECONDS",
                3.0,
            ),
            enable_builtin_ui=_env_bool(
                "ASTERION_ENABLE_BUILTIN_UI",
                True,
            ),
            enable_builtin_admins=_env_bool(
                "ASTERION_ENABLE_BUILTIN_ADMINS",
                True,
            ),
            enable_multi_tenant=_env_bool(
                "ASTERION_ENABLE_MULTI_TENANT",
                True,
            ),
            enable_impersonation=_env_bool(
                "ASTERION_ENABLE_IMPERSONATION",
                True,
            ),
            impersonation_require_reason=_env_bool(
                "ASTERION_IMPERSONATION_REQUIRE_REASON",
                True,
            ),
            audit_retention_days=_env_int("ASTERION_AUDIT_RETENTION_DAYS", 90),
            user_anonymize_after_days=_env_optional_int("ASTERION_USER_ANONYMIZE_AFTER_DAYS"),
            audit_pii_mode=_env_literal(
                "ASTERION_AUDIT_PII_MODE",
                "redact",
                get_args(AuditPIIMode),
            ),
            audit_behavioral_detail=_env_bool("ASTERION_AUDIT_BEHAVIORAL_DETAIL", False),
            tenant_resolution=_env_literal(
                "ASTERION_TENANT_RESOLUTION",
                "header",
                get_args(TenantResolution),
            ),
            tenant_header_name=_env_optional(
                "ASTERION_TENANT_HEADER_NAME",
                "X-Tenant-Slug",
            ),
            tenant_cache_ttl_seconds=_env_int(
                "ASTERION_TENANT_CACHE_TTL_SECONDS",
                30,
            ),
            default_language=_env_optional(
                "ASTERION_DEFAULT_LANGUAGE",
                "en",
            ),
            default_date_format=_env_literal(
                "ASTERION_DEFAULT_DATE_FORMAT",
                "locale",
                get_args(DateFormat),
            ),
            default_date_pattern=_env_optional(
                "ASTERION_DEFAULT_DATE_PATTERN",
                "%Y-%m-%d %H:%M",
            ),
            default_show_timezone=_env_bool(
                "ASTERION_DEFAULT_SHOW_TIMEZONE",
                False,
            ),
            db_pool_size=_env_int("ASTERION_DB_POOL_SIZE", 10),
            db_max_overflow=_env_int("ASTERION_DB_MAX_OVERFLOW", 20),
            db_pool_pre_ping=_env_bool("ASTERION_DB_POOL_PRE_PING", True),
            db_statement_cache_size=_env_optional_int("ASTERION_DB_STATEMENT_CACHE_SIZE"),
            log_level=_env_optional("ASTERION_LOG_LEVEL", "INFO").upper(),
            log_json=_env_bool("ASTERION_LOG_JSON", False),
            cors_origins=_env_tuple("ASTERION_CORS_ORIGINS", ()),
            cors_allow_credentials=_env_bool("ASTERION_CORS_ALLOW_CREDENTIALS", False),
            cors_allow_methods=_env_tuple(
                "ASTERION_CORS_ALLOW_METHODS",
                ("GET", "POST", "PATCH", "DELETE", "OPTIONS"),
            ),
            cors_allow_headers=_env_tuple(
                "ASTERION_CORS_ALLOW_HEADERS",
                ("Authorization", "Content-Type", "X-Tenant-Slug"),
            ),
            security_headers_enabled=_env_bool("ASTERION_SECURITY_HEADERS_ENABLED", True),
            content_security_policy=os.getenv("ASTERION_CONTENT_SECURITY_POLICY") or None,
            trusted_proxy_count=_env_int("ASTERION_TRUSTED_PROXY_COUNT", 0),
            login_rate_limit_by_ip=_env_bool("ASTERION_LOGIN_RATE_LIMIT_BY_IP", False),
            single_tenant_require_superadmin=_env_bool(
                "ASTERION_SINGLE_TENANT_REQUIRE_SUPERADMIN", True
            ),
            environment=_env_literal(
                "ASTERION_ENVIRONMENT",
                "development",
                get_args(Environment),
            ),
            user_mode=_env_literal(
                "ASTERION_USER_MODE",
                "builtin",
                get_args(UserMode),
            ),
            storage_root=(os.getenv("ASTERION_STORAGE_ROOT") or None),
            storage_max_upload_bytes=_env_int(
                "ASTERION_STORAGE_MAX_UPLOAD_BYTES",
                25 * 1024 * 1024,
            ),
        )

        if overrides:
            valid_field_names = {field.name for field in fields(cls)}
            unknown = set(overrides) - valid_field_names

            if unknown:
                raise ValueError(f"Unknown CoreAdminConfig override(s): {sorted(unknown)}")

            config = replace(config, **overrides)

        config.validate()
        return config

    def validate(self) -> None:
        """Validate the whole config, grouped by concern.

        Each ``_validate_*`` helper raises ``ValueError`` on the first
        violation in its group; the order here is preserved from when this
        was a single flat method, so error precedence is unchanged.
        """
        self._validate_secrets()
        self._validate_token_policy()
        self._validate_paths()
        self._validate_tenancy_and_i18n()
        self._validate_operational()
        self._validate_production_footguns()

    def _validate_secrets(self) -> None:
        if not self.database_url.strip():
            raise ValueError("database_url must not be empty")
        if not self.secret_key.strip():
            raise ValueError("secret_key must not be empty")
        if self.secret_key == "change-me-in-production":
            raise ValueError("secret_key must not use the insecure default value")

    def _validate_token_policy(self) -> None:
        if self.access_token_expire_minutes <= 0:
            raise ValueError("access_token_expire_minutes must be greater than 0")
        if self.refresh_token_expire_minutes <= 0:
            raise ValueError("refresh_token_expire_minutes must be greater than 0")
        if self.password_reset_token_expire_minutes <= 0:
            raise ValueError("password_reset_token_expire_minutes must be greater than 0")
        if self.password_reset_rate_limit_max <= 0:
            raise ValueError("password_reset_rate_limit_max must be greater than 0")
        if self.password_reset_rate_limit_window_seconds <= 0:
            raise ValueError("password_reset_rate_limit_window_seconds must be greater than 0")
        if self.invite_token_expire_minutes <= 0:
            raise ValueError("invite_token_expire_minutes must be greater than 0")
        if self.password_min_length < 8:
            raise ValueError("password_min_length must be at least 8")

    def _validate_paths(self) -> None:
        if not self.auth_api_prefix.startswith("/"):
            raise ValueError("auth_api_prefix must start with '/'")
        if not self.admin_api_prefix.startswith("/"):
            raise ValueError("admin_api_prefix must start with '/'")
        if not self.root_api_prefix.startswith("/"):
            raise ValueError("root_api_prefix must start with '/'")
        if not self.admin_ui_path.startswith("/"):
            raise ValueError("admin_ui_path must start with '/'")

    def _validate_tenancy_and_i18n(self) -> None:
        if self.tenant_resolution not in get_args(TenantResolution):
            raise ValueError(f"tenant_resolution must be one of {get_args(TenantResolution)}")
        if not self.tenant_header_name.strip():
            raise ValueError("tenant_header_name must not be empty")
        if not self.default_language.strip():
            raise ValueError("default_language must not be empty")
        if self.default_date_format not in get_args(DateFormat):
            raise ValueError(f"default_date_format must be one of {get_args(DateFormat)}")
        if self.default_date_format == "custom" and not self.default_date_pattern.strip():
            raise ValueError(
                "default_date_pattern must not be empty when default_date_format='custom'"
            )

    def resolved_statement_cache_size(self) -> int | None:
        """The asyncpg statement-cache size to apply to the Postgres engine.

        ``None`` means "leave asyncpg's default in place" (don't pass
        ``connect_args``). An explicit :attr:`db_statement_cache_size` always
        wins; when unset, multi-tenant deployments disable the cache (``0``) to
        avoid ``InvalidCachedStatementError`` across ``search_path`` switches,
        while single-tenant keeps the default for the prepared-statement speed-up.
        """
        if self.db_statement_cache_size is not None:
            return self.db_statement_cache_size
        return 0 if self.enable_multi_tenant else None

    def _validate_operational(self) -> None:
        """PR-4: operational baseline (pools, logging, CORS, enums)."""
        if self.db_pool_size <= 0:
            raise ValueError("db_pool_size must be > 0")
        if self.db_max_overflow < 0:
            raise ValueError("db_max_overflow must be >= 0")
        if self.db_statement_cache_size is not None and self.db_statement_cache_size < 0:
            raise ValueError("db_statement_cache_size must be >= 0 when set")

        normalized_level = self.log_level.upper()
        if normalized_level not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"log_level must be one of {_VALID_LOG_LEVELS}, got {self.log_level!r}"
            )
        if logging.getLevelName(normalized_level) == f"Level {normalized_level}":
            # extra paranoia — should never trigger because of the list check
            raise ValueError(f"log_level {self.log_level!r} is not recognised")

        if self.cors_allow_credentials and "*" in self.cors_origins:
            raise ValueError(
                "Unsafe CORS config: cors_origins=['*'] combined with "
                "cors_allow_credentials=True is rejected by browsers and "
                "by asterion."
            )

        if self.environment not in get_args(Environment):
            raise ValueError(
                f"environment must be one of {get_args(Environment)}, got {self.environment!r}"
            )
        if self.user_mode not in get_args(UserMode):
            raise ValueError(
                f"user_mode must be one of {get_args(UserMode)}, got {self.user_mode!r}"
            )
        if self.audit_pii_mode not in get_args(AuditPIIMode):
            raise ValueError(
                f"audit_pii_mode must be one of {get_args(AuditPIIMode)}, "
                f"got {self.audit_pii_mode!r}"
            )
        if self.audit_retention_days <= 0:
            raise ValueError("audit_retention_days must be greater than 0")
        if self.user_anonymize_after_days is not None and self.user_anonymize_after_days <= 0:
            raise ValueError("user_anonymize_after_days must be greater than 0 when set")
        if self.storage_max_upload_bytes <= 0:
            raise ValueError("storage_max_upload_bytes must be > 0")

    def _validate_production_footguns(self) -> None:
        """PR-10: refuse to boot with insecure config in production.

        validate() is called from from_env() AND from create_admin(). In
        production mode we hard-fail rather than warn — a misconfigured
        production deployment is worse than a crash at startup.
        """
        if self.environment != "production":
            return
        if self.debug:
            raise ValueError("debug=True is not allowed in production; set ASTERION_DEBUG=false.")
        if self.database_url.startswith(("sqlite://", "sqlite+aiosqlite://")):
            raise ValueError(
                "SQLite is not allowed in production; use a PostgreSQL "
                "database_url. SQLite is for local dev + tests only."
            )
        if len(self.secret_key) < MIN_PRODUCTION_SECRET_LENGTH:
            raise ValueError(
                f"secret_key must be at least {MIN_PRODUCTION_SECRET_LENGTH} "
                "characters in production; generate one with "
                "`openssl rand -hex 32`."
            )

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "app_title": self.app_title,
            "debug": self.debug,
            "auth_api_prefix": self.auth_api_prefix,
            "admin_api_prefix": self.admin_api_prefix,
            "root_api_prefix": self.root_api_prefix,
            "admin_ui_path": self.admin_ui_path,
            "jwt_algorithm": self.jwt_algorithm,
            "access_token_expire_minutes": self.access_token_expire_minutes,
            "refresh_token_expire_minutes": self.refresh_token_expire_minutes,
            "password_reset_token_expire_minutes": self.password_reset_token_expire_minutes,
            "password_reset_rate_limit_max": self.password_reset_rate_limit_max,
            "password_reset_rate_limit_window_seconds": (
                self.password_reset_rate_limit_window_seconds
            ),
            "invite_token_expire_minutes": self.invite_token_expire_minutes,
            "password_min_length": self.password_min_length,
            "password_hibp_check": self.password_hibp_check,
            "enable_builtin_ui": self.enable_builtin_ui,
            "enable_builtin_admins": self.enable_builtin_admins,
            "enable_multi_tenant": self.enable_multi_tenant,
            "enable_impersonation": self.enable_impersonation,
            "impersonation_require_reason": self.impersonation_require_reason,
            "audit_retention_days": self.audit_retention_days,
            "user_anonymize_after_days": self.user_anonymize_after_days,
            "audit_pii_mode": self.audit_pii_mode,
            "audit_behavioral_detail": self.audit_behavioral_detail,
            "tenant_resolution": self.tenant_resolution,
            "tenant_header_name": self.tenant_header_name,
            "default_language": self.default_language,
            "default_date_format": self.default_date_format,
            "default_date_pattern": self.default_date_pattern,
            "default_show_timezone": self.default_show_timezone,
            "db_pool_size": self.db_pool_size,
            "db_max_overflow": self.db_max_overflow,
            "db_pool_pre_ping": self.db_pool_pre_ping,
            "db_statement_cache_size": self.db_statement_cache_size,
            "log_level": self.log_level,
            "log_json": self.log_json,
            "cors_origins": list(self.cors_origins),
            "cors_allow_credentials": self.cors_allow_credentials,
            "cors_allow_methods": list(self.cors_allow_methods),
            "cors_allow_headers": list(self.cors_allow_headers),
            "security_headers_enabled": self.security_headers_enabled,
            "environment": self.environment,
            "user_mode": self.user_mode,
            "storage_root": self.storage_root,
            "storage_max_upload_bytes": self.storage_max_upload_bytes,
            "database_url_set": bool(self.database_url),
            "secret_key_set": bool(self.secret_key),
        }
