---
name: Offene Todos
description: Bekannte Lücken und geplante Features die noch nicht implementiert sind
type: project
---

## Sicherheit / Auth

1. **Tenant `is_active` durchsetzen** — `DisableTenantAction` setzt `is_active = False`, aber adminfoundry blockiert Zugriffe inaktiver Tenants aktuell nicht. Muss in `TenantMiddleware` oder `AuthProvider` ausgewertet werden.

2. **2FA (TOTP)** — Time-based One-Time Password als optionales Auth-Feature. Klarer Vorteil gegenüber Django Admin, das kein built-in 2FA hat.

3. **IP-Allowlist per Tenant** — Ergänzt `is_active`-Enforcement; Zugang nur aus definierten IP-Ranges.

## Permissions / Daten

4. ✅ **Permission-Matrix pro Rolle** — `RolePermission(role_id, model_name, can_list, can_create, can_update, can_delete)` implementiert. `PolicyEngine.effective_model_caps()` nimmt jetzt optionalen `db_caps`-Override. Async-Helper `fetch_model_caps` / `fetch_all_model_caps` in `adminfoundry/authz/role_caps.py`. `RolePermissionAdmin` registriert. Tests in `tests/test_role_permissions.py`. Migration `0003_add_role_permissions.py`. **Offen:** `_check_model_access` in `router.py` prüft noch nicht die DB-Permissions (nur `effective_model_caps` tut es).

5. ✅ **Field-level Permissions (per-Record)** — `PolicyEngine.evaluate_field()` nimmt jetzt optionalen `record`-Parameter. ModelAdmin kann `field_permission(user, field_name, record) -> FieldPolicy | None` überschreiben. `update_object`-Route übergibt das Objekt an `evaluate_field`.

5. ✅ **Custom Filters** — `FilterBuilder.build_filters()` unterstützt jetzt `range_filter_fields` (`field__gte` / `field__lte`) und `enum_filter_fields` (`field__in=a,b,c`). Beide Attribute auf `ModelAdmin` definiert. Werden automatisch aus Query-Params ausgelesen.

## UI / UX

6. ✅ **Inline-Relations** — `inline_fields: list[str]` auf ModelAdmin (Relationship-Attributnamen). Contract liefert `inline_relations: list[InlineRelationMeta]`. Serializer gibt verschachtelte Objekte zurück. **Offen:** Volle UI-Darstellung im admin.js (Inline-Formular rendern, Speichern).

7. ✅ **List-Editable** — `list_editable: list[str]` auf ModelAdmin. Contract enthält `list_editable`-Feld. admin.js rendert `<input class="list-inline-input">` für editierbare Felder und speichert per blur → PATCH.

8. ✅ **Audit Log im UI** — `AuditLogAdmin` registriert in `admin_config.py`. Read-only, Filter auf `action`/`method`/`status_code`, Range-Filter auf `created_at`, Sortierung nach `-created_at`.

9. ✅ **Breadcrumb-Navigation** — `setBreadcrumb(parts)` Funktion in admin.js. `<nav id="breadcrumb">` in base.html. Breadcrumbs werden in initList, initDetail, initCreate, initUpdate gesetzt.

10. ✅ **Dark Mode** — CSS Custom Properties + `html[data-theme="dark"]`. System-Präferenz via `@media (prefers-color-scheme: dark)`. Manueller Toggle via Button in Sidebar-Footer. Präferenz in localStorage gespeichert.

## Ops / Observability

11. ✅ **Health-Dashboard** — `GET /health/dashboard` aggregiert DB-Status, aktive Sessions, Rate-Limit-Config, Metriken-Snapshot, letzte 5 Jobs.

12. ✅ **Metrics-Endpoint** — `GET /metrics` im Prometheus-Format (text/plain). Enthält: requests_total, request_errors_total, actions_total, action_errors_total, audit_write_failures_total, active_sessions.

13. **CSV/Excel Export** — JSON-Export läuft bereits; CSV/XLSX ist für Ops-Workflows deutlich häufiger gefragt.
