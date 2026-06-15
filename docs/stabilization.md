# Stabilisierung / Härtung vor 1.0

Stand: 2026-06-15 · bezieht sich auf `adminfoundry` 0.1.0

Phasen 1–5 der [Roadmap](roadmap.md) sind abgeschlossen; Phase 6/7 sind
geparkt. Dieses Dokument sammelt die **Härtungsarbeit, die kein neues
Feature ist**, sondern den vorhandenen Kern vor einem `1.0`/„stable"
absichert. Reihenfolge nach Risiko × Nutzen.

> Keine neuen Produktfeatures. Jeder Punkt schließt eine Vertrauens- oder
> Konsolidierungslücke an bestehendem Code.

## Übersicht

| Prio | Thema | Kern | Aufwand |
|---|---|---|---|
| **P1** | [JS-Test-Harness](#p1-js-test-harness) | UI-Logik automatisiert testen | mittel |
| **P1** | [Contract-Snapshot-Tests](#p1-contract-snapshot-tests) | UI-Wahrheit gegen stille Breaking Changes | klein–mittel |
| **P1** | [`mypy` in CI](#p1-mypy-in-ci) | `py.typed`-Versprechen erzwingen | klein |
| **P2** | [A0.4 Field-Visibility](#p2-a04-field-visibility-konsolidieren-oder-festschreiben) | 3 Mechanismen vereinheitlichen oder bewusst dokumentieren | mittel |
| **P2** | [A0.5 User-Entkopplung](#p2-a05-user-entkopplung-rootauditcli) | „external auth" vollständig machen oder Grenze dokumentieren | mittel–groß |
| **P3** | [protected_fields-Singleton](#p3-protected_fields-singleton-revisit) | nur revisit bei Multi-App-Isolationsbedarf | mittel |
| **P3** | [Custom-Component-Injektion](#p3-custom-component-injektionspunkt) | echter JS-Renderer-Hook nur bei Bedarf | mittel |
| **—** | [Release-/Versionspolitik](#release--versionspolitik) | 1.0-Gate & SemVer-Zusage festlegen | klein |

---

## P1 — JS-Test-Harness

**Ziel:** Die UI-Logik (Form-/List-View, Conditional/Dependent Fields,
Inline-Edit, Column-Visibility, Sortierung) wird automatisiert getestet,
nicht nur per `node --check` + Python-Smoke.

**Befund:** ~17 JS-Module in [adminfoundry/ui/static/admin/](../adminfoundry/ui/static/admin/)
tragen mittlerweile echte Logik (Sichtbarkeitsregeln, Choice-Filter,
Dirty-Tracking, localStorage-Prefs). Aktuell gibt es **keine** JS-Test-
Infrastruktur — DOM-Verhalten ist ungetestet. Das ist die größte
Regressions­lücke des Pakets.

**Konkrete Änderung:** Vitest (oder node:test) + jsdom einführen; die
reinen Logikfunktionen exportierbar/testbar machen
(`buildFormBody`, `valueSatisfies`, Dependency-Narrowing,
`listPrefs`, Sort-Cycle, Edit-Dirty-Tracking). CI-Job ergänzen.

**Akzeptanzkriterien:**
- [ ] `npm test` läuft die JS-Suite; in CI als eigener Job.
- [ ] Conditional-/Dependent-Field-Auswertung, Sort-Cycle, Column-Visibility
  und Inline-Edit-Dirty-Tracking sind mit Unit-Tests gedeckt.
- [ ] Mindestens ein jsdom-Render-Test pro View-Modul (mountet ohne Fehler).

**Empfohlene Tests:** die in dieser Session per Wegwerf-`node -e` geprüften
Logikbausteine als echte Vitest-Fälle festschreiben.

---

## P1 — Contract-Snapshot-Tests

**Ziel:** Der Contract ist die einzige UI-Wahrheit — Breaking Changes daran
müssen bewusst werden, nicht still passieren.

**Befund:** `ModelContractMeta` ist über die Phasen stark gewachsen
(`fieldsets`, `form_layout`, `list_badges`, `date_hierarchy`,
`list_editable`, `dependency`, `condition`, `placeholder`, …). Es gibt
Feldtests, aber keinen Snapshot, der die **Gesamtform** einer Referenz-
Ressource einfriert.

**Konkrete Änderung:** Snapshot-Test über `build_model_contract()` für ein
repräsentatives Admin (mit Fieldsets, Relationen, Protected/Readonly,
Actions, Badges, Date-Hierarchy). `contract_version` als bewussten Gate
prüfen.

**Akzeptanzkriterien:**
- [ ] Ein Snapshot-Test schlägt fehl, wenn sich die Contract-Form ändert,
  ohne dass Snapshot **und** ggf. `contract_version` mitgezogen werden.
- [ ] Protected Fields tauchen im Snapshot nie als lesbar/writable auf.

**Empfohlene Tests:** `tests/contract/test_contract_snapshot.py`.

---

## P1 — `mypy` in CI

**Ziel:** Das ausgelieferte `py.typed`-Versprechen wird erzwungen.

**Befund:** Das Paket versendet `py.typed`, aber CI prüft nur `ruff`
(Lint + Format) und `pytest` — **kein** Typecheck. Typfehler an der
Public API können unbemerkt durchrutschen.

**Konkrete Änderung:** `mypy` (oder `pyright`) konfigurieren und als
CI-Job ergänzen; zunächst ggf. auf die Public-API-Module streng, intern
nachziehbar.

**Akzeptanzkriterien:**
- [ ] CI bricht bei Typfehlern in `adminfoundry/` ab.
- [ ] Baseline dokumentiert (welche Module bereits streng sind).

---

## P2 — A0.4 Field-Visibility konsolidieren oder festschreiben

**Ziel:** Eine nachvollziehbare Regel für Feld-Sichtbarkeit/Schreibbarkeit.

**Befund:** Drei Mechanismen koexistieren — `protected_fields`,
`readonly_fields`, `AdminPolicy.field_permission()`. Sie sind via
`FieldPermission.strictest()` UND-verknüpft, aber **nicht** zu einer
Pipeline vereinheitlicht (Roadmap A0.4).

**Entscheidung (deine):** entweder
(a) intern in *eine* Policy-Resolution übersetzen, oder
(b) den 3-Wege-Stand bewusst als „so gewollt" dokumentieren.

**Akzeptanzkriterien:**
- [ ] Es gibt eine dokumentierte, getestete Auflösungsreihenfolge.
- [ ] Tests decken Kombinationen (protected ∧ readonly ∧ policy) ab.

---

## P2 — A0.5 User-Entkopplung (Root/Audit/CLI)

**Ziel:** „external `user_mode`" ist keine Halbwahrheit mehr.

**Befund:** Direkte `User`-Modell-Imports außerhalb der Builtin-Provider
(`root/users.py`, `root/tenants.py`, `root/impersonation.py`,
`auth/router.py`, `audit/service.py`, `tenancy/bootstrap.py`,
`cli/main.py`) — in Phase 1.5 bewusst scope-reduced. CRUD/Contract laufen
extern, aber Root-/Audit-Pfade hängen am Builtin-User.

**Entscheidung (deine):** vollständige Provider-Entkopplung dieser Pfade
**oder** explizite Doku „Root/Audit/CLI sind Builtin-only".

**Akzeptanzkriterien:**
- [ ] Entweder: kein Root-/Audit-Pfad importiert das konkrete `User`-Modell,
  und ein Fake-External-Provider deckt sie in Tests ab.
- [ ] Oder: die Builtin-Kopplung ist in [docs/auth-architecture.md](auth-architecture.md)
  als bewusste Grenze dokumentiert.

---

## P3 — `protected_fields`-Singleton (revisit)

**Ziel:** Klarheit über App-Isolation der Security-Registry.

**Befund:** Modul-Level-Singleton; bewusst dokumentiert + fail-safe
(Teilen führt nur zu Über-Protektion, kein Leck). Bereits per Tripwire-Test
festgeschrieben.

**Status:** **akzeptiert.** Nur aufgreifen, wenn echte Per-App-Isolation
mehrerer Admin-Apps im selben Prozess gefordert wird — dann den Lesepfad
(`ModelAdmin.all_protected`, ~10 Stellen) über die Runtime führen, mit
voller Leak-Test-Suite.

---

## P3 — Custom-Component-Injektionspunkt

**Ziel:** Apps können eigene Feld-Renderer beistellen.

**Befund:** „Custom Components" ist heute nur ein Widget-Override
(eingebaute Widgets `select`/`textarea`). Ein echter custom JS-Renderer
braucht einen Lade-/Registry-Mechanismus, den die statische No-Build-UI
nicht hat.

**Status:** **bei Bedarf.** Nur umsetzen, wenn ein konkreter Use-Case
auftritt — dann eine `registerWidget`-Registry + Injektionspunkt (analog
Admin-Pages `js_module`) entwerfen.

---

## Release- / Versionspolitik

**Ziel:** Klare Stabilitätszusage statt implizitem „0.1.0".

**Konkrete Änderung:** 1.0-Gate definieren (P1-Punkte erfüllt + Doku ehrlich
+ Contract snapshot-getestet), SemVer-Politik für Public API und
`contract_version` festhalten.

**Akzeptanzkriterien:**
- [ ] Dokumentierte 1.0-Kriterien.
- [ ] Public-API- und Contract-Versionsregeln in README/Doku.

---

## Empfohlene Reihenfolge

**P1 zuerst** (JS-Harness → Contract-Snapshots → `mypy`) — sie schaffen das
Sicherheitsnetz, unter dem die P2-Architekturentscheidungen risikoarm
werden. **P3** bleibt bewusst optional/akzeptiert.
