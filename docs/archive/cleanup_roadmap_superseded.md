# asterion Cleanup-Roadmap

> **SUPERSEDED / ARCHIVIERT (2026-06-15).**
> Dieses Dokument schlug vor, das Extension-System abzubauen. Die
> verbindliche Entscheidung ist das Gegenteil: Das Extension-System
> bleibt Kern. Maßgeblich ist `docs/review-hardening-roadmap.md`. Dieses Dokument wird
> nur historisch aufbewahrt und ist **keine** Arbeitsgrundlage.
> (Zusätzlich ist es durch ein fehlerhaftes Such-/Ersetzen an mehreren
> Stellen technisch beschädigt.)

Stand: 2026-05-30  
Repo: `https://github.com/ArnWac/asterion-admin`

## Zielbild

`asterion` soll kein breit aufgeblähtes Admin-/SaaS-Framework werden, sondern ein stabiles, contract-driven FastAPI Admin-Package.

**Gesetzte Kernfeatures:**

- Declarative `ModelAdmin`
- Registry-basierte CRUD-Routen
- Contract API als UI-Wahrheit
- Built-in UI als minimaler Referenz-Client
- Permission-Key Authorization
- Multi-Tenancy über PostgreSQL Schema-per-Tenant
- Audit Logging
- 2FA
- Impersonation
- CLI für DB, Tenant, Permissions, Superadmin, Diagnose
- Provider-System für Auth/User/Tenant/Permissions

**Nicht mehr Teil des Produkts:**

- Jobs
- Workflows
- Webhooks
- Observability als Admin-Feature
- optionale Featurelinien außerhalb des Core-Scopes

Die harte Konsequenz: Jobs, Workflows, Webhooks und Observability dürfen weder als Produktfeature noch als Roadmap-Phantom in Public API, Doku, Tests oder README verbleiben. Das Extension-Modul selbst bleibt Core, aber nur als kontrollierter interner Erweiterungsmechanismus für Admin-UI/Contract/Navigation/Permissions — nicht als offene Plattform für beliebige Produktfeatures.

---

## Kritische Leitentscheidung

Die wichtigste Entscheidung ist nicht 2FA oder Impersonation, sondern die API-Grenze:

```python
app = create_admin(
    config=CoreAdminConfig(...),
    register=register_admins,
    auth_provider=...,
    user_provider=...,
    tenant_provider=...,
    permission_provider=...,
)
```

Das ist die aktuelle Kern-API-Wahrheit.

Nicht zusätzlich:

```python
extensions=[...]
```

Nicht zusätzlich:

```python
workflows=...
jobs=...
observability=...
webhooks=...
```

Kein halbfertiges Plugin-System für entfernte Produktlinien.

**Begründung:**  
Ein Admin-Package braucht zuerst einen stabilen Kern. Ein Extension-System erzeugt eine zweite Produktlinie: Lifecycle, Contribution-Registries, Dependency-Reihenfolge, Migrations, Routing-Priorität, Doku, Tests und Support. Das ist für den aktuellen Kernscope zu viel und steht im Widerspruch zur Entscheidung, Jobs/Workflows/Webhooks/Observability nicht umzusetzen.

---

## Phase 0 — Repo-Zustand einfrieren und Zielscope dokumentieren

### Ziel

Vor dem Refactor muss klar sein, was absichtlich bleibt und was absichtlich entfernt wird. Sonst löschst du Symptome, aber nicht die Ursache.

### Aufgaben

- [ ] Neues Dokument anlegen: `docs/core-scope.md`
- [ ] Darin festhalten:
  - [ ] Core Features
  - [ ] Security Features
  - [ ] explizit ausgeschlossene Features
  - [ ] Public API Boundary
  - [ ] Obsolete/Removed Concepts
- [ ] README anpassen:
  - [ ] Keine Erwähnung von Jobs
  - [ ] Keine Erwähnung von Workflows
  - [ ] Keine Erwähnung von Webhooks
  - [ ] Keine Erwähnung von Observability als Feature
  - [ ] Keine Bewerbung eines allgemeinen Extension-Systems
- [ ] `docs/architecture.md` auf Ist-Zielstand bringen:
  - [ ] Provider-System: ja
  - [ ] Core-Extension-Lifecycle: nein, falls nicht für den Kernscope produktiv benötigt
  - [ ] Contribution-Registries nur behalten, wenn sie für Built-in UI/Contract wirklich genutzt werden

### Akzeptanzkriterien

- [ ] Ein neuer Entwickler kann nach README + `docs/core-scope.md` eindeutig sagen, was zum Kern gehört.
- [ ] Die Doku beschreibt nicht mehr Features, die absichtlich nicht kommen.
- [ ] Keine "Roadmap"-Kommentare im Code suggerieren entfernte Feature-Linien.

---

## Phase 1 — Public API bereinigen

### Ziel

Die importierbare API darf keine falschen Erwartungen erzeugen.

### Aufgaben

- [ ] `asterion/__init__.py` prüfen:
  - [ ] Nur stabile Kern-Objekte exportieren
  - [ ] Keine Feature-Extension-Klassen für entfernte Produktlinien exportieren
  - [ ] Keine Jobs/Workflow/Webhook/Observability-Typen exportieren
- [ ] `create_admin()` Signatur bereinigen:
  - [ ] `extensions` nur behalten, wenn es das Core-Extension-Modul für interne Contributions ist
  - [ ] Core-Extension-Lifecycle-Aufrufe entfernen
  - [ ] `storage` nur behalten, wenn FileField/Upload Kernscope ist
  - [ ] `password_reset_notifier` nur behalten, wenn Password Reset Kernscope ist
- [ ] Falls `storage` bleibt:
  - [ ] klar als Core-SPI behandeln oder bewusst über das Core-Extension-Modul anbinden
- [ ] Falls `password_reset_notifier` bleibt:
  - [ ] klar als Auth-SPI behandeln, nicht als allgemeines Feature-Plugin

### Empfohlene finale Signatur

```python
def create_admin(
    config: CoreAdminConfig | None = None,
    *,
    register: Callable[[AdminRegistry], None] | None = None,
    auth_provider: AuthProvider | None = None,
    user_provider: UserProvider | None = None,
    permission_provider: PermissionProvider | None = None,
    tenant_provider: TenantProvider | None = None,
    password_reset_notifier: PasswordResetNotifier | None = None,
    storage: StorageBackend
AdminExtension
ExtensionContext
ExtensionRegistry
run_setup_phase
compose_lifespan
register_models
register_routes
register_permissions
register_navigation
register_contract_contributions | None = None,
    **fastapi_kwargs,
) -> FastAPI:
    ...
```

### Kritischer Punkt

`storage` und `password_reset_notifier` sind akzeptabel, wenn sie als gezielte SPIs verstanden werden. Sie dürfen aber nicht als Tür zurück zu einem allgemeinen Extension-System dienen.

### Akzeptanzkriterien

- [ ] `from asterion import ...` enthält nur Kern-stabile API.
- [ ] `create_admin()` enthält keine offene Feature-Extension-API für entfernte Produktlinien.
- [ ] Keine Lifecycle-Imports für Jobs/Workflows/Webhooks/Observability in `core/app_factory.py`.
- [ ] Tests schlagen fehl, wenn entfernte öffentliche API weiter importierbar ist.

---

## Phase 2 — Core-Extension-Modul bereinigen und begrenzen

### Ziel

Wenn Jobs/Workflows/Webhooks/Observability gelöscht sind, darf das Extension-Modul nicht als Phantomarchitektur für diese Featurelinien übrig bleiben. Es soll Core bleiben, aber eng begrenzt: Contributions für Contract, Navigation, Permissions, ggf. Routes/Models, wenn diese wirklich vom Admin-Core benötigt werden.

### Aufgaben

- [ ] Ordner `asterion/extensions/` prüfen:
  - [ ] Als Core-Modul behalten
  - [ ] Jobs/Workflows/Webhooks/Observability-Reste vollständig entfernen
  - [ ] Nur generische, admin-relevante Contribution-Mechanik behalten
  - [ ] Keine Produktfeature-Extensions im Core-Ordner verstecken
- [ ] Behalten oder neu zuschneiden:
  - [ ] `AdminExtension`, falls es die stabile Core-Schnittstelle für interne/adminrelevante Contributions ist
  - [ ] `ExtensionContext`, falls es nur Runtime/Registry/Config kapselt
  - [ ] `run_setup_phase`, falls es deterministisch und getestet ist
  - [ ] `compose_lifespan`, falls Extensions wirklich Lifespan-Beiträge liefern dürfen
  - [ ] `ExtensionRegistry`, falls es keine Feature-Roadmap simuliert
- [ ] Entfernen:
  - [ ] `extension_models`, falls nur Altlast aus entfernten Featurelinien
  - [ ] alle Jobs/Workflow/Webhook/Observability-Contributions
  - [ ] alle Hooks, die aktuell keine klare Core-Nutzung haben
- [ ] Falls `ContractContributionRegistry` noch gebraucht wird:
  - [ ] Umbenennen in neutraleren Namen, z. B. `ContractRegistry`
  - [ ] Kein falsches Extension-Wording für entfernte Featurelinien
- [ ] Falls `NavigationRegistry` noch gebraucht wird:
  - [ ] Als UI-Core-Konzept behalten
  - [ ] Kein falsches Extension-Wording für entfernte Featurelinien

### Was nicht passieren darf

Nicht einfach nur Featureordner löschen und die generischen APIs unkontrolliert stehen lassen. Das erzeugt später wieder Feature-Drift.

### Akzeptanzkriterien

- [ ] Suche nach Jobs/Workflows/Webhooks/Observability ergibt keine produktiven Treffer mehr.
- [ ] Doku beschreibt den Core-Extension-Lifecycle nur als Core-Mechanismus, nicht als offene Plattform für entfernte Featurelinien.
- [ ] Tests erwarten nur klar definierte Core-Extension-Contributions.
- [ ] Keine Migrations oder Models gehören zu entfernten Featurelinien.

---

## Phase 3 — Runtime-State verschlanken

### Ziel

`AdminRuntime` soll nur das enthalten, was der Request-Lifecycle wirklich braucht.

### Aktuelles Risiko

Wenn Runtime noch Felder für Feature-Extension-Reste, entfernte Feature-Models oder globale Protected-Field-Singletons enthält, bleibt die alte Architektur indirekt bestehen.

### Zielstruktur

```python
@dataclass(slots=True)
class AdminRuntime:
    config: CoreAdminConfig
    db: DatabaseManager
    registry: AdminRegistry
    providers: ProviderSet
    fields: FieldRegistry
    protected_fields: ProtectedFieldRegistry
    permission_registry: PermissionRegistry
    contract_registry: ContractRegistry
    navigation: NavigationRegistry
    password_reset_notifier: PasswordResetNotifier | None = None
    storage: StorageBackend
AdminExtension
ExtensionContext
ExtensionRegistry
run_setup_phase
compose_lifespan
register_models
register_routes
register_permissions
register_navigation
register_contract_contributions | None = None
```

### Aufgaben

- [ ] `AdminRuntime` prüfen und entfernen:
  - [ ] Feature-Extension-Reste aus Jobs/Workflows/Webhooks/Observability
  - [ ] `extension_models`, falls nicht generisch benötigt
  - [ ] Extension-Kommentare, die entfernte Featurelinien andeuten
  - [ ] Freeze-/Setup-Mechanik, falls sie nicht wirklich für Core-Extensions gebraucht wird
- [ ] `protected_fields` runtime-lokal machen:
  - [ ] Kein module-level Singleton für Security-relevante Registry
  - [ ] Default-Factory statt globalem `get_registry()`
- [ ] Kommentare bereinigen:
  - [ ] Keine Roadmap-Kommentare wie `Roadmap P4`
  - [ ] Keine alten Phase-Kommentare
  - [ ] Keine Hinweise auf entfernte Feature-Extension-Architektur

### Akzeptanzkriterien

- [ ] `AdminRuntime` ist vollständig aus `create_admin()` erklärbar.
- [ ] Runtime enthält keine entfernten Feature-Linien.
- [ ] Mehrere Admin-App-Instanzen im gleichen Prozess teilen keine Security-Registry versehentlich.
- [ ] Tests decken zwei parallele App-Instanzen mit getrenntem Runtime-State ab.

---

## Phase 4 — Auth, AdminContext und Provider-System vereinheitlichen

### Ziel

Es darf nur eine Request-Wahrheit geben: `AdminContext`.

### Architekturregel

Router, CRUD-Services, Contract-Builder und UI dürfen nicht direkt auf konkrete User-Models, JWT-Claims oder Framework-Models zugreifen.

Schlecht:

```python
user = await get_current_user(...)
```

Besser:

```python
ctx = await require_admin_context(...)
user = ctx.user
tenant = ctx.tenant
permissions = ctx.permissions
```

### Aufgaben

- [ ] Alle Router durchsuchen:
  - [ ] `get_current_user`
  - [ ] `require_superadmin`
  - [ ] direkte JWT-Decode-Nutzung
  - [ ] direkte Imports konkreter User-Models
- [ ] Alle Admin-relevanten Routen auf `AdminContext` umstellen:
  - [ ] CRUD
  - [ ] Contract
  - [ ] Actions
  - [ ] Root Users
  - [ ] Root Tenants
  - [ ] Impersonation
  - [ ] 2FA
  - [ ] UI-API-Endpunkte
- [ ] Auth intern halten:
  - [ ] JWT-Decode bleibt in `BuiltinJWTAuthProvider`
  - [ ] Password-Hashing bleibt in Auth-Service
  - [ ] Token-Version-Revocation bleibt Auth-intern
- [ ] Provider-Interfaces schärfen:
  - [ ] `AuthProvider`: authentifiziert Request und liefert Principal
  - [ ] `UserProvider`: lädt User-Objekt/Profil
  - [ ] `TenantProvider`: resolved Tenant
  - [ ] `PermissionProvider`: lädt effektive Permission Keys

### Akzeptanzkriterien

- [ ] Kein Admin-Router hängt direkt am konkreten User-Modell.
- [ ] Alle Permission-Checks nutzen `AdminContext`.
- [ ] Externe Auth kann eingebunden werden, ohne CRUD/Contract/Root-Router zu ändern.
- [ ] Tests decken Builtin-Auth und Fake-External-Auth ab.

---

## Phase 5 — 2FA sauber integrieren

### Ziel

2FA soll nicht als Sonderfall neben Auth leben, sondern als Teil des Auth-Flows.

### Harte Produktentscheidung

2FA ist gesetztes Kernfeature. Dann braucht es einen sauberen Auth-State-Machine-Ansatz.

### Empfohlener Login-Flow

```text
POST /api/v1/auth/login
  -> wenn 2FA nicht aktiv:
       access_token + refresh/session info
  -> wenn 2FA aktiv:
       challenge_token + required_factor

POST /api/v1/auth/2fa/verify
  -> challenge_token + code
  -> access_token
```

### Aufgaben

- [ ] 2FA-Modell prüfen:
  - [ ] Secret verschlüsselt oder anderweitig geschützt speichern
  - [ ] Backup-Codes nur gehasht speichern
  - [ ] Recovery-/Reset-Prozess definieren
- [ ] Challenge Token einführen:
  - [ ] kurzer TTL
  - [ ] anderer Token-Typ als Access Token
  - [ ] nicht für Admin-Routen gültig
- [ ] Rate Limiting für 2FA:
  - [ ] pro User
  - [ ] pro IP
  - [ ] pro Challenge
- [ ] Audit Events:
  - [ ] `2fa_enabled`
  - [ ] `2fa_disabled`
  - [ ] `2fa_challenge_created`
  - [ ] `2fa_success`
  - [ ] `2fa_failed`
  - [ ] `2fa_backup_code_used`
- [ ] Contract/UI:
  - [ ] `/auth/me` zeigt nur `two_factor_enabled`
  - [ ] niemals Secret oder Backup-Code-Hashes ausgeben
- [ ] CLI:
  - [ ] Optional: `asterion user disable-2fa <email>` für Superadmin/Recovery

### Was nicht passieren darf

- Kein Access Token, bevor 2FA erfolgreich abgeschlossen ist.
- Kein normaler JWT mit `requires_2fa=true`, der versehentlich Admin-Routen passieren kann.
- Keine Backup-Codes im Klartext nach der initialen Anzeige.
- Kein 2FA-Secret in API Responses, Logs oder Audit Payloads.

### Akzeptanzkriterien

- [ ] Login mit aktivem 2FA gibt keinen Access Token zurück.
- [ ] Challenge Token kann keine Admin-Route aufrufen.
- [ ] Falsche Codes werden rate-limited.
- [ ] Backup-Codes funktionieren einmalig.
- [ ] Secret/Backup-Codes erscheinen nie in Responses, Logs oder Audit.

---

## Phase 6 — Impersonation sauber integrieren

### Ziel

Impersonation ist mächtig und gefährlich. Es darf nicht wie ein normaler Login aussehen.

### Prinzipien

- Impersonation ist nur für Superadmins erlaubt.
- Impersonation muss auditierbar sein.
- Impersonation muss im Token eindeutig sichtbar sein.
- Impersonation darf Superadmin-Rechte nicht in Tenant-Kontext hineinleaken.
- Impersonation darf 2FA nicht umgehen.

### Empfohlenes Token-Modell

Ein impersonation access token enthält mindestens:

```json
{
  "sub": "target_user_id",
  "act": {
    "sub": "superadmin_user_id",
    "type": "impersonation"
  },
  "tenant": "target_tenant_slug",
  "imp": true,
  "type": "access",
  "jti": "...",
  "exp": "..."
}
```

### Aufgaben

- [ ] Route definieren:
  - [ ] `POST /api/v1/root/impersonate`
  - [ ] `POST /api/v1/root/impersonate/stop` optional, falls UI Session-Wechsel braucht
- [ ] Preconditions:
  - [ ] Actor ist Superadmin
  - [ ] Actor hat 2FA erfolgreich abgeschlossen, falls 2FA für Superadmins erforderlich ist
  - [ ] Target User existiert
  - [ ] Target Tenant existiert oder ist eindeutig
  - [ ] Target User ist Mitglied im Target Tenant
- [ ] Token-Regeln:
  - [ ] kurze Laufzeit
  - [ ] keine Refresh Tokens für Impersonation
  - [ ] `act` Claim verpflichtend
  - [ ] `imp=true` verpflichtend
  - [ ] eigener Token-Type oder klarer Claim
- [ ] Permission-Regeln:
  - [ ] effektive Permissions entsprechen Target User, nicht Actor
  - [ ] Root/Superadmin-Routen sind während Impersonation blockiert
  - [ ] besonders gefährliche Aktionen optional blockieren
- [ ] UI-Regeln:
  - [ ] sichtbarer Banner "Impersonating ..."
  - [ ] klare Stop-Impersonation-Aktion
  - [ ] keine stille Weiterleitung ohne Anzeige
- [ ] Audit Events:
  - [ ] `impersonation_started`
  - [ ] `impersonation_stopped`
  - [ ] `impersonated_action`
  - [ ] Audit enthält Actor und Subject

### Was nicht passieren darf

- Kein Impersonation-Token mit Superadmin-Rechten.
- Kein Refresh für Impersonation.
- Kein Impersonation ohne Audit.
- Kein Root-Zugriff während Impersonation.
- Kein Verbergen im UI.

### Akzeptanzkriterien

- [ ] Superadmin kann Target User impersonieren.
- [ ] Normaler User kann niemals impersonieren.
- [ ] Impersonated Requests nutzen Target Permissions.
- [ ] Root Routes lehnen Impersonation Tokens ab.
- [ ] Audit zeigt Actor und Target eindeutig.
- [ ] UI zeigt Impersonation-Zustand sichtbar an.

---

## Phase 7 — Permission-System entdoppeln

### Ziel

Es darf nicht mehrere konkurrierende Permission-Wahrheiten geben.

### Empfohlene Begriffe

```text
PermissionDefinition
    statische Definition: "admin.posts.list"

PermissionCatalog
    persistierte DB-Sicht auf bekannte Permissions

PermissionProvider
    request-time Loader effektiver User-Permissions

PermissionMatcher
    reine Funktion für Wildcards und Checks
```

### Aufgaben

- [ ] Code durchsuchen nach:
  - [ ] `PermissionRegistry`
  - [ ] `PermissionCatalog`
  - [ ] `PermissionProvider`
  - [ ] `has_permission`
  - [ ] Wildcard-Matcher
- [ ] Verantwortlichkeiten trennen:
  - [ ] Registry erzeugt Definitionen aus ModelAdmin/Root/System
  - [ ] Catalog synchronisiert Definitionen in DB
  - [ ] Provider lädt effektive Keys für Request
  - [ ] Matcher entscheidet ja/nein
- [ ] Keine Permission-Checks in UI/Contract verstecken:
  - [ ] Contract darf Permissions anzeigen
  - [ ] API muss Permissions erzwingen
- [ ] Root Permissions gesondert behandeln:
  - [ ] Superadmin ist kein Wildcard-Normaluser im Tenant
  - [ ] Root-Rechte bleiben global
  - [ ] Tenant-Rechte bleiben tenant-lokal

### Akzeptanzkriterien

- [ ] Es gibt genau einen Weg, effektive Permissions für einen Request zu bekommen.
- [ ] CRUD/Contract/Actions nutzen denselben Permission-Mechanismus.
- [ ] Root und Tenant Permissions sind sauber getrennt.
- [ ] Tests decken Wildcards, fehlende Permissions, Superadmin und Impersonation ab.

---

## Phase 8 — Contract API als einzige UI-Wahrheit stabilisieren

### Ziel

Die Built-in UI darf keine Sonderlogik besitzen, die externe UIs nicht über die API bekommen.

### Aufgaben

- [ ] Contract Response prüfen:
  - [ ] Ressourcen
  - [ ] Felder
  - [ ] Typen
  - [ ] Readonly/Writable
  - [ ] Protected/Secret Field Verhalten
  - [ ] Actions
  - [ ] Permissions
  - [ ] 2FA/Auth-State, falls UI relevant
  - [ ] Impersonation-State, falls UI relevant
- [ ] Built-in UI prüfen:
  - [ ] keine hardcodierten Resource-Sonderfälle
  - [ ] keine hart codierten Permissions
  - [ ] keine versteckten API-Annahmen
- [ ] Contract-Versionierung einführen:
  - [ ] z. B. `contract_version: "1.0"`
  - [ ] Breaking Changes bewusst machen
- [ ] Snapshot-Tests für Contract einführen:
  - [ ] Minimal Model
  - [ ] Protected Fields
  - [ ] Readonly Fields
  - [ ] Relation Fields
  - [ ] Actions
  - [ ] Permissions
  - [ ] Tenant Context

### Akzeptanzkriterien

- [ ] Eine externe UI kann dieselben Informationen nutzen wie die Built-in UI.
- [ ] Built-in UI funktioniert als Contract Consumer.
- [ ] Contract Snapshot-Tests verhindern unbewusste Breaking Changes.
- [ ] Protected Fields werden nie im Contract als lesbar/writable geleakt.

---

## Phase 9 — Storage und FileField kritisch entscheiden

### Ziel

Storage darf nicht als verkapptes Extension-System zurückkommen.

### Entscheidung A — FileField gehört zum Kernscope

Dann gilt:

- `StorageBackend
AdminExtension
ExtensionContext
ExtensionRegistry
run_setup_phase
compose_lifespan
register_models
register_routes
register_permissions
register_navigation
register_contract_contributions` ist Core-SPI.
- `LocalFileStorage` ist Built-in.
- S3 gehört nicht in den aktuellen Kernscope.
- Keine Extension-Begriffe.
- File Uploads haben eigene Security-Regeln.

### Entscheidung B — FileField gehört nicht zum Kernscope

Dann gilt:

- `storage` aus `create_admin()` entfernen.
- `asterion/storage` entfernen oder nach `obsolete/` verschieben.
- FileField-Doku entfernen.
- Tests entfernen oder parken.

### Empfehlung

Für Kernscope nur behalten, wenn CRUD/Contract ohne FileField sonst unfertig wirkt. Ansonsten parken. Der Admin-Core ist schon komplex genug.

### Akzeptanzkriterien bei Entscheidung A

- [ ] FileField hat klare Max-Size-Regeln.
- [ ] MIME-Type/Extension-Regeln sind definiert.
- [ ] Dateien werden nicht über unsichere Pfade gespeichert.
- [ ] StorageBackend
AdminExtension
ExtensionContext
ExtensionRegistry
run_setup_phase
compose_lifespan
register_models
register_routes
register_permissions
register_navigation
register_contract_contributions ist minimal und stabil.
- [ ] Kein S3 im aktuellen Kernscope.

---

## Phase 10 — Tests bereinigen und neu ausrichten

### Ziel

Tests sollen nicht alten Feature-Scope konservieren.

### Aufgaben

- [ ] Tests löschen oder verschieben für:
  - [ ] Jobs
  - [ ] Workflows
  - [ ] Webhooks
  - [ ] Observability
  - [ ] Core-Extension-Lifecycle
- [ ] Tests hinzufügen/verschärfen für:
  - [ ] `create_admin()` Public API
  - [ ] Runtime-Isolation
  - [ ] Provider-Override
  - [ ] AdminContext als einzige Request-Wahrheit
  - [ ] 2FA Flow
  - [ ] Impersonation Flow
  - [ ] Permission Checks
  - [ ] Protected Field Redaction
  - [ ] Contract Snapshots
  - [ ] Multi-Tenant Isolation
- [ ] Marker sauber halten:
  - [ ] Unit Tests ohne PostgreSQL
  - [ ] PostgreSQL Tests separat markiert
  - [ ] Security Tests separat gruppiert

### Empfohlene Teststruktur

```text
tests/
  unit/
    test_config.py
    test_registry.py
    test_contract_builder.py
    test_permissions.py
    test_protected_fields.py

  integration/
    test_create_admin.py
    test_crud_routes.py
    test_auth_flow.py
    test_2fa_flow.py
    test_impersonation_flow.py
    test_admin_context.py

  postgres/
    test_schema_tenancy.py
    test_permission_provider_postgres.py
    test_tenant_bootstrap.py

  security/
    test_no_secret_leaks.py
    test_impersonation_boundaries.py
    test_2fa_boundaries.py
```

### Akzeptanzkriterien

- [ ] Keine Tests importieren entfernte Featuremodule.
- [ ] Security-kritische Features haben Boundary-Tests.
- [ ] PostgreSQL-Isolation wird real getestet.
- [ ] SQLite wird nur als Unit-/Dev-Fallback behandelt.

---

## Phase 11 — Doku, README und DX auf Kernscope trimmen

### Ziel

Die Außendarstellung soll kleiner, glaubwürdiger und nutzbarer werden.

### README-Struktur

```text
# asterion

## What it is
Contract-driven FastAPI admin framework for SQLAlchemy.

## What it is not
No jobs, no workflows, no webhooks, no observability platform.

## Install

## Quickstart

## Core Concepts
- ModelAdmin
- Registry
- Contract API
- AdminContext
- Providers
- Permissions
- Tenancy

## Security Features
- 2FA
- Impersonation
- Audit
- Protected Fields

## CLI

## Development
```

### Aufgaben

- [ ] README reduzieren
- [ ] Architecture-Doku aktualisieren
- [ ] Security-Doku erweitern:
  - [ ] 2FA
  - [ ] Impersonation
  - [ ] Audit
  - [ ] Protected Fields
  - [ ] Token Boundaries
- [ ] Deployment-Doku bereinigen:
  - [ ] Observability entfernen
  - [ ] Health/Ready behalten
  - [ ] Logging minimal beschreiben
- [ ] ModelAdmin-Doku aktualisieren
- [ ] Provider-Doku ergänzen
- [ ] Examples prüfen:
  - [ ] kein alter Feature-Extension-Code
  - [ ] kein Jobs/Workflow/Webhook-Bezug
  - [ ] ein Minimal Example
  - [ ] ein Multi-Tenant Example
  - [ ] ein External Auth Example

### Akzeptanzkriterien

- [ ] README verspricht nichts, was nicht implementiert ist.
- [ ] Doku enthält keine entfernten Features.
- [ ] Quickstart funktioniert copy-paste-nah.
- [ ] Examples bilden echte Zielnutzung ab.

---

## Phase 12 — Obsolete statt sofort löschen, aber nur temporär

### Ziel

Wenn du Code nicht endgültig verlieren willst, parke ihn bewusst. Aber `obsolete/` darf kein zweites Produkt werden.

### Regeln für `obsolete/`

- Kein Import aus produktivem Code.
- Nicht in Package Data.
- Nicht in Public API.
- Nicht in Tests, außer ein Test prüft, dass es nicht importiert wird.
- Jede Datei bekommt Header:

```text
OBSOLETE:
This code is intentionally removed from the current core scope.
Do not import from production code.
Deletion target: before stable release.
```

### Aufgaben

- [ ] Entfernte Module entweder löschen oder nach `obsolete/` verschieben
- [ ] `obsolete/README.md` anlegen
- [ ] Löschdatum oder Release-Grenze definieren
- [ ] CI-Test hinzufügen:
  - [ ] Produktionscode darf nicht aus `obsolete` importieren
  - [ ] Package Build enthält `obsolete` nicht

### Akzeptanzkriterien

- [ ] Obsolete-Code beeinflusst Runtime nicht.
- [ ] Obsolete-Code wird nicht veröffentlicht.
- [ ] Vor einem stabilen Release gibt es eine klare Löschentscheidung.

---

## Empfohlene Reihenfolge für die Umsetzung

### Sprint 1 — Scope und API

1. `docs/core-scope.md`
2. README bereinigen
3. `create_admin()` Signatur finalisieren
4. Extension-Parameter und Core-Extension-Lifecycle entfernen
5. Public API bereinigen

### Sprint 2 — Runtime und Context

1. `AdminRuntime` verschlanken
2. Protected Fields runtime-lokal machen
3. `AdminContext` als einzige Router-Wahrheit erzwingen
4. Provider-Interfaces schärfen
5. Permission-System entdoppeln

### Sprint 3 — Security Features

1. 2FA Flow finalisieren
2. 2FA Boundary-Tests
3. Impersonation Token-Modell finalisieren
4. Impersonation Boundary-Tests
5. Audit Events für 2FA und Impersonation

### Sprint 4 — Contract und UI

1. Contract Snapshot-Tests
2. Built-in UI auf Contract Consumer trimmen
3. Impersonation Banner
4. 2FA UI Flow
5. Kein UI-Hardcoding außerhalb Contract

### Sprint 5 — Tests, Docs, Release Hygiene

1. Entfernte Tests löschen
2. Security-Test-Suite strukturieren
3. Examples aufräumen
4. Deployment-Doku bereinigen
5. Package Build prüfen
6. Kernscope.0.0 Release-Kriterien dokumentieren

---

## Suchliste für Claude Code / manuelle Repo-Prüfung

Diese Begriffe sollten nach dem Cleanup verschwunden sein oder nur noch in `obsolete/` bzw. historischen Notizen vorkommen:

```text
jobs
workflow
workflows
webhook
webhooks
observability
metrics
extension_models
```

Diese Begriffe dürfen bleiben, müssen aber bewusst als Core-Konzepte verwendet werden:

```text
ProviderSet
AuthProvider
UserProvider
TenantProvider
PermissionProvider
AdminContext
PermissionRegistry
PermissionCatalog
ProtectedFieldRegistry
NavigationRegistry
ContractRegistry
TwoFactor
Impersonation
Audit
StorageBackend
AdminExtension
ExtensionContext
ExtensionRegistry
run_setup_phase
compose_lifespan
register_models
register_routes
register_permissions
register_navigation
register_contract_contributions
```

---

## Stabilitäts-Gates

Der Kern darf erst als stabil gelten, wenn diese Punkte erfüllt sind:

- [ ] Keine ausgeschlossenen Features in Public API, README, Doku oder Tests.
- [ ] `create_admin()` ist final und dokumentiert.
- [ ] `AdminRuntime` enthält keine Phantom-Featurelinien.
- [ ] Alle Admin-Routen laufen über `AdminContext`.
- [ ] 2FA hat vollständige Boundary-Tests.
- [ ] Impersonation hat vollständige Boundary-Tests.
- [ ] Protected Fields leaken nicht in Responses, Contract, Logs oder Audit.
- [ ] Contract API ist snapshot-getestet.
- [ ] Built-in UI nutzt keine versteckte Sonderlogik.
- [ ] Multi-Tenant Isolation ist mit PostgreSQL getestet.
- [ ] README ist ehrlich: kleiner Scope, funktionierender Quickstart, keine Zukunftsversprechen als Ist-Zustand.

---

## Harte Einschätzung

Der gefährlichste Fehler wäre jetzt, 2FA und Impersonation einfach oben drauf zu setzen, während Extension-Reste, doppelte Permission-Konzepte und uneinheitliche Auth-Pfade im Code bleiben.

Die richtige Reihenfolge ist:

```text
Erst Scope und Architektur säubern.
Dann 2FA und Impersonation als Security-Features sauber integrieren.
Dann Contract/UI stabilisieren.
Dann stabilisieren.
```

2FA und Impersonation sind keine normalen Features. Sie verschärfen die Anforderungen an Auth, Tokens, Audit, Permissions und UI-Transparenz. Wenn der Kern vorher nicht sauber ist, werden sie zu Sicherheitsrisiken statt zu Produktfeatures.
