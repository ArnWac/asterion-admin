# asterion — Roadmap (konsolidiert)

Stand: 2026-06-24 · bezieht sich auf `asterion` 0.1.0

Dieses Dokument ist die **einzige maßgebliche Roadmap**. Es konsolidiert die
frühere `roadmap.md` (Feature-Phasen, Non-Goals), die `stabilization.md`
(Pre-1.0-Härtung) und die Befunde der externen Code-Reviews
(Runde 1 R1–R12 vom 2026-06-16, Runde 2 R13–R17 vom 2026-06-18) und ergänzt
sie um den am 2026-06-24 angeforderten Block
[Governance, Datenschutz & Security (B2B/SaaS)](#governance-datenschutz--security-härtung-b2bsaas).

> Leitregel: **keine neuen Produktfeatures** vor 1.0. Jeder offene Punkt
> schließt eine Sicherheits-, Vertrauens- oder Konsolidierungslücke an
> bestehendem Code. Ein Punkt gilt erst als erledigt, wenn ein **Test** die
> Zusage absichert — bei Isolationsthemen ein Test gegen echtes PostgreSQL
> über den HTTP-Pfad, nicht nur das Primitiv.

## Wo steht die Arbeit?

- **Feature-Phasen 1–5** und **Pre-1.0-Härtung P1–P3**: abgeschlossen
  (siehe [Historie](#abgeschlossen-historie)).
- **Review-Härtung R1–R17**: umgesetzt + gemergt, bis auf **R14** (XSS:
  CSP/Token-Storage), das als [G10](#g10--xss-härtung-csp--token-storage)
  weitergeführt wird. R1/R2 (Tenant-Isolation) sind auf echtem PostgreSQL
  CI-bestätigt. Kompaktnachweis: [Abgeschlossen — R1–R17](#abgeschlossen--review-härtung-r1r17).
- **Aktive offene Arbeit**: der Block
  [Governance, Datenschutz & Security](#governance-datenschutz--security-härtung-b2bsaas)
  (G1–G22) plus die [offenen Follow-ups](#offene-follow-ups-kein-10-blocker).
- **Geparkt**: Phasen 6/7 (Enterprise-Identity etc.).

---

# Governance, Datenschutz & Security-Härtung (B2B/SaaS)

**Angefordert 2026-06-24.** Aus zwei Governance-/Datenschutz-Reviews
abgeleitete Bausteine, damit Asterion für professionelle B2B-/SaaS-Nutzung
(Erstkonsument: **Simpletimes** — Zeiterfassung für KMU-Gastronomie) sowie für
Datenschutzprüfungen und öffentliche Auftraggeber tauglich ist. Die klassische
App-Security (Tenant-Isolation, Authz, Auth, Error-Handling, Secrets) ist
bereits abgedeckt (R1–R17); die hier gelisteten Punkte schließen die
**Governance-/Datenschutz-Lücken**.

> **Architekturentscheidung (festgelegt):** Datenschutz-Funktionalität kommt als
> **Core-Modul `asterion/privacy/`**, **nicht** als Extension. Begründung: Die
> Extension-SPI ist für optionale, third-party-artige Bausteine
> ([extensions.md](extensions.md) „When NOT to write an extension"); Anonymisierung,
> Retention und Offboarding greifen aber in Core-Modelle (`User`, `AuditLog`,
> `TenantAuditLog`, `Tenant`) und Core-Flows (Audit-Writer, Tenant-Lifecycle) ein
> — das ist Kernverhalten, kein Plugin. Erweiterbarkeit bleibt erhalten, indem die
> **PII-Klassifizierung** als beitragbare Registry nach dem Vorbild von
> [`ProtectedFieldRegistry`](../asterion/security/protected_fields.py) gebaut wird
> (Beiträge via `register_pii_fields`-Hook), und externer Versand die vorhandenen
> `StorageBackend`/`Notifier`-Protocols nutzt.

## Übersicht

| Stufe | ID | Thema | Aufwand | Status |
|---|---|---|---|---|
| **Muss** | G1 | PII-Klassifizierung (`privacy/classification.py` + Hook) | mittel | geplant |
| **Muss** | G2 | User-Lebenszyklus: Deaktivierung → Anonymisierung (Art. 17) | mittel | geplant |
| **Muss** | G3 | Audit-Retention vollständig (inkl. `tenant_audit_logs`) | mittel | geplant |
| **Muss** | G4 | Datenschutz-Doku-Set (PRIVACY/DATA_RETENTION/AUDIT_LOGGING/DATA_PROCESSING) | mittel | geplant |
| **Muss** | G5 | Beschäftigtendatenschutz-Defaults (Anti-Mitarbeiterüberwachung) | mittel | geplant |
| **Sollte** | G6 | Tenant-Offboarding (Export + Schema-Drop + public-Cleanup) | groß | geplant |
| **Sollte** | G7 | PII-aware Audit-Redaktion | mittel | geplant |
| **Sollte** | G8 | Betroffenenrechte (Auskunft/Export/Berichtigung/Einschränkung + DSAR-Log) | groß | geplant |
| **Sollte** | G9 | Impersonation-`reason` + Governance-Trail | klein | geplant |
| **Sollte** | G10 | XSS-Härtung abschließen (CSP-Nonce / HttpOnly-Cookie) — ex-R14 | mittel | 🟡 teilweise |
| **Sollte** | G11 | Governance-Doku (GOVERNANCE/THREAT_MODEL/ADRs/Berechtigungsmatrix/Shared-Responsibility) | mittel | geplant |
| **Sollte** | G12 | Security-CI-Härtung (Dependency-/Secret-Scan, SBOM, PII-freie Testdaten) | mittel | geplant |
| **Sollte** | G13 | IDOR-/Tenant-Leak-Testsuite ausbauen | mittel | geplant |
| **Sollte** | G19 | Per-Tenant Rate-Limiting / Quotas (Noisy-Neighbor) | mittel | geplant |
| **Sollte** | G20 | Observability: OpenTelemetry-Tracing + Metriken (Core, optional) | mittel | geplant |
| **Sollte** | G21 | Passwort-Policy nach NIST 800-63B (inkl. Breach-Check) | klein | geplant |
| **Später** | G14 | Globale RBAC / Support-Rollen | groß | geplant (Design s. u.) |
| **Später** | G15 | PostgreSQL Row Level Security als Defense-in-Depth | groß | zu entscheiden (ADR) |
| **Später** | G16 | Audit-Tamper-Evidence (Hash-Chain / WORM / Legal Hold) | groß | geplant |
| **Später** | G17 | Standort-/Org-Rollen (Multi-Location-RBAC) | groß | geplant |
| **Später** | G22 | Feldverschlüsselung + Crypto-Shredding | groß | zu entscheiden (ADR) |
| **Später** | G18 | Consent-Management + DSAR-Workflow-UI | groß | geplant |

---

## Stufe 1 — Muss vor produktivem Einsatz

### G1 — PII-Klassifizierung (Fundament)

- **Problem:** Es gibt keine Stelle, an der personenbezogene Felder als solche
  deklariert sind. `sanitize_payload` kennt nur Secret-Keys, keine PII
  (`email`, `full_name`, IP, Adresse, …).
- **Risiko:** Ohne Klassifizierung sind Datenminimierung, Anonymisierung,
  Export und PII-Redaktion (G2/G6/G7/G8) nicht systematisch umsetzbar.
- **Änderung:** Neues Core-Modul `asterion/privacy/classification.py` mit
  `PIICategory` (z. B. `IDENTITY`, `CONTACT`, `BEHAVIORAL`, `SENSITIVE`) und
  einer `PIIFieldRegistry` analog zu
  [`ProtectedFieldRegistry`](../asterion/security/protected_fields.py)
  (Singleton, `freeze()`, `register_pii_fields`-Extension-Hook). Optionales
  `ModelAdmin.pii_fields` als ergonomischer Shortcut. Default-Seed:
  `User.email` (CONTACT/IDENTITY), `User.full_name` (IDENTITY).
- **Betroffene Dateien:** neu `asterion/privacy/classification.py`;
  `asterion/extensions/base.py` (+`context.py`) für den Hook;
  `asterion/registry/admin.py` (Shortcut).
- **Aufwand:** mittel. **Status:** geplant.

### G2 — User-Lebenszyklus: Deaktivierung → Anonymisierung (DSGVO Art. 17)

- **Problem:** Heute existiert nur **Stufe 1** (Deaktivierung: `is_active=False`
  + `token_version++`, [cli/main.py:1036](../asterion/cli/main.py)). **Stufe 2**
  (endgültige Löschung/Anonymisierung) fehlt; `email`/`full_name` und
  Audit-`actor_label`/`ip_address` bleiben unbegrenzt.
- **Befund Datenmodell (geprüft 2026-06-24):** Ein **harter** `DELETE users` ist
  die falsche Stufe 2. Nur `tenant_membership.user_id` trägt einen Foreign Key
  (`ondelete=CASCADE`); **alle** anderen User-Referenzen sind FK-lose Spalten —
  `audit_logs.actor_user_id` **+ `actor_label` (E-Mail im Klartext)**,
  `tenant_audit_logs.actor_user_id`, `impersonation_logs.*`,
  `saved_filters.user_id`, `revoked_tokens.user_id`,
  `password_reset_tokens.user_id`, `two_factor_backup_codes.user_id`. Ein
  Row-Delete ließe also **PII im Audit zurück** (unvollständige Löschung) und
  verwaiste Referenzen.
- **Risiko:** Löschbegehren technisch nicht erfüllbar — **rechtliches Muss**.
  Gegenläufig: Arbeitszeitdaten unterliegen gesetzlichen Aufbewahrungsfristen
  (z. B. §16 ArbZG ~2 J., Lohn-/Steuerunterlagen bis 10 J.) → sofortiges
  Vollständig-Löschen ist gerade **nicht** zulässig, solange Aufbewahrung gilt.
- **Empfohlener zweistufiger Lebenszyklus:**
  1. **Deaktivieren (vorhanden):** `is_active=False` + `token_version++`. Login
     sofort tot, reversibel; entspricht „Einschränkung der Verarbeitung"
     (Art. 18) und startet die Sperrfrist.
  2. **Anonymisieren (neu, statt Hard-Delete):** nach Ablauf der
     Aufbewahrungsfrist. `anonymize_user()` tilgt PII am User (deterministischer
     Tombstone, FK-Integrität bleibt), `anonymize_audit_actor()` tilgt
     `actor_label`/`ip_address` in **beiden** Audit-Tabellen. Der User-Row bleibt
     für Audit-Integrität; gesetzlich nötige Bewegungsdaten ggf. pseudonymisiert
     bis Fristende.

  ```
  aktiv ──disable──► inaktiv/gesperrt ──[Sperrfrist]──► anonymisiert (endgültig)
          (heute)      (Token tot, reversibel)            (G2, geplant)
  ```
- **Änderung:** `asterion/privacy/anonymizer.py`
  (`anonymize_user`, `anonymize_audit_actor`); optionale Sperrfrist-Logik
  (`user_anonymize_after_days`), die der Retention-Job (G3) anwendet; Route
  `DELETE /root/users/{id}` (= anonymisieren, **nicht** hart löschen) + CLI
  `user anonymize`. Jede Anonymisierung schreibt einen Audit-Eintrag.
- **Betroffene Dateien:** neu `asterion/privacy/anonymizer.py`;
  [root/users.py](../asterion/root/users.py) (heute nur GET);
  [cli/main.py](../asterion/cli/main.py); Zusammenspiel mit
  `asterion/privacy/retention.py` (G3).
- **Test (Abnahme):** nach Anonymisierung keine PII am User **und** im
  Audit-Actor; Login bricht; FKs intakt; Route nur Superadmin (sonst 403),
  Impersonation-Token abgelehnt; Sperrfrist: vor Fristablauf kein
  Auto-Anonymisieren durch den Job.
- **Aufwand:** mittel. **Status:** geplant.

### G3 — Audit-Retention vollständig

- **Problem:** `audit prune` löscht nur die public `audit_logs`
  ([cli/main.py:1085](../asterion/cli/main.py)). `tenant_audit_logs` (pro
  Tenant-Schema) werden **nie** geprunt.
- **Risiko:** Unbegrenztes Wachstum **und** Verstoß gegen Speicherbegrenzung pro
  Mandant.
- **Änderung:** Prune-Logik nach `asterion/privacy/retention.py` ziehen;
  deklarative Retention-Policy (Frist pro Tabelle/Aktion); `apply_retention(db)`
  iteriert über `tenants`, setzt je Schema `search_path` und prunt
  `tenant_audit_logs`. Wendet auch die G2-Sperrfrist an (Auto-Anonymisieren nach
  `user_anonymize_after_days`). CLI `audit prune --all-tenants` +
  `privacy retention-run`.
- **Betroffene Dateien:** neu `asterion/privacy/retention.py`;
  [cli/main.py](../asterion/cli/main.py).
- **Test (Abnahme, `@pytest.mark.postgres`):** prune löscht in public **und** in
  jedem Tenant-Schema; Cutoff korrekt.
- **Aufwand:** mittel. **Status:** geplant.

### G4 — Datenschutz-Doku-Set

- **Problem:** Es fehlen die Pflicht-/Nachweisdokumente für eine DSGVO-Prüfung.
- **Risiko:** Keine Nachweisbarkeit (Art. 5 Abs. 2 Rechenschaftspflicht);
  Blocker für jede Datenschutzprüfung.
- **Änderung (neue Dokumente):** `docs/PRIVACY.md` (PII-Inventar je Spalte +
  Zweck, Lösch-/Auskunfts-Workflow), `docs/DATA_RETENTION.md` (Default-Fristen +
  Cron-Setup), `docs/AUDIT_LOGGING.md` (was wird/wird nicht geloggt,
  Aufbewahrung, Manipulationsschutz), `docs/DATA_PROCESSING.md` (AVV-relevante
  technische Beschreibung + TOMs-Vorlage). `SECURITY.md`/`security.md` „Known
  limitations" ehrlich halten, bis G2/G6 umgesetzt sind.
- **Backup-vs-Löschung:** `DATA_RETENTION.md` muss ehrlich beschreiben, wie
  Erasure (G2) mit Backups/PITR umgeht — Crypto-Shredding (G22) als saubere
  Strategie, sonst der dokumentierte Hinweis „Löschung wirkt erst nach
  Backup-Rotation X".
- **Aufwand:** mittel. **Status:** geplant.

### G5 — Beschäftigtendatenschutz-Defaults (Anti-Mitarbeiterüberwachung)

- **Problem:** Simpletimes verarbeitet Verhaltens-/Leistungsdaten von
  Beschäftigten (§26 BDSG / Art. 88 DSGVO, Mitbestimmung). Audit-`changes` von
  z. B. Stempelkorrekturen können unbeabsichtigt eine lückenlose
  Verhaltenskontrolle ermöglichen.
- **Risiko:** Rechtswidrige Mitarbeiterüberwachung; Betriebsrats-/
  Mitbestimmungsproblem.
- **Änderung:** Datenschutzfreundliche Defaults dokumentieren + Schalter:
  tenant-spezifische Audit-Detailtiefe (Default: minimal — Aktion/Actor/Resource,
  **keine** Feldwert-Diffs für als `BEHAVIORAL` klassifizierte Felder ohne
  explizites Opt-in). Baut auf G1 + G7 auf. Dokumentation in `docs/PRIVACY.md`
  (Abschnitt „Beschäftigtendatenschutz").
- **Betroffene Dateien:** [audit/service.py](../asterion/audit/service.py);
  [core/config.py](../asterion/core/config.py) (neuer Schalter, s. u.).
- **Aufwand:** mittel. **Status:** geplant.

---

## Stufe 2 — Sollte vor zahlenden B2B-Kunden

### G6 — Tenant-Offboarding

- **Problem:** Nur `tenant disable` ([cli/main.py:731](../asterion/cli/main.py)).
  Kein Schema-Drop, kein Export, kein Cleanup der public-Zeilen (Memberships,
  Audit mit `tenant_id`).
- **Risiko:** AVV-Pflicht (Rückgabe/Löschung nach Vertragsende) nicht erfüllbar
  — B2B-Blocker.
- **Änderung:** `asterion/tenancy/offboarding.py` mit
  `offboard_tenant(slug, *, mode="archive"|"drop")`: Export → public-Zeilen-
  Cleanup → `DROP SCHEMA tenant_<slug> CASCADE` (idempotent, transaktional,
  audit-pflichtig). Route `POST /root/tenants/{id}/offboard` + CLI
  `tenant offboard` / `tenant export`.
- **Betroffene Dateien:** neu `asterion/tenancy/offboarding.py`;
  [root/tenants.py](../asterion/root/tenants.py); [cli/main.py](../asterion/cli/main.py).
- **Test (`@pytest.mark.postgres`):** nach Offboard Schema weg, public-Zeilen
  weg, Folge-Request auf den Slug → 404; Export-Bundle vollständig.
- **Aufwand:** groß. **Status:** geplant.

### G7 — PII-aware Audit-Redaktion

- **Problem:** `changes`-Diffs sind nicht PII-klassifiziert
  ([audit/service.py:125](../asterion/audit/service.py)).
- **Risiko:** Datenminimierung (Art. 5) verletzt; größerer Schaden bei
  Audit-Leak.
- **Änderung:** `changes` zusätzlich durch einen PII-Redaktor schicken
  (`redact`/`hash`/`keep`, gesteuert über die G1-Klassifizierung). Default für
  Framework-Modelle: `email`/`name` maskiert.
- **Betroffene Dateien:** [audit/service.py](../asterion/audit/service.py);
  `asterion/privacy/classification.py`.
- **Aufwand:** mittel. **Status:** geplant.

### G8 — Betroffenenrechte (Auskunft / Export / Berichtigung / Einschränkung)

- **Problem:** Kein per-Person-Datenexport, keine Protokollierung von
  Datenschutzanfragen (DSAR).
- **Risiko:** Art. 15/16/18/20 nicht bedienbar.
- **Änderung:** `asterion/privacy/export.py` mit `export_subject(user_id)` (JSON-
  Bundle aller PII-Quellen, kein fremder Tenant). Berichtigung läuft über
  bestehendes CRUD; „Einschränkung der Verarbeitung" = `is_active=False` +
  dokumentierter Marker. DSAR-Protokoll als kleine `data_subject_requests`-Tabelle
  (wer/was/wann/Ergebnis). Versand via vorhandenes `Notifier`/`StorageBackend`.
- **Betroffene Dateien:** neu `asterion/privacy/export.py`, ggf.
  `asterion/models/data_subject_request.py`; Route unter `root/`.
- **Aufwand:** groß. **Status:** geplant.

### G9 — Impersonation-`reason` + Governance-Trail

- **Problem:** [ImpersonationLog](../asterion/models/impersonation_log.py) hält
  wer/wen/`jti`, aber **kein `reason`**.
- **Risiko:** Support-Zugriff auf fremde (Beschäftigten-)Daten ohne nachweisbare
  Zweckbindung.
- **Änderung:** Migration (Spalte `reason`), Pflichtparameter in der
  Impersonate-Route, in Log + Audit-`changes` schreiben.
- **Betroffene Dateien:** [models/impersonation_log.py](../asterion/models/impersonation_log.py);
  [root/impersonation.py](../asterion/root/impersonation.py); neue Shared-Migration.
- **Test:** Impersonate ohne `reason` → 422; `reason` landet im Log.
- **Aufwand:** klein. **Status:** geplant.

### G10 — XSS-Härtung: CSP + Token-Storage (ex-R14)

- **Problem:** Bearer-Token im `localStorage`
  ([ui/static/admin/api.js:14](../asterion/ui/static/admin/api.js)) → eine
  einzige XSS im Admin-UI = Token-Diebstahl ohne HttpOnly-Schutz.
- **Risiko:** Hoch (clientseitig der größte Hebel).
- **Bereits erledigt (aus R14):** konfigurierbarer
  `content_security_policy`-Header ([core/middleware.py](../asterion/core/middleware.py),
  Default aus; API-first-Deployments können strikt setzen). Header-Assertion in
  `tests/operations/test_middleware.py`.
- **Offen:** Nonce-Härtung der Inline-Skripte der Bundled-UI (damit ein striktes
  `script-src 'self'` greift) **oder** HttpOnly-Cookie-Token-Option (zieht dann
  eine CSRF-Schicht nach sich — vgl. [Bewusst NICHT umgesetzt](#bewusst-nicht-umgesetzt)).
- **Aufwand:** mittel. **Status:** 🟡 teilweise.

### G11 — Governance-Doku

- **Problem:** Es fehlen `GOVERNANCE.md`, `THREAT_MODEL.md`, ADRs, eine explizite
  **Berechtigungsmatrix**, das **Shared-Responsibility-Modell** (Betreiber vs.
  Kunde) und Datenfluss-/externe-Dienste-Doku.
- **Risiko:** Governance-Nachweis fehlt; erschwert Security-/Vergabeprüfungen.
- **Änderung:** `docs/GOVERNANCE.md`, `docs/THREAT_MODEL.md` (STRIDE light),
  `docs/adr/` (erste ADRs: „Schema-per-Tenant statt RLS", „Privacy als Core-Modul",
  „Bearer-Token statt Cookie-Session"), `docs/permission-matrix.md`
  (Rolle × Permission-Key, generierbar aus dem `PermissionCatalog`),
  `docs/shared-responsibility.md`. Datenflüsse/externe Dienste (S3, SMTP, OAuth,
  Redis) in `DATA_PROCESSING.md` ergänzen.
- **API-Deprecation/Sunset-Policy:** ergänzend zu `CONTRACT_VERSION` eine
  dokumentierte Deprecation-Politik + `Deprecation`/`Sunset`-Header (RFC 8594)
  für abgekündigte Felder/Endpunkte (Vorbild: Stripe datierte Versionen, GitHub
  API-Versioning) — kleiner Header-Anteil, Rest Doku.
- **Aufwand:** mittel. **Status:** geplant.

### G12 — Security-CI-Härtung

- **Problem:** Kein Dependency-/Secret-Scanning, kein SBOM; Beispieldaten/Tests
  ohne dokumentierte PII-Freiheit.
- **Risiko:** Bekannte CVEs / geleakte Secrets / Echt-PII in Fixtures bleiben
  unbemerkt.
- **Änderung:** [.github/workflows/ci.yml](../.github/workflows/ci.yml) um
  `pip-audit` (Dependency-Scan), `gitleaks`/`trufflehog` (Secret-Scan) und
  SBOM-Erzeugung (`cyclonedx`) erweitern; Tripwire-Test, dass Fixtures keine
  realen PII-Muster enthalten.
- **Aufwand:** mittel. **Status:** geplant.

### G13 — IDOR-/Tenant-Leak-Testsuite ausbauen

- **Problem:** Tenant-Isolation ist PG-getestet (R2), aber IDOR systematisch
  (fremde Record-/Membership-IDs → 404 statt 403) ist nur punktuell gedeckt.
- **Risiko:** Regressions-Lücke bei der wichtigsten Garantie.
- **Änderung:** Parametrisierte Negativ-Tests pro registrierter Ressource:
  fremder-Tenant-Datensatz → 404; Cross-Tenant-Mutation → 404; Member-Router
  bereits abgedeckt ([member_router.py:152](../asterion/admin/member_router.py)).
- **Aufwand:** mittel. **Status:** geplant.

### G19 — Per-Tenant Rate-Limiting / Quotas (Noisy-Neighbor-Schutz)

- **Problem:** Rate-Limiting existiert nur am Login
  ([auth/rate_limiter.py](../asterion/auth/rate_limiter.py)), kein generelles
  Limit pro Tenant/User/Route. Ein Tenant kann die API für alle anderen
  ausbremsen.
- **Risiko:** „Lauter Nachbar"-Ausfall — der klassische Multi-Tenant-Fehler;
  zugleich fehlt eine faire Ressourcenverteilung als SLA-Zusage.
- **Vorbild:** Stripe (per-account limits), Shopify (per-shop), DRF
  `ScopedRateThrottle`, API-Gateways (Kong/Tyk).
- **Änderung:** Das vorhandene `RateLimiterBackend`-Protocol auf eine
  Request-Middleware verallgemeinern, die auf `(tenant, route-bucket)` keyt;
  Limits in `CoreAdminConfig` (Default großzügig/aus); Antwort `429` über das
  bestehende Error-Envelope (`rate_limited`). Das Redis-Backend (ex-R7) deckt
  Multi-Worker ab.
- **Betroffene Dateien:** [core/middleware.py](../asterion/core/middleware.py);
  [auth/rate_limiter.py](../asterion/auth/rate_limiter.py) (Backend
  wiederverwenden); [core/config.py](../asterion/core/config.py).
- **Test:** Limit pro Tenant greift; Tenant A erschöpft sein Budget ohne Tenant B
  zu beeinflussen; `429`-Envelope korrekt.
- **Aufwand:** mittel. **Status:** geplant.

### G20 — Observability: OpenTelemetry-Tracing + Metriken (Core, optional)

- **Problem:** Es gibt strukturierte Logs (`request_id`/`tenant_id`/`actor`),
  aber kein verteiltes Tracing und keine Metriken. Incident-Response und
  per-Tenant-SLA-Nachweis sind dadurch mühsam.
- **Risiko:** Betriebs-Governance-Lücke (Diagnose, SLA-Belege) — bei
  B2B/öffentlich zunehmend Erwartung.
- **Vorbild:** django-prometheus, Rails-Instrumentation, Laravel Pulse, Spring
  Actuator.
- **Änderung:** Optionale OTel-Instrumentierung (Span pro Request mit
  `tenant_id`/`actor_user_id`/`route` als Attributen, aus dem vorhandenen
  Request-Lifecycle) und ein schlankes `/metrics` (Counter/Histogram).
  **Optionale** Dependency — ohne installiertes OTel/`prometheus-client` ist es
  ein No-op. Knüpft an die gestrichene „Observability-als-Extension"-Notiz an
  („gehört, wenn überhaupt, in den Core").
- **Betroffene Dateien:** [core/middleware.py](../asterion/core/middleware.py);
  neu z. B. `asterion/core/observability.py`;
  [core/config.py](../asterion/core/config.py) (Schalter).
- **Aufwand:** mittel. **Status:** geplant.

### G21 — Passwort-Policy nach NIST 800-63B (inkl. Breach-Check)

- **Problem:** Nur `password_min_length`
  ([core/config.py](../asterion/core/config.py)). Keine pluggable Policy, kein
  Abgleich gegen geleakte Passwörter.
- **Risiko:** Schwache/kompromittierte Passwörter bei builtin-Auth; NIST rät zu
  Länge + Breach-Check statt Komplexitätsregeln.
- **Vorbild:** Django `AUTH_PASSWORD_VALIDATORS`, HIBP Pwned-Passwords
  (k-Anonymity — nur ein Hash-Präfix verlässt den Server, kein Klartext).
- **Änderung:** `PasswordPolicy`-Protocol mit Default-Validatoren (Länge, optional
  Breach-Check via HIBP); verdrahtet in
  [auth/password.py](../asterion/auth/password.py) und in die Reset-/Invite-Flows.
  HIBP-Check standardmäßig **aus** (externer Netzaufruf), opt-in.
- **Betroffene Dateien:** [auth/password.py](../asterion/auth/password.py);
  [core/config.py](../asterion/core/config.py).
- **Test:** zu kurzes/geleaktes Passwort wird abgelehnt; ohne HIBP-Opt-in kein
  Netzaufruf.
- **Aufwand:** klein. **Status:** geplant.

---

## Stufe 3 — Später / Enterprise

### G14 — Globale RBAC / Support-Rollen

- Detail-Design unten:
  [Globale RBAC](#globale-rbac--nicht-superadmin-globale-admins-support-rollen).
  Least-Privilege für Cross-Tenant-Support statt all-or-nothing Superadmin.
- **Aufwand:** groß. **Status:** geplant (Design liegt vor).

### G15 — PostgreSQL Row Level Security (Defense-in-Depth)

- **Problem:** Isolation hängt heute allein am `search_path`. RLS als **zweite**
  Schicht (nicht als Ersatz; row-level-Tenancy bleibt
  [Non-Goal](#non-goals--donts-durchgängig)) fängt einen fehlenden
  `SET search_path` ab.
- **Risiko/Einordnung:** Mittel; berührt den CI-bestätigten Isolationspfad —
  daher **ADR-pflichtig** vor Umsetzung.
- **Änderung:** ADR „Schema-per-Tenant + optionale RLS"; optional aktivierbare
  `tenant_id`-RLS-Policies auf Tenant-Tabellen. Sorgfältig gegen die bestehende
  Architektur abwägen.
- **Aufwand:** groß. **Status:** zu entscheiden (ADR).

### G16 — Audit-Tamper-Evidence

- **Problem:** Audit-Zeilen sind mutier-/löschbar (Prune); kein
  Manipulationsnachweis.
- **Risiko:** Für regulierte Kunden / öffentliche Auftraggeber unzureichend.
- **Änderung:** Hash-Chain (jede Zeile signiert Vorgänger-Hash) **oder**
  Append-only/WORM + Legal-Hold-Flag, das Prune überstimmt.
- **Aufwand:** groß. **Status:** geplant.

### G17 — Standort-/Org-Rollen (Multi-Location-RBAC)

- **Problem:** Restaurant-Ketten haben mehrere Filialen; heute gibt es nur
  Rollen **pro Tenant**, nicht **pro Standort**.
- **Risiko:** Über-Berechtigung (Filialleiter sieht alle Filialen).
- **Änderung:** Optionale Org-/Standort-Scope-Ebene unter dem Tenant; eigenes,
  versioniertes Increment.
- **Aufwand:** groß. **Status:** geplant.

### G18 — Consent-Management + DSAR-Workflow-UI

- Einwilligungsverwaltung + UI für Betroffenenanfragen (auf G8 aufbauend).
- **Aufwand:** groß. **Status:** geplant.

### G22 — Feldverschlüsselung + Crypto-Shredding

- **Problem:** PII-Spalten liegen im Klartext in der DB; eine endgültige
  Löschung (G2) wirkt **nicht** in PITR-/Offline-Backups — das
  DSGVO-Backup-Problem.
- **Risiko:** Special-Category-Daten ungeschützt at-rest; Erasure-Zusage in
  Backups nicht einhaltbar.
- **Vorbild:** Rails 7 ActiveRecord Encryption, Laravel encrypted casts,
  CipherStash.
- **Lösung (Crypto-Shredding):** Per-Subject- oder Per-Tenant-Schlüssel; sensible
  Spalten werden damit verschlüsselt gespeichert. **Schlüssel löschen = Daten
  faktisch unlesbar — auch in Backups.** Damit erfüllt G2 die Löschung auch dort,
  wo physisches Überschreiben unmöglich ist; verstärkt zugleich G16.
- **Änderung:** verschlüsselter SQLAlchemy-Spaltentyp (an die G1-Klassifizierung
  gekoppelt), Key-Management-Hook (Schlüssel pro Tenant/Subject), Anbindung von
  `anonymize_*` an „Schlüssel verwerfen".
- **Betroffene Dateien:** neu `asterion/privacy/encryption.py`;
  `asterion/privacy/classification.py`; `asterion/privacy/anonymizer.py`.
- **Einordnung:** ADR-pflichtig (Key-Management ist heikel; Schlüsselverlust =
  Datenverlust).
- **Aufwand:** groß. **Status:** zu entscheiden (ADR).

### Beobachtungsliste — optional, (noch) nicht eingeplant

Aus dem Framework-Vergleich, bewusst (noch) ohne G-Nummer — erst bei konkretem
Kundenbedarf scopen:

- **Aktive Sessions auflisten + per Gerät widerrufen** (GitHub/Google-Stil).
  Braucht ein server-seitiges Refresh-Token-Register; heute decken
  `token_version` (logout-all) + per-`jti`-Revocation den Kern ab.
- **Audit-Streaming an SIEM** (Splunk/Datadog/OpenSearch). Enterprise/
  öffentlicher Sektor; dockt an G16 an.
- **Vier-Augen-Prinzip (Maker-Checker) für destruktive Root-Aktionen**
  (Tenant-Offboard, Impersonate). **Grenze beachten:** nur eng umrissen für
  Root-Destruktiv-Ops — **keine** generische Approval-/Workflow-Engine (die
  bleibt [gestrichen](#bewusst-gestrichen-waren-in-früheren-plänen-jetzt-raus)).

---

## Neue Konfigurationsoptionen (gesammelt)

Additiv zu [`CoreAdminConfig`](../asterion/core/config.py), alle mit
datenschutzfreundlichem Default:

| Option | Default | Zweck | Item |
|---|---|---|---|
| `audit_retention_days` | `90` | Standard-Aufbewahrung Audit (public + tenant) | G3 |
| `user_anonymize_after_days` | `None` | Sperrfrist vor Auto-Anonymisierung (None = nur manuell) | G2/G3 |
| `audit_pii_mode` | `"redact"` | `redact`/`hash`/`keep` für PII in `changes` | G7 |
| `audit_behavioral_detail` | `False` | Feldwert-Diffs für `BEHAVIORAL`-Felder nur bei Opt-in | G5 |
| `impersonation_require_reason` | `True` | Begründung beim Impersonate erzwingen | G9 |
| `privacy_export_enabled` | `True` | Subject-Export-Routen aktiv | G8 |
| `tenant_rate_limit` | `None` | Per-Tenant API-Limit (Requests/Fenster; None = aus) | G19 |
| `observability_enabled` | `False` | OTel-Tracing + `/metrics` (optionale Dependency) | G20 |
| `password_breach_check` | `False` | HIBP-Abgleich beim Passwort-Setzen (externer Aufruf) | G21 |
| `field_encryption_enabled` | `False` | Feldverschlüsselung für PII-Spalten | G22 |

---

# Offene Follow-ups (kein 1.0-Blocker)

### Bundled-UI: gemeinsames `widgets.js`-Modul (Schema → Widget)

**Ziel:** Das Schema→Widget-Mapping der **gebündelten** Admin-UI in einem
einzigen Modul `asterion/ui/static/admin/widgets.js` zentralisieren, das sowohl
Model-Forms als auch Action-`input_schema`-Forms bedient.

**Befund (Stand v0.1.37):** Die Render-Logik liegt doppelt vor —
[views/form.js](../asterion/ui/static/admin/views/form.js) `buildInput()` rendert
aus `FieldMeta` (`type`/`widget`/`metadata.choices`/`validation`),
[views/action_modal.js](../asterion/ui/static/admin/views/action_modal.js)
`buildInput()` rendert aus rohem JSON-Schema (`format`/`enum`/`title`/
`min*`/`max*`/`pattern`). Date-/Time-Picker, Select, Number-/Text-Inputs samt
Validierungs-Attributen sind dadurch konzeptionell zweimal implementiert.

**Abgrenzung — reine UI-Aufgabe, nicht Contract:** Betrifft ausschließlich das
mitgelieferte UI. Die Datenbasis (`FieldMeta.widget`/`type`/`validation`/
`metadata.choices`, `AdminActionMeta.input_schema`, `InlineMeta.widget`/
`value_field`) ist bereits im Contract und damit für jedes Fremd-Frontend
nutzbar; ein eigenes Frontend konsumiert diese JS-Dateien gar nicht. Daher
**kosmetisch/intern**, kein funktionaler Bedarf solange nur die Bundled-UI
verwendet wird.

**Vorgehen wenn aufgegriffen:** Eine gemeinsame „widget spec" definieren, auf
die sowohl `FieldMeta` als auch JSON-Schema normalisiert werden
(date-time/date/time → Picker, enum → Select, boolean → Toggle, FK → Picker,
min/max/length/pattern → Validierung, `title`/`description` → Label/Hilfetext);
`form.js` + `action_modal.js` auf das Modul umstellen; Dual-List-Inline
(`widget="dual_list"`) als weiteren Widget-Typ einsortieren.

**Status:** offen, **kein 1.0-Blocker**.

### mypy aufs Gesamtpaket

**Ziel:** mypy von der Vertragsschicht (`providers/` + `core/config.py`) auf
`asterion/` insgesamt ausweiten.

**Befund:** ~80 Fehler, überwiegend Framework-Typing-Nits (`var-annotated`
an SQLAlchemy-Statements, `type[ModelAdmin]`-vs-Instanz in CRUD/Contract-
Signaturen, FastAPI-`lifespan`-Generics). Mehrheitlich echte, aber benigne
Signaturinkonsistenzen.

**Vorgehen wenn aufgegriffen:** kategorienweise (`var-annotated` zuerst —
billig; dann `type[ModelAdmin]` → `ModelAdmin` in den CRUD/Contract-
Signaturen; FastAPI-Quirks gezielt `# type: ignore` mit Begründung), dann
`[tool.mypy] files` aufs Paket erweitern.

**Status:** offen, **kein 1.0-Blocker**.

### Idempotenz-Schlüssel für Schreib-APIs (generische HTTP-Schicht)

**Was Idempotenz heißt:** Dieselbe Schreiboperation mehrfach ausführen hat
denselben Effekt wie einmal — eine Wiederholung verdoppelt nichts. `GET`/
`DELETE` sind von Natur aus idempotent; `POST` (etwas anlegen) ist es nicht und
braucht dafür einen Schlüssel.

**Einordnung:** Das ist **API-Grundlagen-Härtung**, kein Produktfeature — dieselbe
Familie wie Request-IDs, Error-Envelope und Pagination, die der Core bereits
besitzt. Daher gehört es nach Asterion und **nicht** in die App: es ist generische
Plumbing, und der Core hält bereits Request-Lifecycle, Tenant-Kontext und
DB-Session — genau das, was die Schicht braucht.

**Problem:** Einzelne interne Operationen sind bereits idempotent geschrieben
(Token-Revocation, Tenant-Bootstrap, `create-superadmin`). Aber es gibt **keinen
generischen Idempotency-Key-Mechanismus** für die Schreib-APIs. Bei der
Zeiterfassung über instabile Terminal-Netze ist die Wiederholung eines
`POST /punch` (verlorene Antwort → Client retried) der Regelfall → ohne Schlüssel
doppelte Buchungen.

**Zwei geschichtete Ebenen (beide nötig, sie tun Unterschiedliches):**

- **(A) Domain-Backstop, App-seitig, trivial:** ein `UNIQUE`-Constraint auf dem
  fachlichen Schreibschlüssel (z. B. `TimeEvent.idempotency_key`). Erzwingt
  Geschäfts-Eindeutigkeit und greift auch dann noch, wenn die HTTP-Schicht (B)
  ihren Eintrag längst gepruned hat. **Steht der App heute ohne Core-Änderung
  zur Verfügung.**
- **(B) Generische HTTP-Schicht in Asterion (dieses Item):** fängt den Retry,
  *bevor* der Handler erneut läuft, und replayt die gespeicherte Antwort — schützt
  damit **alle** opt-in-Schreib-Endpunkte uniform. (A) bleibt als Defense-in-Depth.

**Design (B):**

```
- Greift auf unsicheren Methoden (POST/PATCH/PUT) mit Header `Idempotency-Key`.
- Tabelle idempotency_keys (TenantModel, pro Schema → Isolation + Tenant-Cleanup):
    key (unique), request_fingerprint (Body+Pfad-Hash), state (in_progress|done),
    response_status, response_body, response_headers, created_at, expires_at
- Ablauf:
    key fehlt         → Zeile "in_progress" INSERT (UNIQUE serialisiert
                         parallele Retries), Handler laufen lassen, Antwort
                         speichern, state="done"
    key + done        → gespeicherte Antwort replayen; Fingerprint-Mismatch
                         → 409 "key mit abweichendem Payload wiederverwendet"
    key + in_progress → 409 "request in progress" (kein Doppel-Lauf)
- TTL + Prune-Job (z. B. 24–48 h).
- Opt-in pro Router via Marker-Dependency (Default aus → kein Verhalten ändert sich).
```

**Mechanik-Hinweis (ehrlicher Aufwand):** (B) muss die Response *erfassen* und
replayen — das geht nur als Middleware oder als custom `APIRoute`-Klasse, nicht
als reine Dependency. Das ist der eigentliche Implementierungsaufwand; der
DB-Teil ist klein. Deshalb ist (B) ein eigenes, gescoptes Item und **kein
Blocker** für den Zeiterfassungs-Core — der fährt zunächst auf (A).

**Test (Abnahmekriterium):** (1) Zwei identische `POST` mit gleichem Key → genau
eine Ausführung, zweite Antwort ist Replay. (2) Gleicher Key + abweichender Body
→ 409. (3) Paralleler identischer Key (asyncio.gather) → eine Ausführung, kein
Doppel-Insert. (4) Nach TTL/Prune verhält sich der Key wie neu. (5) Tenant A und
B können denselben Key unabhängig benutzen (Schema-Isolation).

**Status:** geplant — Design festgelegt, Umsetzung nach Bedarf; der erste
Konsument (Zeiterfassungs-Punch) läuft bis dahin auf Domain-Backstop (A).

### Globale RBAC — nicht-superadmin globale Admins („Support"-Rollen)

**Angefordert 2026-06-23 · Roadmap-ID [G14](#g14--globale-rbac--support-rollen).**
Heute sind die globalen (public-schema) Ressourcen — `users`, `tenants`,
`audit_logs`, `impersonation_logs`, `tenant_memberships` — **ausschließlich
superadmin-zugänglich**. Der Zugriff hängt allein an `User.is_superadmin`: ein
Superadmin bekommt im Public-Kontext `admin.*`, jeder Nicht-Superadmin bekommt
dort **keine** Keys und wird geblockt. Ein **per-Rollen-Permissionsystem
existiert nur per-Tenant** (`tenant_roles` + `tenant_role_permissions` im
Tenant-Schema). Für die globale Ebene gibt es bewusst kein Äquivalent.

**Ziel:** eine globale Rolle wie **„Support"** — ein Nicht-Superadmin, der
tenant-übergreifend bestimmte globale Ressourcen sehen (typischer Fall:
read-only) und ggf. eng umrissen bearbeiten darf, **ohne** den Vollzugriff
eines Superadmins. Beispiel: Support sieht alle `users` + `tenants` +
`audit_logs` read-only, darf aber nicht impersonieren, keine Tenants anlegen
und keine Logs löschen.

**Warum das ein eigenes Feature ist (kein Reuse der Tenant-Matrix):** Die
Tenant-RBAC-Tabellen leben im **Tenant-Schema** und werden über den
tenant-gescopten `search_path` aufgelöst; im Public-Kontext (`ctx.tenant is
None`) existieren sie nicht. Globale Rollen brauchen daher **eigene
public-schema-Tabellen** und einen eigenen Auflösungspfad im
`BuiltinPermissionProvider`.

**Designskizze (noch nicht umgesetzt):**

- **Datenmodell (public schema, `GlobalModel`):**
  - `global_roles` — Rollendefinition (z. B. „Support", „Billing").
  - `global_role_permissions` — Rolle × Permission-Key (z. B. `users.read`,
    `tenants.read`, `audit_logs.read`).
  - `user_global_roles` — Zuordnung `User` ↔ `global_role` (n:m).
- **Permission-Auflösung:** `BuiltinPermissionProvider.get_permissions`
  ([providers/permissions.py](../asterion/providers/permissions.py)) im
  **Public-Kontext** (kein aktiver Tenant) um die globalen Rollen-Keys des
  Users ergänzen — additiv zu `is_superadmin`. Im Tenant-Kontext bleibt alles
  wie heute (tenant-RBAC).
- **Read-only-Default:** (a) die Rolle vergibt schlicht nur `*.read`-Keys;
  (b) `ReadOnlyPolicy` / `AdminPolicy.read_only` erzwingt es hart unabhängig
  von den Keys. Für „Support sieht alles, ändert nichts" genügt (a).
- **UI:** Eine **globale Permission-Matrix** analog zur per-Tenant-Matrix, aber
  public-gescopt, plus Rollen-Zuweisung auf der `User`-Detailseite
  (Wiederverwendung des Two-List-Pickers
  [views/role_permissions.js](../asterion/ui/static/admin/views/role_permissions.js)).

**Sicherheits-/Vertrauens-Hinweise (müssen ins Scoping):**

- Globale Ressourcen tragen **sensible, tenant-übergreifende** Daten (User-PII,
  Impersonation-Logs, alle Memberships). Eine Support-Rolle ist per Definition
  ein **cross-tenant**-Einblick — bewusste Vertrauensentscheidung. Default daher
  **read-only**; Impersonation/Tenant-Anlage **nie** über eine Rolle, sondern
  weiter `is_superadmin`-only.
- Die `protected_fields` / Field-Policy-Schicht bleibt die Grenze für einzelne
  Spalten (z. B. `password_hash`, `token_version`).
- Kein „Rolle vergibt `admin.*`" — der Wildcard bleibt allein an
  `is_superadmin` gebunden.

**Test (Abnahmekriterium, wenn umgesetzt):** (1) User mit Support-Rolle +
`users.read` sieht `users` read-only, bekommt 403 auf POST/PATCH/DELETE.
(2) Derselbe User ohne `impersonate`-Recht bekommt 403 auf
`/root/impersonate`. (3) Kein globaler Key ⇒ Public-Kontext weiterhin komplett
geblockt. (4) Im Tenant-Kontext greift weiterhin ausschließlich die Tenant-RBAC.

**Status:** geplant — Design festgelegt, Umsetzung als eigenes, versioniertes
Increment nach Bedarf.

---

# Architektur-Entscheidungen & Muster

## Anwendungs-Integration — Terminal-/PIN-Auth (Muster, kein Core-Feature)

**Kontext:** Die erste Fachanwendung (Zeiterfassung) braucht Stempel-Terminals,
an denen sich Mitarbeiter per PIN ein-/ausstempeln. Naheliegend wäre eine
„zweite Authentifizierung pro Router". Das ist **nicht** nötig und wird auch
**nicht** in den Core gezogen. Die Lösung entsteht durch sauberes Modellieren auf
den bestehenden Erweiterungspunkten.

**Kernentscheidung — zwei Identitätsschichten trennen:**

1. **Wer ist das Gerät?** Das Terminal authentifiziert sich gegenüber dem
   Backend (Maschinen-Identität).
2. **Wer stempelt gerade?** Der Mitarbeiter identifiziert sich per PIN. Die PIN
   authentifiziert *nicht* den Request — sie wählt nur den handelnden
   Mitarbeiter innerhalb der bereits vertrauenswürdigen Geräte-Session aus.

**Muster:**

- **Terminal = ein `User`** (Maschinen-/Service-Account in der bestehenden
  `users`-Tabelle, [models/user.py](../asterion/models/user.py)), mit eng
  geschnittenen Permissions (nur z. B. `app.timeclock.punch`), `is_superadmin=False`.
- **Mitarbeiter = `Employee`-Fachmodell** (App-seitig, `TenantModel`) mit
  `pin_hash` (bcrypt, nie Klartext) und **optionalem, meist `NULL`-em** `user_id`
  — der dokumentierte User-Entkopplungs-Fall („nicht jeder Mitarbeiter besitzt
  einen Login").

**Was dadurch aus Asterion wiederverwendet wird — ohne Neubau:**

- **Keine zweite Auth-Pipeline.** Der Terminal-Request läuft über die normale
  Auth (`require_admin_context`). Die PIN-Prüfung ist App-Logik *im Handler*.
- **Geräte-Sperre** über `User.is_active`.
- **Token-Widerruf** über `token_version` + per-`jti`-Revocation.
- **Zugriffsbegrenzung** über die granularen Permission-Keys.
- **Audit** trägt `actor_user_id` = Terminal; der konkrete Mitarbeiter wandert
  als `changes`/`record_id` in die Audit-Zeile.

**Ablauf:**

```
Terminal ──[Asterion-Auth: Terminal-User-Token]──► POST /timeclock/punch
                                                    Body: {employee_id, pin, idempotency_key}
Handler:
  1. Asterion authentifiziert den Request als Terminal-User   (Standard)
  2. Permission-Gate app.timeclock.punch                      (Standard)
  3. App verifiziert pin gegen Employee.pin_hash              (App-Logik)
  4. bucht Stempelung, Audit: actor=Terminal, employee=…      (App-Logik)
```

**Warum nicht „Mitarbeiter = User, PIN = Passwort":** (a) Eine kurze PIN als
*alleinige* Auth über das offene Netz ist unsicher — sie funktioniert nur, weil
das Gerät vertrauenswürdig und authentifiziert ist. (b) Es würde jedem Stempler
einen `User` aufzwingen, was die dokumentierte User-Entkopplung verletzt.

**Hinweise / App-seitig offen:** Terminal-User stehen in derselben
`users`-Tabelle (Trennung per Namenskonvention oder eigenem `Terminal`-Modell);
Geräte-Provisioning ist App-/Deployment-Sache; PIN-Brute-Force-Schutz über den
vorhandenen `RateLimiterBackend` (Redis-Rate-Limiter, ehemals R7).

**Status:** Muster entschieden + dokumentiert; Umsetzung in der
Zeiterfassungs-App, nicht im Framework.

## Bewusst NICHT umgesetzt

| Vorschlag (aus Review/Original) | Entscheidung | Begründung |
|---|---|---|
| Dedizierte CSRF-Schicht | abgelehnt | UI nutzt Bearer-Token aus `localStorage` ([ui/static/admin/api.js:14,58](../asterion/ui/static/admin/api.js)), keine Cookie-Ambient-Auth → kein CSRF-Vektor. Falls je eine Cookie-Session kommt (vgl. G10), neu bewerten. |
| Separate Impersonation-Session-Tabelle | abgelehnt | Bausteine existieren: `RevokedToken.jti` + `ImpersonationLog.jti` + Token an `target.token_version` gebunden. Einzeln widerrufbar. |
| Zusätzliches JSON-Schema-Dokument | aufgeschoben | Pydantic-`ModelContractMeta` + gepinnter Snapshot + `CONTRACT_VERSION` decken den Bedarf vorerst. |

---

## Release- / Versionspolitik + 1.0-Gate

**Stabilitätszusage (0.x):** Solange `0.x`, kann ein Minor-Release die
Public API oder den Contract brechen — Breaking Changes werden im
Commit/Changelog markiert. Ab `1.0` gilt SemVer:

- **Public API** = die Re-Exports in `asterion/__init__.__all__`
  (`create_admin`, `CoreAdminConfig`, `AdminRegistry`, `ModelAdmin`) plus
  die Provider-Protocols in `asterion/providers/base.py`. Breaking
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
  CI-Lauf `65391f8` vom 2026-06-18, Job `Test (PostgreSQL integration)` grün.
- [x] R5 — Changelog/Release-Notes-Prozess etabliert (`CHANGELOG.md`)
- [ ] **G10** (ex-R14) — XSS-Härtung (CSP-Nonce / Token-Storage) abgeschlossen
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
- Redis Distributed Cache (Rate-Limiter-Protocol existiert; Backend ist Extension)
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
  PostgreSQL-Schema, nicht aus `tenant_id`-Filtern in Python. (RLS nur als
  *zusätzliche* Schicht denkbar — siehe [G15](#g15--postgresql-row-level-security-defense-in-depth).)
- **Keine neuen globalen Singletons.** Bestehende, bewusst akzeptierte
  Ausnahme: das `protected_fields`-Modul-Singleton (fail-safe; per Tripwire-Test
  in `tests/security/test_protected_field_registry.py` festgeschrieben). Die
  geplante `PIIFieldRegistry` (G1) folgt demselben fail-safe-Muster.
- **Kein OIDC/SAML/SCIM** in der Auth-Hardening — gehört nach Phase 6.
- **Keine Feature-Flag-Plattform** — App-Sache (LaunchDarkly/Unleash); der
  tenant-spezifische Audit-Schalter (G5) genügt als Primitive.
- **Kein CAPTCHA / Bot-Management im Core** — gehört an Edge/WAF, nicht ins
  Framework.
- **Keine Secrets-Manager-Integration im Core** (Vault/ASM) — Deployment-Sache;
  12-Factor-Env bleibt die Grenze.
- **Kein DLP / ML-Anomalieerkennung** — kein Framework-Job, Over-Engineering.

### Bewusst gestrichen (waren in früheren Plänen, jetzt raus)

- **EventBus / Domain-Event-System** — Audit + Lifecycle-Hooks reichen.
- **JobQueue (Framework-eigen)** — jede Produktiv-App hat schon Celery/RQ/arq.
- **Observability / `/metrics` als Extension** — gehört, wenn überhaupt,
  direkt in den Core.
- **Webhooks-Extension** — Apps nutzen Svix/Hookdeck/eigene Lambda.
- **Jobs UI** — abhängig von der gestrichenen JobQueue.
- **Workflows / Approval-Engine** — eigenes Produkt; spezifisch bauen oder
  einkaufen.

---

## Abgeschlossen — Review-Härtung (R1–R17)

Befunde der externen Reviews (Runde 1 vom 2026-06-16, Runde 2 vom 2026-06-18),
umgesetzt + gemergt. Detail-Begründungen stehen in Git-Historie und CHANGELOG;
hier nur Kompaktnachweis. **Einziger Rest:** R14 → fortgeführt als
[G10](#g10--xss-härtung-csp--token-storage).

### Runde 1 (Review 2026-06-16)

| Prio | ID | Thema | Status |
|---|---|---|---|
| P0 | R1 | `search_path` auf der Request-Session | ✅ erledigt (CI-bestätigt 2026-06-18) |
| P0 | R2 | HTTP-PG-Isolationstest | ✅ erledigt (CI grün) |
| P0 | R3 | Doku-Zusagen zur Isolation korrigiert | ✅ erledigt |
| P1 | R4 | `test-postgres` als Build-Gate | ✅ erledigt |
| P1 | R5 | CHANGELOG.md + SECURITY.md | ✅ erledigt |
| P1 | R6 | Coverage messen + CI-Badge | ✅ erledigt |
| P2 | R7 | Verteilter Rate-Limiter (Redis-Extension) | ✅ erledigt |
| P2 | R8 | JWT-Härtung: aud/iss | ✅ erledigt |
| P2 | R9 | Tenant-Cache-Invalidierung (TTL + `invalidate_tenant`) | ✅ erledigt |
| P3 | R10 | Release-Workflow + Wheel-Smoke | ✅ erledigt |
| P3 | R11 | JS-Tests ausbauen | ✅ erledigt |
| P3 | R12 | Slug-Normalisierung | ✅ erledigt |

### Runde 2 (Analyse 2026-06-18)

| Prio | ID | Thema | Status |
|---|---|---|---|
| P0 | R13 | Roten `test-postgres`-Job geklärt (Test-Override-Annotation) | ✅ erledigt (CI grün) |
| P1 | R14 | XSS-Härtung: CSP + Token-Storage | 🟡 CSP-Knopf da; Nonce/Cookie offen → [G10](#g10--xss-härtung-csp--token-storage) |
| P2 | R15 | Login-Enumeration + Limiter-Keying (Konstante-Zeit + opt-in `(email,ip)`) | ✅ erledigt |
| P2 | R16 | Proxy-/Client-IP (`trusted_proxy_count`) | ✅ erledigt |
| P3 | R17 | Toten/redundanten Code aufräumen | ✅ erledigt (Provider-Doppel-Session-Merge als Folgeschritt offen) |

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
