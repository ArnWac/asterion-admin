# adminfoundry — Roadmap & Hardening (konsolidiert)

Stand: 2026-06-18 · bezieht sich auf `adminfoundry` 0.1.0

Dieses Dokument konsolidiert die frühere `roadmap.md` (Feature-Phasen,
Non-Goals) und `stabilization.md` (Pre-1.0-Härtung) und führt sie mit den
Befunden des externen Code-Reviews vom 2026-06-16 (Runde 1, R1–R12) und der
Folge-Analyse vom 2026-06-18 (Runde 2, R13–R17) zusammen. Es ist die
**einzige maßgebliche Roadmap**.

> Leitregel: **keine neuen Produktfeatures** vor 1.0. Jeder offene Punkt
> schließt eine Sicherheits-, Vertrauens- oder Konsolidierungslücke an
> bestehendem Code. Ein Punkt gilt erst als erledigt, wenn ein **Test** die
> Zusage absichert — bei Isolationsthemen ein Test gegen echtes PostgreSQL
> über den HTTP-Pfad, nicht nur das Primitiv.

**Status der aktiven Arbeit:** Feature-Phasen 1–5 und Härtung P1–P3
abgeschlossen (siehe [Historie](#abgeschlossen-historie)). Runde-1-Härtung
**R1–R12 umgesetzt + gemergt**; R1/R2 (Tenant-Isolation) ist seit dem CI-Lauf
vom 2026-06-18 auf echtem PostgreSQL **bestätigt** (Job `test-postgres` grün).
Runde 2 (**R13–R17**) sammelt die Befunde der Folge-Analyse — R13 erledigt,
R14–R17 offen. Phasen 6/7 bleiben geparkt.

---

## Übersicht — offene Review-Härtung

| Prio | ID | Thema | Status |
|---|---|---|---|
### Runde 1 (Review 2026-06-16)

| Prio | ID | Thema | Status |
|---|---|---|---|
| **P0** | R1 | [`search_path` auf der Request-Session](#p0--r1-search_path-auf-der-request-session) | ✅ erledigt (CI-bestätigt 2026-06-18) |
| **P0** | R2 | [HTTP-PG-Isolationstest](#p0--r2-http-pg-isolationstest) | ✅ erledigt (CI grün) |
| **P0** | R3 | [Doku-Zusagen zur Isolation korrigieren](#p0--r3-doku-zusagen-zur-isolation-korrigieren) | ✅ erledigt |
| **P1** | R4 | [`test-postgres` als Build-Gate](#p1--r4-test-postgres-als-build-gate) | ✅ erledigt |
| **P1** | R5 | [CHANGELOG.md + SECURITY.md](#p1--r5-changelogmd--securitymd) | ✅ erledigt |
| **P1** | R6 | [Coverage messen + CI-Badge](#p1--r6-coverage-messen--ci-badge) | ✅ erledigt |
| **P2** | R7 | [Verteilter Rate-Limiter](#p2--r7-verteilter-rate-limiter) | ✅ erledigt |
| **P2** | R8 | [JWT-Härtung: aud/iss](#p2--r8-jwt-härtung-audiss) | ✅ erledigt |
| **P2** | R9 | [Tenant-Cache-Invalidierung](#p2--r9-tenant-cache-invalidierung) | ✅ erledigt |
| **P3** | R10 | [Release-Workflow + Wheel-Smoke](#p3--r10-release-workflow--wheel-smoke) | ✅ erledigt |
| **P3** | R11 | [JS-Tests ausbauen](#p3--r11-js-tests-ausbauen) | ✅ erledigt |
| **P3** | R12 | [Slug-Normalisierung](#p3--r12-slug-normalisierung) | ✅ erledigt |

### Runde 2 (Analyse 2026-06-18)

| Prio | ID | Thema | Status |
|---|---|---|---|
| **P0** | R13 | [Roten `test-postgres`-Job klären](#p0--r13-roten-test-postgres-job-klären) | ✅ erledigt (Test-Override-Annotation; CI grün) |
| **P1** | R14 | [XSS-Härtung: CSP + Token-Storage](#p1--r14-xss-härtung-csp--token-storage) | 🟡 CSP-Knopf erledigt; Bundled-UI-Nonce + Cookie-Option offen |
| **P2** | R15 | [Login-Enumeration + Default-Limiter-Keying](#p2--r15-login-enumeration--default-limiter-keying) | ✅ erledigt (Konstante-Zeit + opt-in `(email,ip)`-Keying) |
| **P2** | R16 | [Proxy-/Client-IP (CIDR-Allowlist, Audit)](#p2--r16-proxy--client-ip) | ✅ erledigt (`trusted_proxy_count`) |
| **P3** | R17 | [Toten/redundanten Code aufräumen](#p3--r17-totenredundanten-code-aufräumen) | ✅ erledigt (Provider-Session-Merge als Folgeschritt offen) |
| **—** | — | [Bewusst NICHT umgesetzt](#bewusst-nicht-umgesetzt) | entschieden |

---

## P0 — R1: `search_path` auf der Request-Session

**Problem:** Die CRUD-Endpunkte beziehen ihre Session über
`get_async_session` ([db/dependencies.py](../adminfoundry/db/dependencies.py)),
die eine Transaktion öffnet, aber **kein** `SET LOCAL search_path` setzt.
`require_admin_context` → `build_admin_context` → `BuiltinPermissionProvider.get_permissions`
([providers/permissions.py:59](../adminfoundry/providers/permissions.py))
öffnet dafür eine **eigene** Session (`async_sessionmaker(runtime.db.engine)`)
und setzt `search_path` nur dort. Es gibt keinen Engine-`connect`-Hook und
keinen ContextVar, der die Request-Session scoped.

**Risiko (P0):** Auf PostgreSQL laufen alle CRUD-Operationen auf
`TenantModel`-Tabellen (App-Modelle wie `projects`/`tickets`, ebenso die als
ModelAdmin registrierten RBAC-Tabellen) mit `search_path = public`.
Wahrscheinlichster Effekt: `relation "..." does not exist` (500), da
Tenant-Tabellen nur in `tenant_<slug>` existieren. Worst Case bei einer
gleichnamigen `public`-Tabelle: stiller tenant-übergreifender Zugriff. Auf
SQLite unsichtbar (`schema_translate_map {"public": None}`,
[db/session.py:50](../adminfoundry/db/session.py)) — deshalb trotz Bug grüne
Suite.

**Betroffene Dateien:**
- [adminfoundry/db/dependencies.py](../adminfoundry/db/dependencies.py) (`get_async_session`)
- [adminfoundry/tenancy/schema_strategy.py](../adminfoundry/tenancy/schema_strategy.py) (`set_search_path`, `make_tenant_schema_name`)
- ggf. [adminfoundry/providers/permissions.py](../adminfoundry/providers/permissions.py) (Doppel-Session entfernen)

**Änderung:** In `get_async_session` nach `session.begin()` prüfen, ob
`request.state.tenant` gesetzt ist und der Backend PostgreSQL ist; dann
`await set_search_path(session, make_tenant_schema_name(tenant.slug))`. Damit
hält die Request-Transaktion den Tenant-`search_path` für die gesamte
nachfolgende CRUD-Logik — exakt die Invariante, die die Doku ohnehin
behauptet. Folgeschritt: den separaten Session-Lookup im
`BuiltinPermissionProvider` auf dieselbe Request-Session ziehen (eine
Session pro Request, kein zweiter Pool-Connection-Hop).

**Test (Abnahmekriterium):** siehe R2 — gilt erst als erledigt, wenn der
HTTP-PG-Isolationstest grün ist.

**Status:** ✅ erledigt — auf echtem PostgreSQL per CI bestätigt (s. R13).

---

## P0 — R2: HTTP-PG-Isolationstest

**Problem:** `tests/postgres/` prüft heute nur das `set_search_path`-Primitiv
auf handgebauten Sessions
([test_tenant_isolation.py](../tests/postgres/test_tenant_isolation.py),
[test_search_path_lifecycle.py](../tests/postgres/test_search_path_lifecycle.py)).
Kein Test fährt den realen HTTP-CRUD-Pfad (`TestClient` →
`get_async_session` + `build_admin_context`). Die Doku
([tenancy.md](tenancy.md)) behauptet dennoch, die Tests bewiesen die
Isolation end-to-end.

**Risiko:** Ohne diesen Test bleibt R1 ungesichert und CI kann grün sein,
während die Kerngarantie gebrochen ist.

**Betroffene Dateien:** neuer Test, z. B.
`tests/postgres/test_http_tenant_isolation.py`.

**Änderung / Testfälle** (alle `@pytest.mark.postgres`, zwei Tenants A/B mit
eigenem Schema + je einer tenant-lokalen ModelAdmin):
1. `POST /api/v1/admin/{resource}` als Mitglied von A → `GET` als Mitglied
   von B liefert den Datensatz **nicht** (leere Liste / 404).
2. Gleiche Primärschlüssel/Namen in A und B kollidieren nicht.
3. Nach einer Exception im Handler (Rollback) zeigt ein Folge-Request auf
   derselben Pool-Connection den Default-`search_path` (kein Leak).
4. Parallele Requests verschiedener Tenants (asyncio.gather) vermischen
   keine Daten.
5. Tenant-lokale Action / Bulk-Operation läuft im richtigen Schema.

**Status:** ✅ erledigt — Test grün auf echtem PostgreSQL (CI, s. R13).

---

## P0 — R3: Doku-Zusagen zur Isolation korrigieren

**Problem:** Mehrere Stellen behaupten ein Verhalten, das der Code (vor R1)
nicht erfüllt:
- [docs/tenancy.md:61-62](tenancy.md): „The CRUD session and the
  `BuiltinPermissionProvider` session are the **same object**."
- [docs/tenancy.md:68](tenancy.md): „`tests/postgres/` proves all three
  [invariants] against a real PG instance."
- [examples/multi_tenant/models.py:6](../examples/multi_tenant/models.py):
  „isolation is enforced by `SET LOCAL search_path` applied by
  `TenantMiddleware` on every request" — die Middleware setzt nur
  `request.state.tenant`.

**Risiko:** Eine **falsche Sicherheitszusage** ist gravierender als eine
Lücke — sie verdeckt das Risiko vor Integratoren.

**Betroffene Dateien:** [docs/tenancy.md](tenancy.md),
[docs/architecture.md](architecture.md) (Request-Lifecycle-Diagramm Zeile
~155-161), [examples/multi_tenant/models.py](../examples/multi_tenant/models.py).

**Änderung:** Nach R1 die Aussagen wahr machen (Request-Session setzt
`search_path`); das Beispiel auf „durch `get_async_session`" statt
„`TenantMiddleware`" korrigieren; die Test-Beweis-Zusage erst aufnehmen,
wenn R2 grün ist.

**Status:** ✅ erledigt (zusammen mit R1/R2).

---

## P1 — R4: `test-postgres` als Build-Gate

**Problem:** `build.needs = [lint, typecheck, js, test]`
([.github/workflows/ci.yml:99](../.github/workflows/ci.yml)) — `test-postgres`
ist nicht enthalten. Ein Release kann grün durchlaufen, obwohl die
Isolationstests fehlschlagen oder (wie heute lokal) übersprungen werden.

**Risiko:** Für ein Schema-per-Tenant-Framework ist das die wichtigste
Test-Stufe; sie darf den Build nicht *nicht* blockieren.

**Betroffene Dateien:** [.github/workflows/ci.yml](../.github/workflows/ci.yml).

**Änderung:** `test-postgres` in `build.needs` aufnehmen. Sicherstellen, dass
die Postgres-Service-Stufe die neuen HTTP-Isolationstests (R2) tatsächlich
ausführt (Env-Var `ADMINFOUNDRY_TEST_POSTGRES_URL` ist gesetzt, ci.yml:93).

**Test:** Ein bewusst gebrochener Isolationsfall lässt den `build`-Job rot
werden (manuell verifizieren).

**Status:** ✅ erledigt — `build.needs` enthält `test-postgres`.

---

## P1 — R5: CHANGELOG.md + SECURITY.md

**Problem:** [pyproject.toml:44](../pyproject.toml) verlinkt
`CHANGELOG.md`, die Datei fehlt → toter Metadaten-Link auf PyPI. `SECURITY.md`
(Meldeweg für Schwachstellen) fehlt ebenfalls.

**Risiko:** Niedrig technisch, aber OSS-/Release-Hygiene und Voraussetzung
für das [1.0-Gate](#release--versionspolitik--10-gate) (Changelog-Prozess).

**Betroffene Dateien:** neue `CHANGELOG.md`, `SECURITY.md` (Root).

**Änderung:** `CHANGELOG.md` im Keep-a-Changelog-Format anlegen, 0.1.0 als
ersten Eintrag; die 0.x-Stabilitätszusage aus der
[Release-Politik](#release--versionspolitik--10-gate) referenzieren.
`SECURITY.md` mit Meldeweg + unterstützten Versionen.

**Status:** ✅ erledigt.

---

## P1 — R6: Coverage messen + CI-Badge

**Problem:** Keine Coverage-Messung; README zeigt keinen CI-Status.

**Risiko:** Niedrig; verbessert Sichtbarkeit echter Testlücken (insb. der
Isolations-/Negativpfade).

**Betroffene Dateien:** [.github/workflows/ci.yml](../.github/workflows/ci.yml),
[README.md](../README.md), `pyproject.toml` (pytest-cov in `dev`).

**Änderung:** `pytest --cov=adminfoundry` im `test`-Job, Coverage-Report als
Artefakt; CI-Status-Badge im README. Optional Schwellwert ohne Hard-Fail
(Reporting, kein Gate).

**Status:** ✅ erledigt.

---

## P2 — R7: Verteilter Rate-Limiter

**Problem:** `InMemoryLoginRateLimiter`
([auth/rate_limiter.py:37](../adminfoundry/auth/rate_limiter.py)) ist
pro-Prozess, verliert Zähler bei Neustart und skaliert nicht über Worker.
Schlüssel ist nur die lowercase-E-Mail → kein IP-Bezug (kein Schutz gegen
Password-Spraying über viele Konten; Victim-Lockout möglich). **Sauber
dokumentiert und über `RateLimiterBackend`-Protocol austauschbar.**

**Risiko:** Mittel — Design ist vorbereitet, der Default reicht nur für
Single-Worker/MVP.

**Betroffene Dateien:** neues Backend als Extension (analog `storage_s3`):
`adminfoundry/extensions/rate_limit_redis/` hinter dem vorhandenen Protocol;
Verdrahtung über `create_admin(login_rate_limiter=...)` + Runtime-Feld; ggf.
Keying-Strategie `(email, ip)`.

**Änderung:** Optionales Redis-Backend als Extra `rate-limit-redis`
bereitgestellt (analog `storage-s3`, eigener `extensions/`-Ordner); Default
unverändert. `RedisLoginRateLimiter` ist gegen jeden async Redis-Client
duck-typed (kein harter `redis`-Import im Core). IP-/Tupel-Keying bleibt offen.

**Test:** Protocol-Conformance-Test gegen `fakeredis`
(`tests/extensions/rate_limit_redis/test_backend.py`); Fenster-/Reset-/
Clear-Verhalten.

**Status:** ✅ erledigt.

---

## P2 — R8: JWT-Härtung: aud/iss

**Problem:** `decode_token`
([auth/tokens.py:218-225](../adminfoundry/auth/tokens.py)) validiert weder
`aud` noch `iss`; die Tokens setzen diese Claims nicht. `alg` ist gepinnt
(`algorithms=[algorithm]`), `exp` prüft jose by default, `type` wird je
Decoder erzwungen.

**Risiko:** Niedrig im symmetrischen Ein-Secret-Setup; relevant, sobald
Tokens über mehrere Dienste/Audiences geteilt werden.

**Betroffene Dateien:** [auth/tokens.py](../adminfoundry/auth/tokens.py),
[core/config.py](../adminfoundry/core/config.py) (konfigurierbare
`issuer`/`audience`).

**Änderung:** `iss`/`aud` beim Erzeugen setzen und in `decode_token`
(`jwt.decode(..., audience=, issuer=)`) validieren, sobald konfiguriert;
abwärtskompatibel optional halten.

**Test:** Token mit falschem `aud`/`iss` wird abgelehnt; ohne Konfiguration
unverändertes Verhalten.

**Status:** ✅ erledigt.

---

## P2 — R9: Tenant-Cache-Invalidierung

**Problem:** `_TENANT_TTL = 30` In-Memory-Cache pro Prozess
([tenancy/resolver.py:14](../adminfoundry/tenancy/resolver.py)). Ein
deaktivierter/gelöschter Tenant oder geänderte `allowed_cidrs` wird bis zu
30 s aus dem Cache weiterbedient (Middleware liest `is_active`/CIDR aus dem
`TenantContext`).

**Risiko:** Mittel bei „Tenant sofort sperren"-Anforderungen.

**Betroffene Dateien:** [tenancy/resolver.py](../adminfoundry/tenancy/resolver.py).

**Änderung:** Cache-Bust beim Tenant-Statuswechsel (im Root-/CLI-Pfad
`clear_tenant_cache()` aufrufen) oder den status-/CIDR-sensitiven Teil nicht
cachen (nur die Slug→Schema-Auflösung cachen). TTL konfigurierbar machen.

**Test:** Nach Deaktivierung eines Tenants liefert der nächste Request 403
(kein 30-s-Fenster).

**Status:** ✅ erledigt (TTL konfigurierbar + `invalidate_tenant`).

---

## P3 — R10: Release-Workflow + Wheel-Smoke

**Problem:** Keine Release-Tags (nur `archive/pre-v1-core-rebuild`), kein
Publish-Job. Build + `twine check` laufen lokal/CI grün, aber das erzeugte
Wheel wird nicht in einer frischen Umgebung getestet.

**Risiko:** Niedrig vor 1.0; nötig für vertrauenswürdige Veröffentlichung.

**Betroffene Dateien:** neuer Workflow `.github/workflows/release.yml`.

**Änderung:** Tag-getriggerter Release-Job mit Trusted Publishing
(PyPI/TestPyPI); Smoke-Schritt: Wheel in frischer venv installieren,
`python -c "import adminfoundry"` + `adminfoundry --help`.

**Status:** ✅ erledigt (`.github/workflows/release.yml`).

---

## P3 — R11: JS-Tests ausbauen

**Problem:** Genau eine JS-Testdatei
([tests/js/logic.test.js](../tests/js/logic.test.js), 14 Fälle) für die als
SPA-Shell beworbene UI; reines DOM-Mounting nur per Python-Smoke gedeckt
(bewusste Entscheidung aus der Härtung P1).

**Risiko:** Niedrig.

**Änderung:** jsdom-Tests ergänzt für `api.js` (`tokenStore`-localStorage-
Round-Trip, `APIError`-Envelope-Parsing); `jsdom` als JS-Dev-Dependency. Die
Pure-Logic-Tests bleiben per Datei-Direktive auf dem node-Environment.

**Status:** ✅ erledigt (`tests/js/api.test.js`).

---

## P3 — R12: Slug-Normalisierung

**Problem:** Slug-Auflösung ist case-sensitiv/exakt
([tenancy/resolver.py:65](../adminfoundry/tenancy/resolver.py)), Subdomain-
Strategie nimmt `parts[0]` ungetrimmt. Kein Case-/Unicode-Folding.

**Risiko:** Niedrig — Korrektheits-, kein Sicherheitsthema (kein Bypass, nur
Treffer/Fehltreffer).

**Änderung:** Slug vor Lookup normalisieren (lowercase/trim) und beim
Tenant-Anlegen dieselbe Normalisierung erzwingen.

**Status:** ✅ erledigt (`validate_tenant_slug` + `_extract_slug`).

---

# Runde 2 — Folge-Analyse (2026-06-18)

Befunde aus der Anwendung der 5 Analyse-Achsen auf den Stand nach R1–R12.

## P0 — R13: Roten `test-postgres`-Job klären

**Problem:** Der `test-postgres`-CI-Job für den letzten `main`-Push
(`aea91f4`) ist **rot** — Step `Run postgres-marked tests` (`pytest -m
postgres`). Damit ist die Isolation aus R1 **auf echtem PostgreSQL nicht
bewiesen**; lokal lief der HTTP-Isolationstest (R2) nur als Skip (keine DB).
Verdächtig ist primär der neue `test_http_tenant_isolation.py`, der lokal nie
ausgeführt werden konnte.

**Risiko:** **Kritisch/Blocker** — solange ungeklärt, ist der Kern-Claim
(Tenant-Isolation) offen; im schlimmsten Fall ein realer Fehler in R1/R2 auf
PG.

**Betroffene Dateien:** [tests/postgres/test_http_tenant_isolation.py](../tests/postgres/test_http_tenant_isolation.py),
[db/dependencies.py](../adminfoundry/db/dependencies.py),
[.github/workflows/ci.yml](../.github/workflows/ci.yml).

**Ursache (gefunden 2026-06-18):** **kein Produktbug.** Der Dependency-Override
im R2-Test hatte `async def _ctx_override(request)` **ohne Typannotation** →
FastAPI behandelte `request` als erforderlichen Query-Parameter → jeder POST
scheiterte mit `422 {"fields":[{"name":"request","message":"Field required"}]}`,
bevor die Isolationslogik (R1) überhaupt lief. Lokal unsichtbar, weil der Test
nur auf PostgreSQL läuft.

**Änderung:** `request: Request` annotiert ([test_http_tenant_isolation.py](../tests/postgres/test_http_tenant_isolation.py)),
damit FastAPI das Request-Objekt injiziert statt es zu validieren.

**Test:** `test-postgres` grün auf `main`.

**Status:** ✅ erledigt — CI-Lauf `65391f8` (2026-06-18) grün, inkl.
`Test (PostgreSQL integration)` + `Build`.

## P1 — R14: XSS-Härtung: CSP + Token-Storage

**Problem:** Kein CSP ([core/middleware.py:109-113](../adminfoundry/core/middleware.py))
und Bearer-Token im `localStorage` ([ui/static/admin/api.js:14](../adminfoundry/ui/static/admin/api.js))
→ eine einzige XSS im Admin-UI = Token-Diebstahl ohne HttpOnly-Schutz.

**Risiko:** Hoch (clientseitig der größte Hebel).

**Änderung:** Opt-in-CSP-Header (konfigurierbar, sobald die UI kompatibel ist;
die statische No-Build-UI ggf. an eine restriktive CSP anpassen) und/oder
Token-Speicherung auf HttpOnly-Cookie umstellen (zieht dann R14 ↔ CSRF nach
sich — vgl. „Bewusst NICHT umgesetzt").

**Test:** Header-Assertion (CSP gesetzt, wenn konfiguriert; sonst abwesend) —
`tests/operations/test_middleware.py`.

**Status:** 🟡 teilweise — konfigurierbarer `content_security_policy`-Header
erledigt (Default aus, API-first kann strikt setzen). **Offen:** Nonce-Härtung
der Inline-Skripte der Bundled-UI bzw. HttpOnly-Cookie-Token-Option.

## P2 — R15: Login-Enumeration + Default-Limiter-Keying

**Problem:** (a) `inactive_user`→403 vs. `invalid_credentials`→401
([auth/router.py](../adminfoundry/auth/router.py)) plus bcrypt-Timing-Short-
Circuit ([providers/auth.py:133](../adminfoundry/providers/auth.py)) erlauben
User-Enumeration. (b) Der Default-Limiter ist per-Worker und nur per E-Mail
gekeyt (kein IP) → schwacher Brute-Force-/Spraying-Schutz im Default.

**Risiko:** Mittel.

**Änderung:** Einheitliche, generische Login-Fehlermeldung (gleicher Status/
Text für unbekannt/falsch/inaktiv); konstante-Zeit-Pfad (Dummy-Hash bei
unbekannter E-Mail); optional `(email, ip)`-Keying. Default-Schwäche zumindest
klar dokumentieren + R7 als Produktionsempfehlung verlinken.

**Test:** unbekannte vs. falsche E-Mail liefern identische Antwort; „inactive"
nur bei korrektem Passwort — `tests/auth/test_login_enumeration.py`.

**Status:** ✅ erledigt — Konstante-Zeit-Pfad (`dummy_verify_password`) +
uniforme 401; opt-in `login_rate_limit_by_ip` keyt auf `(email, ip)` über die
R16-Client-IP (`tests/auth/test_login_limiter_keying.py`).

## P2 — R16: Proxy- / Client-IP

**Problem:** Tenant-CIDR-Allowlist ([tenancy/middleware.py:42](../adminfoundry/tenancy/middleware.py))
und Audit-IP ([audit/service.py:66](../adminfoundry/audit/service.py)) nutzen
`request.client.host` direkt — kein `X-Forwarded-For`. Hinter dem üblichen
Reverse-Proxy ist die CIDR-Allowlist faktisch wirkungslos/umgehbar, Audit-IPs
sind die Proxy-IP.

**Risiko:** Mittel (eine Sicherheitskontrolle, die Mandanten vertrauen).

**Änderung:** Echte Client-IP aus `X-Forwarded-For` ableiten — **nur** bei
konfigurierten Trusted Proxies (z. B. `forwarded_allow_ips` / Anzahl Hops),
nie ungeprüft (sonst Spoofing). Deployment-Doku: `uvicorn --proxy-headers`.

**Test:** `client_ip`-Auflösung für 0/1/2 Trusted Hops + Fallbacks —
`tests/core/test_net.py`.

**Status:** ✅ erledigt — `trusted_proxy_count`-Config + `core/net.client_ip`,
verdrahtet in Tenant-CIDR-Allowlist + Audit-IP (Default 0 = unverändert).

## P3 — R17: Toten/redundanten Code aufräumen

**Problem:**
- `resolve_impersonation_tenant` in `tenancy/resolver.py` war **tot** (keine
  Aufrufer) — **entfernt** (samt ungenutztem `uuid`-Import).
- `clear()`-Inkonsistenz im Login (nutzte den Modul-Default statt des
  injizierten Limiters) — **gefixt** ([auth/router.py](../adminfoundry/auth/router.py)).
- `BuiltinPermissionProvider.get_permissions` öffnet pro Request eine zweite
  Session ([providers/permissions.py:59](../adminfoundry/providers/permissions.py));
  seit R1 ist die Request-Session bereits gescoped → ließe sich
  zusammenführen (zweiter Pool-Hop entfällt). **Bewusst aufgeschoben** —
  würde die `PermissionProvider`-Protocol-Signatur ändern und den gerade
  CI-bestätigten Isolationspfad anfassen; gehört in einen eigenen, getesteten
  Schritt.

**Risiko:** Niedrig (Wartbarkeit; der `clear()`-Bug war ein echter
Korrektheitsfehler mit Redis-Backend).

**Test:** Integrationstest „injizierter Limiter wird auf allen drei Pfaden
(is_limited/record/clear) benutzt" — ergänzt
([tests/auth/test_login_limiter_injection.py](../tests/auth/test_login_limiter_injection.py)).

**Status:** ✅ erledigt — toter Code entfernt, `clear()`-Fix + Test drin.
Provider-Doppel-Session bewusst als separater Folgeschritt offen.

---

## Bewusst NICHT umgesetzt

| Vorschlag (aus Review/Original) | Entscheidung | Begründung |
|---|---|---|
| Dedizierte CSRF-Schicht | abgelehnt | UI nutzt Bearer-Token aus `localStorage` ([ui/static/admin/api.js:14,58](../adminfoundry/ui/static/admin/api.js)), keine Cookie-Ambient-Auth → kein CSRF-Vektor. Falls je eine Cookie-Session kommt, neu bewerten. |
| Separate Impersonation-Session-Tabelle | abgelehnt | Bausteine existieren: `RevokedToken.jti` + `ImpersonationLog.jti` + Token an `target.token_version` gebunden. Einzeln widerrufbar. |
| Zusätzliches JSON-Schema-Dokument | aufgeschoben | Pydantic-`ModelContractMeta` + gepinnter Snapshot + `CONTRACT_VERSION` decken den Bedarf vorerst. |

---

## Offene Follow-ups (kein 1.0-Blocker)

### mypy aufs Gesamtpaket

**Ziel:** mypy von der Vertragsschicht (`providers/` + `core/config.py`) auf
`adminfoundry/` insgesamt ausweiten.

**Befund:** ~80 Fehler, überwiegend Framework-Typing-Nits (`var-annotated`
an SQLAlchemy-Statements, `type[ModelAdmin]`-vs-Instanz in CRUD/Contract-
Signaturen, FastAPI-`lifespan`-Generics). Mehrheitlich echte, aber benigne
Signaturinkonsistenzen.

**Vorgehen wenn aufgegriffen:** kategorienweise (`var-annotated` zuerst —
billig; dann `type[ModelAdmin]` → `ModelAdmin` in den CRUD/Contract-
Signaturen; FastAPI-Quirks gezielt `# type: ignore` mit Begründung), dann
`[tool.mypy] files` aufs Paket erweitern.

**Status:** offen, **kein 1.0-Blocker**.

---

## Release- / Versionspolitik + 1.0-Gate

**Stabilitätszusage (0.x):** Solange `0.x`, kann ein Minor-Release die
Public API oder den Contract brechen — Breaking Changes werden im
Commit/Changelog markiert. Ab `1.0` gilt SemVer:

- **Public API** = die Re-Exports in `adminfoundry/__init__.__all__`
  (`create_admin`, `CoreAdminConfig`, `AdminRegistry`, `ModelAdmin`) plus
  die Provider-Protocols in `adminfoundry/providers/base.py`. Breaking
  Changes daran → **Major**. Gepinnt durch `tests/public_api/`.
- **Contract** = `ModelContractMeta`. Formänderungen ziehen den
  Snapshot-Test (`tests/contract/test_contract_snapshot.py`); eine
  *breaking* Formänderung muss `CONTRACT_VERSION` erhöhen.

**1.0-Gate (Kriterien):**
- [x] Härtung P1 erfüllt (JS-Harness, Contract-Snapshots, mypy-CI auf Vertragsschicht)
- [x] Härtung P2 entschieden + dokumentiert (Field-Visibility-Resolver, User-Entkopplungs-Grenze)
- [x] Doku ehrlich bei „Known limitations"
- [x] **R1–R4 + R13** — tenant-lokales CRUD über den HTTP-Pfad auf echtem
  PostgreSQL **nachweislich** isoliert und im Build gegated. **Bestätigt:**
  CI-Lauf `65391f8` vom 2026-06-18, Job `Test (PostgreSQL integration)` grün
  (Run 27724422671). Der harte 1.0-Blocker ist gefallen.
- [x] R5 — Changelog/Release-Notes-Prozess etabliert (`CHANGELOG.md`)
- [ ] R14 — XSS-Härtung (CSP / Token-Storage) bewertet + adressiert
- [ ] mypy aufs Gesamtpaket grün (Follow-up) — *empfohlen, nicht zwingend*

---

## Geparkt — erst bei konkretem Bedarf, jeweils eigenes Scoping

### Phase 6 — Enterprise-Identity

- **SCIM / SAML** — SCIM-Provisioning, SAML-Login. Hoher Wert für
  Enterprise-Kunden, aber großer, eigenständiger Block; gehört hinter ein
  stabiles Refresh/2FA-Fundament, nicht in die Auth-Hardening-Phase.

### Phase 7 — Post-v1 Enterprise

- Billing / Metering / Usage Seats
- White Labeling
- Multi-Region Tenancy
- Redis Distributed Cache (Rate-Limiter-Protocol existiert; Backend ist Extension — vgl. R7)
- Flutter UI

---

## Non-Goals / Don'ts (durchgängig)

Explizite Nicht-Ziele, aus den Quelldokumenten kondensiert:

- **Keine neuen Produktfeatures** vor Abschluss der Härtung. „Erst
  Robustheit. Dann Feature-Tiefe."
- **Kein `PermissionProvider.can(obj, ...)`** — würde mit `AdminPolicy`
  konkurrieren. PermissionProvider liefert *Keys*, AdminPolicy macht
  *Object/Field*-Entscheidungen.
- **Keine row-level Tenancy** wieder einführen — Isolation kommt aus dem
  PostgreSQL-Schema, nicht aus `tenant_id`-Filtern in Python.
- **Keine neuen globalen Singletons.** Bestehende, bewusst akzeptierte
  Ausnahme: das `protected_fields`-Modul-Singleton (fail-safe — Teilen führt
  nur zu Über-Protektion, kein Leck; per Tripwire-Test in
  `tests/security/test_protected_field_registry.py` festgeschrieben).
- **Kein OIDC/SAML/SCIM** in der Auth-Hardening — gehört nach Phase 6.

### Bewusst gestrichen (waren in früheren Plänen, jetzt raus)

Sechs Items, die nach kritischer Bewertung mehr Komplexität als Wert
gebracht hätten:

- **EventBus / Domain-Event-System** — Audit + Lifecycle-Hooks reichen.
- **JobQueue (Framework-eigen)** — jede Produktiv-App hat schon
  Celery/RQ/arq.
- **Observability / `/metrics` als Extension** — gehört, wenn überhaupt,
  direkt in den Core.
- **Webhooks-Extension** — Apps nutzen Svix/Hookdeck/eigene Lambda.
- **Jobs UI** — abhängig von der gestrichenen JobQueue.
- **Workflows / Approval-Engine** — eigenes Produkt; spezifisch bauen oder
  einkaufen.

---

## Abgeschlossen (Historie)

Bereits umgesetzt (1390 Tests grün); hier nur als Nachweis, dass die
aktiven Phasen vor der Review-Härtung durch sind.

**Feature-Phasen (ehemals `roadmap.md`):**

- **Phase 1 — Robustheit-Härtung** ✅ — `AdminRegistry.freeze()`, toter
  `schema_builder`-Singleton entfernt, Serializer→FieldAdapter-Pipeline,
  Public-API-Test, CRUD-Matrix-Lücken, Protected-Fields-Sweep,
  Examples-Smoke-Parity.
- **Phase 2 — Architektur-Konsolidierung** ✅ — ein FieldPolicy-Resolver
  (`FieldPermission.strictest()`), Inline-Permission, Validation-Hints,
  per-caller `field_permission`, optionale Provider-Protocols
  (`UserListingProvider`, `CredentialAuthProvider`).
- **Phase 3 — Auth-Hardening** ✅ — Refresh Tokens, `RevokedToken`
  (single-token logout), Password-Reset, 2FA/TOTP inkl. Login-Step-Up,
  OAuth-Flow.
- **Phase 4 — Storage-SPI + File/Image Fields** ✅ — `StorageBackend`-SPI,
  FileField/ImageField, S3-Adapter-Extension, generisches
  `Notifier`-Protocol.
- **Phase 5 — UI-Tiefe** ✅ — Audit-UI, Permission-Matrix-UI,
  Import/Export-Ausbau, Form-Layout (Tabs/Conditional/Dependent Fields,
  Placeholders, Widgets), List-View (Badges, Density, Sortable, Date
  Hierarchy, Inline Bulk Edit), Admin-Pages/Plugin-Slots.

**Pre-1.0-Härtung (ehemals `stabilization.md`):**

- **P1** ✅ — JS-Test-Harness (Vitest + `logic.js`), Contract-Snapshot-Tests,
  `mypy` in CI (gescopt auf die Vertragsschicht).
- **P2** ✅ — Field-Visibility als **ein** Resolver festgeschrieben;
  User-Entkopplung als Grenze dokumentiert (`root/*`, `audit/service.py`,
  `bootstrap.py`, `cli/main.py` bleiben bewusst Builtin-`User`-gekoppelt).
- **P3** ✅ — `protected_fields`-Singleton akzeptiert (Tripwire-Test);
  Custom-Component-Injektionspunkt aufgeschoben; ruff auf `>=0.15,<0.16`
  gepinnt + Lint/Format-Drift bereinigt.
