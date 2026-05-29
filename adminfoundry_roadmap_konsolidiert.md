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

### Phase 1 — Robustheit-Härtung ✅ ABGESCHLOSSEN

**Ziel:** Bestehende Architektur verbindlich machen, keine neuen Features.

| # | Aufgabe | Status |
|---|---|---|
| 1.1 | `AdminRegistry.freeze()` + `is_frozen`; freeze nach Setup in `create_admin` | ✅ |
| 1.2 | Toter Module-Level `schema_builder` Singleton entfernt | ✅ |
| 1.3 | Serializer durch FieldAdapter-Pipeline (UUID/datetime-Coercion in Adapter) | ✅ |
| 1.4 | Public-API-Test `tests/public_api/test_imports.py` (4 Surfaces, `__all__` gepinnt) | ✅ |
| 1.5 | `audit/service.py` auf `AdminPrincipal` statt `User`. `root/*` + `auth/router` bleiben Builtin-Pfade (→ 2.5/2.6 lösen sie sauber auf) | ✅ scope-reduced |
| 1.6 | CRUD-Matrix-Lücken: update/delete PK-coercion, validate-rollback, unique-conflict | ✅ |
| 1.7 | Protected-Fields-Sweep über list/detail/update/contract/audit/sanitize | ✅ |
| 1.8 | Examples-Smoke-Parity (multi_tenant). Die 3 zusätzlichen Beispiele aus Doc-2 §10 bleiben offen | ✅ scope-reduced |

Commits: `2c41f4a`, `d188417`, `114ce40`, `b768270`, `ec10862`, `a435032`.

---

### Phase 2 — Architektur-Konsolidierung ✅ ABGESCHLOSSEN

**Ziel:** Die drei Wege „field policy" zu einem konsolidieren; Inline-
Permission schließen; Provider-Schicht vervollständigen.

| # | Aufgabe | Status |
|---|---|---|
| 2.1 | `static_field_permission()` + `FieldPermission.strictest()` — eine Resolution-Regel vereint protected/readonly/calculated/policy. Policy kann nur verschärfen, nie lockern | ✅ |
| 2.2 | `InlineAdmin.policy` + per-Inline-Gating in `process_inline_writes` (can_create/can_update_object/can_delete_object pro Row) | ✅ |
| 2.3 | Validation-Hints: `StringAdapter`/`TextAdapter` emittieren `max_length` → `FieldMeta.validation` | ✅ |
| 2.4 | `FieldMeta.field_permission: "read"/"write"/"hidden"` per-caller via `compute_field_permissions` | ✅ |
| 2.5 | `UserListingProvider.list_users(query) -> Page` (separates optionales Protocol) + `root/users.py` list über Provider | ✅ |
| 2.6 | `CredentialAuthProvider.login(credentials) -> AuthSession` (separates optionales Protocol) + `auth/router` delegiert. `logout-all` bleibt Builtin (token_version) | ✅ |

Commits: `4c03e84`, `d5b0434`, `1442421`, `c542477`, `1598933`.

**Architektur-Entscheidung in Phase 2:** Optionale Provider-Fähigkeiten
(`list_users`, `login`) wurden als **separate runtime_checkable
Protocols** (`UserListingProvider`, `CredentialAuthProvider`) modelliert,
statt die Basis-Protocols zu verbreitern — sonst müsste jeder auth-only
externe Provider sie implementieren, nur um `isinstance(x, UserProvider)`
zu bestehen. Endpoints erkennen die Fähigkeit per `hasattr` und geben
501 zurück, wenn sie fehlt. `PermissionProvider.can(obj, ...)` bleibt
**bewusst abgelehnt** (würde mit `AdminPolicy` konkurrieren).

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

### Phase 4 — Storage-SPI + File/Image Fields

**Ziel:** Den einzig konkret nachgefragten Extension-Use-Case sauber
abdecken: Datei-Uploads. SPI klein halten, S3-Adapter als Beispiel.

| # | Aufgabe | Aufwand |
|---|---|---|
| 4.1 | **Storage SPI**: `StorageBackend`-Protocol (`save(key, bytes)`, `read(key)`, `presigned_url(key, ttl)`, `delete(key)`), Local-FS-Default. Klein, kein Job-Queue-/Event-Hop dazwischen. | klein |
| 4.2 | **FileField / ImageField** + `fields/files.py` (Plan-Modul, fehlt). Upload-Endpoint + Storage-Backend-Aufruf, Validation (MIME, Größe). | mittel |
| 4.3 | **S3-Storage-Adapter** als optionale Extension `extensions/storage_s3/` (boto3-Dependency). | klein |
| 4.4 | **Generic `Notifier`-Protocol** — Verallgemeinerung des `PasswordResetNotifier` aus 3.3 zu einem app-weiten Notifier-SPI für transaktionale Emails (welcome, password-change-notification, 2FA-disabled-warnung). Klein, hoher Wiederverwendungswert. | klein |

**DoD:** Eine App kann FileField nutzen, lokales Storage funktioniert
out-of-the-box, S3-Wechsel ist ein Provider-Tausch.

---

### Phase 5 — UI-Tiefe + Admin-UI-Erweiterungen

**Ziel:** Bestehende Daten (Audit, RBAC, AdminContext) endlich in der UI
sichtbar machen. Frontend-Arbeit.

| # | Bereich | Wert |
|---|---|---|
| 5.1 | **Audit-UI** — Read-only Admin auf `AuditLog`-Tabelle mit Diff-Viewer, Filter, Export. Kleines Add-on, sofort hoher Compliance/Debugging-Wert. | hoch |
| 5.2 | **Permission-Matrix-UI** — RBAC-Matrix-Editor für `TenantRole × Permissions`. Schließt eine bekannte UX-Lücke. | hoch |
| 5.3 | **Import/Export-Ausbau** — Dry-Run, Fehler-Report, Bulk-Validation. Heute schon CSV/JSON/XLSX da, fehlen Dry-Run + Report. (Async via Jobs entfällt — siehe „Bewusst gestrichen" unten.) | mittel |
| 5.4 | **Form Layout (UI)** — Tabs, Conditional Fields, Dependent Fields, Side Panels, Placeholders, Custom Components | mittel |
| 5.5 | **List View (UI)** — Date Hierarchy, Bulk Edit (`list_editable`), Custom Row Badges, List Density, Default Ordering per User, Column Visibility als dedizierter Mechanismus | mittel |
| 5.6 | **Admin Pages / Plugin Slots** — Custom Pages außerhalb des CRUD-Schemas (für Reports, Tools); UI-Plugin-Slots für Extensions | mittel |

---

### Phase 6 — Enterprise-Identity

| # | Bereich | Wert |
|---|---|---|
| 6.1 | **SCIM / SAML** — Enterprise-Identity-Standards. SCIM-Provisioning, SAML-Login | hoch (für Enterprise-Kunden) |

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
  Phase 6, wenn Refresh/2FA stabil sind.

### Bewusst gestrichen (waren in früherer Version, jetzt raus)

Sechs Items aus der alten Phase 4 + 5, die nach kritischer Bewertung
mehr Komplexität als Wert eingebracht hätten — siehe Commit-Diskussion:

- **EventBus / Domain-Event-System** — Audit + Lifecycle-Hooks reichen.
  Apps mit eigenem Event-Bedarf bauen einen winzigen `emit()` direkt.
- **JobQueue (Framework-eigen)** — jede Produktiv-App hat schon ein
  Celery/RQ/arq. Framework-eigenes Protocol wird nicht genutzt.
- **Observability / `/metrics` als Extension** — wäre ~80 LoC; falls
  überhaupt, gehört es direkt in den Core, nicht in eine Extension.
- **Webhooks-Extension** — Apps nutzen Svix/Hookdeck/eigene Lambda;
  Framework-Webhooks mit HMAC/Retry/Dead-Letter sind 1500+ LoC für
  selten genutzte Funktionalität.
- **Jobs UI** — abhängig von der gestrichenen JobQueue, fällt mit ihr.
- **Workflows / Approval-Engine** — ist ein eigenes Produkt
  (State-Machine, Reviewer-Pools, Notifications). Wer's braucht,
  baut spezifisch oder kauft ein dediziertes Tool.

Daraus folgt: **kein Phase-4-„Fundament"** (EventBus + JobQueue + Storage
zusammen war Overengineering). Storage-SPI ist klein genug, um zusammen
mit File/Image direkt als Phase 4 zu laufen, und braucht keine
Querschnitts-Primitiven obendrauf. Action-Progress (Gap §10) entfällt
ebenfalls, da es Jobs voraussetzte.

---

## Definition of Done pro Phase

| Phase | Definition of Done |
|---|---|
| 1 (Robustheit) | AdminRegistry-Freeze hart; keine globalen Singletons (außer dokumentiert); Public API getestet; `User`-Imports nur in Builtin-Providern; CRUD-Testmatrix vollständig; Beispiele kuratiert + CI-getestet |
| 2 (Konsolidierung) | Eine FieldPolicy-Pipeline (kein 3-Wege-Mechanismus mehr); Inline-Permission funktional; `AuthProvider.login/logout` + `UserProvider.list_users` im Protocol |
| 3 (Auth-Hardening) | Refresh Tokens + RevokedToken + Password Reset + 2FA produktiv; OAuth-Flow vollständig |
| 4 (Storage + Files) | StorageBackend-SPI + FileField/ImageField + S3-Adapter + Generic Notifier-Protocol |
| 5 (UI-Tiefe) | Audit-UI + Permission-Matrix-UI + Form-Layout-Erweiterungen + List-View-Erweiterungen + Admin-Pages |
| 6 (Enterprise-Identity) | SCIM-Provisioning + SAML-Login |
| 7 (Enterprise Restposten) | nicht zwingend abgeschlossen — geparkt bis Bedarf |

---

## Kurz-Zusammenfassung

Stand: **Phasen 1 + 2** ✅ und der Großteil von **Phase 3** (Auth-
Hardening: 3.1 Refresh, 3.2 Revocation, 3.3 Password-Reset, 3.4a 2FA-
Enrollment) sind committed. Offen sind:

1. **Phase 3 Rest** — 3.4b (Login-Step-Up) + 3.5 (OAuth-Vervollständigung)
2. **Phase 4** — Storage-SPI + FileField/ImageField + S3-Adapter + Generic Notifier-Protocol
3. **Phase 5** — Audit-UI, Permission-Matrix-UI, Form-/List-View-UI-Tiefe, Admin-Pages, Import/Export-Ausbau
4. **Phase 6** — SCIM/SAML (wenn Enterprise-Bedarf besteht)
5. **Phase 7** — Restparken (Billing, White Labeling, Multi-Region, Flutter UI)

Bewusst nicht mehr in der Roadmap: EventBus, JobQueue, Webhooks,
Observability-Extension, Jobs UI, Workflows — siehe „Bewusst gestrichen"
oben.
