# adminfoundry — Konsolidierte Roadmap (offene Punkte)

Konsolidiert aus drei Quelldokumenten:

- `adminfoundry_admin_package_gap_analysis.md` (Architektur-Reife / Feature-Tiefe)
- `adminfoundry_robustheit_ux_10_punkte.md` (Robustheit + DX, keine neuen Features)
- `adminfoundry_v1_core_extensions_roadmap.md` (Core/Security/Extensions/Enterprise-Phasen)

Bereits umgesetzt (1038 Tests grün) wird hier **nicht** wiederholt — siehe
`memory/project_adminfoundry_v1_refactor.md` für den Stand der drei
Refactor-Wellen (Production-Ready PRs, Provider-Refactor, Block A/B/C/D).

---

## Leitlinie für die nächste Phase

> **Erst Robustheit + Architektur-Konsolidierung. Dann Auth-Hardening.
> Dann Extensions. UI- und Enterprise-Tiefe parallel oder zuletzt.**

Diese Reihenfolge stammt aus dem Robustheits-Dokument („Nicht mehr
Architektur erfinden. Bestehende Architektur verbindlich machen") und
ist mit der Realität aus den anderen Quellen abgeglichen: Block A/B/C/D
hat *viel* Architektur-Erweiterung gebracht, jetzt müssen die Kanten
glatt gefeilt werden, bevor neue Themen draufkommen.

---

## 0. Architektur-Audit — was vor neuen Features aufgeräumt gehört

Vier konkrete Befunde, die *jetzt* relevant sind, weil sie spätere
Phasen blockieren oder duplizieren würden:

### A0.1 `AdminRegistry.freeze()` fehlt

`PermissionRegistry` und `ExtensionRegistry` haben `freeze()` + `is_frozen`
([authz/registry.py:51](adminfoundry/authz/registry.py#L51),
[extensions/registry.py:55](adminfoundry/extensions/registry.py#L55)).
`AdminRegistry` ([registry/registry.py](adminfoundry/registry/registry.py))
hat keinen Freeze-Mechanismus — mutiert kann theoretisch nach App-Start
weitergehen. Robustheits-Doc §8 ist ein expliziter Auftrag.

### A0.2 Toter Module-Level Singleton: `schema_builder`

[schemas/builder.py:138](adminfoundry/schemas/builder.py#L138) hat
`schema_builder = SchemaBuilder()` — kein einziger Import nutzt ihn
mehr (überprüft per Grep). Robustheits-Doc §2 verbietet globale
Singletons. Entfernen statt deprecaten, weil 0 Aufrufer.

### A0.3 Serializer dupliziert Type-Coercion

`schemas/serialization/serializer.py:_serialize_value` macht
`UUID → str` + `datetime → isoformat` hartkodiert.
`FieldAdapter.serialize(value, ctx)` ist als Hook gedacht, defaultet
aktuell auf no-op. Robustheits-Doc §4 fordert **eine** Pipeline für
Field-Logik. Konsolidierung: Adapter setzt sein Serializing, Serializer
delegiert.

### A0.4 Drei Field-Visibility-Mechanismen nebeneinander

| Mechanismus | Ort | Granularität |
|---|---|---|
| `protected_fields` (Liste) | `ModelAdmin` + globale Registry | global, immer |
| `readonly_fields` (Liste) | `ModelAdmin` | global, write-block |
| `AdminPolicy.field_permission()` | per-user, async | per-user/per-row |

Funktional kombinieren sie sich (UND-verknüpft), aber Gap-Analysis §8
sagt: „Komfort-Konfigurationen dürfen bleiben, sollten aber intern in
Policies übersetzt werden." Aktuell sind sie nebeneinander, nicht
übersetzt. Refactor-Reserve, nicht dringend; aber relevant bevor mehr
darauf gebaut wird.

### A0.5 Direkte `User`-Modell-Imports außerhalb der Builtin-Provider

Akzeptabel in `providers/auth.py` + `providers/users.py` (sind die
Builtin-Implementationen). **Nicht** akzeptabel in:

- `adminfoundry/root/users.py` + `tenants.py` + `impersonation.py`
- `adminfoundry/auth/router.py` + `auth/dependencies.py`
- `adminfoundry/audit/service.py`
- `adminfoundry/tenancy/bootstrap.py`
- `adminfoundry/cli/main.py`

Robustheits-Doc §3 verlangt: Core-Module dürfen keine konkreten Modelle
importieren. Ohne diesen Fix bleibt „external auth mode" eine Halb-
wahrheit — siehe Phase 1.5 unten.

---

## Roadmap — Phasen

### Phase 1 — Robustheit-Härtung (Doc 2 unfinished + Audit-Findings)

**Ziel:** Bestehende Architektur verbindlich machen, keine neuen Features.

| # | Aufgabe | Quelle | Aufwand |
|---|---|---|---|
| 1.1 | `AdminRegistry.freeze()` + `is_frozen` einbauen; alle Mutationen prüfen; Lifespan-Hook ruft freeze nach Setup | Doc 2 §8 + Audit A0.1 | klein |
| 1.2 | Module-Level `schema_builder` Singleton entfernen | Doc 2 §2 + Audit A0.2 | trivial |
| 1.3 | Serializer durch FieldAdapter-Pipeline leiten (UUID/datetime-Coercion in Adapter verlagern) | Doc 2 §4 + Audit A0.3 | mittel |
| 1.4 | Public-API-Test: `tests/public_api/` prüft welche Symbole offiziell exportiert werden; Dokumentation listet sie auf | Doc 2 §1 | klein |
| 1.5 | `auth/router.py`, `root/*`, `audit/service.py` auf `UserProvider`/`AdminPrincipal` statt direkter `User`-Import; Audit-Service nimmt `AdminPrincipal` statt `User` | Doc 2 §3 + Audit A0.5 | mittel-groß |
| 1.6 | CRUD-Testmatrix vervollständigen: parametrisierte Tests für `{create, list, read, update, delete} × {success, validation_error, permission_error, not_found, protected_field, readonly_field, conflict, transaction_rollback}` | Doc 2 §7 | mittel |
| 1.7 | `protected_fields`-Pfad-Audit: gleicher Test-Sweep über List/Detail/Create/Update/Contract/Audit/Error/Debug — protected darf nirgends auftauchen | Doc 2 §5 | klein-mittel |
| 1.8 | Beispiele kuratieren: `minimal_single_file`, `basic_app`, `custom_auth_provider`, `multi_tenant_subdomain`. CI-Test pro Beispiel. Keine internen Imports | Doc 2 §10 | mittel |

**DoD:** Two Admin-Apps können im selben Prozess mit unterschiedlicher
Config laufen. `AdminRegistry` ist nach Freeze unveränderlich. Public
API ist getestet + dokumentiert. Externe Auth funktioniert vollständig
(ohne `User`-Imports in Core-Pfaden). CRUD-Matrix grün.

---

### Phase 2 — Architektur-Konsolidierung (Gap-Analysis Reste, die Phase 1 unterstützen)

**Ziel:** Die drei Wege „field policy" zu einem konsolidieren; Inline-
Permission schließen; Provider-Schicht vervollständigen.

| # | Aufgabe | Quelle | Aufwand |
|---|---|---|---|
| 2.1 | `protected_fields` + `readonly_fields` intern in `field_permission(...)` übersetzen. Die Liste-Form bleibt API-Convenience, aber **eine** Pipeline entscheidet. Test: Custom Policy kann eine `readonly_fields`-Deklaration overrulen | Gap §8 + Audit A0.4 | mittel |
| 2.2 | `InlineAdmin.policy: AdminPolicy \| None` + Plumbing in `process_inline_writes` (can_view_object / can_update_object / can_delete_object pro Inline-Row) | Gap §4 Mindestfunktion | mittel |
| 2.3 | Validation-Hints aus Adaptern füllen (`max_length` aus `Column(String(200))`, `pattern` aus `Column(...)` constraints) — Slot `FieldMeta.validation` existiert seit A4, leer | Gap §6 | klein |
| 2.4 | `FieldPermission` im Contract sichtbar machen: `FieldMeta.field_permission: "read"\|"write"\|"hidden"` (per-caller) | Gap §6 | klein |
| 2.5 | `UserProvider.list_users(query) -> Page[AdminPrincipal]` + Plumbing in `root/users.py` (statt direkter `User`-Query) | Gap §1 | mittel |
| 2.6 | `AuthProvider.login(credentials)` + `logout(request)` — Login/Logout-Flow auf Provider auslagern, `auth/router.py` wird dünner Wrapper | Gap §1 | groß |

**DoD:** Eine `AdminPolicy.field_permission()`-Implementierung kann
ALLE Visibility-/Writability-Regeln steuern. Inline-Children
respektieren ihre eigene Policy. Externe Auth (z. B. Keycloak) kann
Login/Logout vollständig übernehmen ohne den Builtin-Flow zu patchen.

**Bewusst NICHT in dieser Phase:** `PermissionProvider.can(user, action, resource, obj, ctx)`. Plan-Vorschlag wird **abgelehnt**, weil er
mit `AdminPolicy.can_*_object()` konkurriert. Trennung bleibt:
PermissionProvider liefert *Keys*, AdminPolicy macht *Object/Field*-
Entscheidungen.

---

### Phase 3 — Auth-Hardening (Pre-Extension Pflicht)

**Ziel:** Sicherheits-Baseline für öffentliche Admin-Panels. Reihenfolge
folgt Roadmap-Doc Milestone 5, aber vorgezogen weil sie Pre-Extension-
Voraussetzung ist (Extensions wie Webhooks/Jobs lehnen sich an die Auth-
Pfade an).

| # | Aufgabe | Quelle | Aufwand |
|---|---|---|---|
| 3.1 | Refresh Tokens — `POST /auth/refresh`, Token-Pair-Modell, längere TTL für Refresh, Cookie-/Header-Strategie dokumentiert | Roadmap Post-v1 | mittel |
| 3.2 | `RevokedToken` DB-first (single-token logout); `auth/logout` revoked das aktuelle JWT, ohne `token_version` zu bumpen | Roadmap §6 + Post-v1 | mittel |
| 3.3 | Password-Reset Flow: Token-Tabelle, `/auth/password-reset-request` + `/auth/password-reset-confirm`, E-Mail-Hook als Provider-Interface | Roadmap Post-v1 | mittel-groß |
| 3.4 | 2FA / TOTP — Setup-Flow, Backup-Codes, Recovery, Login-Step-Up. Provider-Interface offen lassen (für Authenticator-Apps) | Roadmap Post-v1 | groß |
| 3.5 | OAuth-Flow vervollständigen — `extensions/auth_oauth/` ist teilweise da; vollständigen OIDC-Flow + JWKS-Caching + Refresh-Handling abschließen | Gap §1 + Roadmap Post-v1 | mittel |

**DoD:** Ein öffentliches Admin-Panel kann sicher betrieben werden:
2FA verpflichtbar pro User, Password-Reset funktioniert ohne manuellen
Admin-Eingriff, einzelne Sessions logoutbar.

**Bewusst NICHT in dieser Phase:** SCIM, SAML (Phase 6).

---

### Phase 4 — Extension-Fundament (Events + Jobs + Storage SPI)

**Ziel:** Drei Querschnitts-Primitiven bauen, die ALLE folgenden
Extensions brauchen. Ohne sie würden Webhooks/Workflows/Observability
jeweils ihre eigene halbgare Lösung erfinden.

| # | Aufgabe | Quelle | Aufwand |
|---|---|---|---|
| 4.1 | **Event-Abstraktion**: `EventBus`-Protocol (publish/subscribe), Domain-Events `crud.created/updated/deleted`, `action.executed`, `auth.login_success/failure`. Audit-Service wird Consumer dieses Bus. Synchron im selben TX, async opt-in via Job. | Doc 3 E3 „Braucht vorher: Audit/Event-Abstraktion" | mittel-groß |
| 4.2 | **Job-Queue-Protocol**: `JobQueue`-Protocol (`enqueue(name, payload, ctx)`, `worker_loop()`), In-Memory-Default, RQ/Celery/SQS-Adapter später. JobModel in DB für Status-Tracking. | Doc 3 E4 | groß |
| 4.3 | **Storage SPI**: `StorageBackend`-Protocol (`save(key, bytes)`, `read(key)`, `presigned_url(key, ttl)`, `delete(key)`), Local-FS-Default. S3-Adapter als Extension. | Doc 3 E7 | mittel |

**DoD:** Webhooks/Workflows/Observability/Dashboard können sich auf
diese drei Primitiven stützen, ohne sie zu duplizieren.

---

### Phase 5 — Extension-Welle 1

Reihenfolge wählt die einfachsten zuerst, damit jede Extension
unabhängig nutzbar ist.

| # | Extension | Stützt sich auf | Aufwand |
|---|---|---|---|
| 5.1 | **Observability** (`extensions/observability/`) — Prometheus-Counter für request_count/duration, audit-Events, error_rate; optionaler `/metrics`-Endpoint | Phase 4.1 (EventBus) | mittel |
| 5.2 | **Webhooks** (`extensions/webhooks/`) — Webhook-Subscription-Modell, HMAC-Signing, Retry mit Exponential Backoff, Dead-Letter-Tabelle | Phase 4.1 + 4.2 | mittel-groß |
| 5.3 | **Jobs UI** (`extensions/jobs_admin/`) — Admin-Resource für JobModel mit Status/Retry/Cancel-Actions | Phase 4.2 | klein |
| 5.4 | **Workflows / Approval** (`extensions/workflows/`) — State-Machine, Reviewer-Liste, `requires_approval=True` auf ModelAdmin, Audit-Diff vor + nach Approval | Phase 4.1 + 4.2 | groß |
| 5.5 | **Dashboard** (`extensions/dashboard/`) — Widget-Protocol, Counts/Recent-Activity/Audit-Stream als Default-Widgets, App-eigene Widgets via Registry | Phase 5.1 + 4.1 | mittel |
| 5.6 | **FileField / ImageField** + **S3-Storage-Extension** — `fields/files.py` (Plan-Modul, fehlt), `extensions/storage_s3/` als Storage-Backend-Adapter | Phase 4.3 + Gap §5 + §13 | mittel-groß |
| 5.7 | **Import/Export ausbauen** — Dry-Run, Fehler-Report, Bulk-Validation, async via Job-Queue; CSV/JSON/XLSX heute schon da, fehlen Dry-Run + Async | Phase 4.2 | mittel |

**DoD:** Jede Extension ist optional, hat eigene Doku + Beispiel-App
unter `examples/`, ist CI-getestet.

---

### Phase 6 — UI- und Enterprise-Tiefe (parallel möglich)

**Ziel:** Was in Doc 1 §11/§12 als „spätere Erweiterungen" markiert ist.
Reine Frontend-Arbeit + Enterprise-Identity. Können parallel zu Phase 4/5
laufen, wenn Frontend-Kapazität vorhanden ist.

| # | Bereich | Aufgaben |
|---|---|---|
| 6.1 | **Form Layout (UI)** | Tabs, Conditional Fields, Dependent Fields, Side Panels, Placeholders, Custom Components |
| 6.2 | **List View (UI)** | Date Hierarchy, Bulk Edit (`list_editable`), Custom Row Badges, List Density, Default Ordering per User, Column Visibility als dedizierter Mechanismus statt SavedFilter-Payload |
| 6.3 | **Admin Pages / Plugin Slots** | Custom Pages außerhalb des CRUD-Schemas (für Reports, Tools); UI-Plugin-Slots für Extensions | Gap §14 P1 |
| 6.4 | **Audit-UI** | Read-only Admin auf `AuditLog`-Tabelle mit Diff-Viewer, Filter, Export |
| 6.5 | **Permission-Matrix-UI** | RBAC-Matrix-Editor für `TenantRole × Permissions` |
| 6.6 | **SCIM / SAML** | Enterprise-Identity-Standards. SCIM-Provisioning, SAML-Login |
| 6.7 | **Action Progress + Partial Success** | Long-running Actions melden Progress via Job-Status, Partial-Failure-Report im Response | Gap §10 + Phase 4.2 |

---

### Phase 7 — Post-v1 Enterprise (parken)

Bewusst zuletzt, kein nahes Deadline-Ziel:

- Billing / Metering / Usage Seats
- White Labeling
- Multi-Region Tenancy
- Redis Distributed Cache (Rate Limiter Protocol existiert; Backend ist Extension)
- Flutter UI

---

## Dont's (durchgängig)

Aus den drei Quelldokumenten kondensiert — explizite Nicht-Ziele:

- **Keine neuen Produktfeatures**, bevor Phase 1 + 2 durch sind. Doc 2
  ist hier eindeutig: „Erst Robustheit. Dann Feature-Tiefe."
- **Kein `PermissionProvider.can(obj, ...)`** — würde mit AdminPolicy
  konkurrieren. PermissionProvider liefert Keys, AdminPolicy macht
  Object/Field-Entscheidungen. (Gap §1 Vorschlag ablehnen.)
- **Keine row-level Tenancy** wieder einführen — Roadmap-Doc §C6 explizit.
- **Keine globalen Singletons** mehr neu erschaffen. Bestehende
  (`protected_fields._singleton`, `schema_builder`) entweder entfernen
  (4.2) oder app-state-scopen (Phase 1).
- **Kein OIDC/SAML/SCIM** in Auth-Hardening (Phase 3) — gehört nach
  Phase 6.6, wenn Refresh/2FA stabil sind.

---

## Definition of Done pro Phase

| Phase | Definition of Done |
|---|---|
| 1 (Robustheit) | AdminRegistry-Freeze hart; keine globalen Singletons (außer dokumentiert); Public API getestet; `User`-Imports nur in Builtin-Providern; CRUD-Testmatrix vollständig; Beispiele kuratiert + CI-getestet |
| 2 (Konsolidierung) | Eine FieldPolicy-Pipeline (kein 3-Wege-Mechanismus mehr); Inline-Permission funktional; `AuthProvider.login/logout` + `UserProvider.list_users` im Protocol |
| 3 (Auth-Hardening) | Refresh Tokens + RevokedToken + Password Reset + 2FA produktiv; OAuth-Flow vollständig |
| 4 (Extension-Fundament) | EventBus + JobQueue + StorageBackend als Protocols + Default-Implementierungen + Tests |
| 5 (Extensions Welle 1) | Observability, Webhooks, Jobs, Workflows, Dashboard, Storage S3, Import/Export-Ausbau, FileField — jede als optionale Extension mit Doku + Beispiel |
| 6 (UI + Enterprise) | UI-Tiefe gemäß Doc 1 §11/§12 + SCIM/SAML |
| 7 (Enterprise Restposten) | nicht zwingend abgeschlossen — geparkt bis Bedarf |

---

## Kurz-Zusammenfassung

Stand: **prod-ready Core** + **Architektur-Reife (Block A–D)** ist
erreicht. Was offen ist gliedert sich in fünf nicht-überlappende Themen:

1. **Robustheits-Kanten glätten** (Doc 2 Reste + 5 Audit-Findings)
2. **Eine FieldPolicy + komplette Provider-Schicht** (Gap §1, §4, §6, §8 Reste)
3. **Auth-Hardening für public Admin-Panels** (Refresh/Revoke/Reset/2FA/OAuth)
4. **Extension-Fundament + Welle 1** (EventBus, Jobs, Storage SPI als Voraussetzung; dann Webhooks/Observability/Dashboard/Workflows/Files)
5. **UI-Tiefe + Enterprise-Identity** (Tabs/Bulk Edit/Audit-UI/SCIM/SAML)

Empfehlung: Phasen 1 + 2 sind die nächste sinnvolle Arbeitseinheit
(Robustheit + Konsolidierung), bevor neue Feature-Ebenen aufgesetzt
werden. Phase 3 (Auth-Hardening) ist die zweite, weil ohne sie kein
externer Admin-Login produktiv geht.
