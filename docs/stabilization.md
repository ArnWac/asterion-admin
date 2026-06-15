# Stabilisierung / Härtung vor 1.0

Stand: 2026-06-15 · bezieht sich auf `adminfoundry` 0.1.0

Phasen 1–5 der [Roadmap](roadmap.md) sind abgeschlossen; Phase 6/7 sind
geparkt. Dieses Dokument sammelt die **Härtungsarbeit, die kein neues
Feature ist**, sondern den vorhandenen Kern vor einem `1.0`/„stable"
absichert. Reihenfolge nach Risiko × Nutzen.

> Keine neuen Produktfeatures. Jeder Punkt schließt eine Vertrauens- oder
> Konsolidierungslücke an bestehendem Code.

## Übersicht

| Prio | Thema | Status |
|---|---|---|
| **P1** | [JS-Test-Harness](#p1-js-test-harness) | ✅ erledigt (Vitest + `logic.js` + CI-Job) |
| **P1** | [Contract-Snapshot-Tests](#p1-contract-snapshot-tests) | ✅ erledigt |
| **P1** | [`mypy` in CI](#p1-mypy-in-ci) | ✅ erledigt (gescopt auf Vertragsschicht) |
| **P2** | [A0.4 Field-Visibility](#p2-a04-field-visibility-konsolidieren-oder-festschreiben) | ✅ entschieden + dokumentiert |
| **P2** | [A0.5 User-Entkopplung](#p2-a05-user-entkopplung-rootauditcli) | ✅ entschieden (Grenze dokumentiert) |
| **P3** | [protected_fields-Singleton](#p3-protected_fields-singleton-revisit) | ✅ akzeptiert (Tripwire-Test) |
| **P3** | [Custom-Component-Injektion](#p3-custom-component-injektionspunkt) | ⏸️ aufgeschoben (bei Bedarf) |
| **—** | [Release-/Versionspolitik](#release--versionspolitik) | ✅ festgelegt (siehe unten) |
| **Follow-up** | [mypy aufs Gesamtpaket](#follow-up-mypy-aufs-gesamtpaket) | offen, kein 1.0-Blocker |
| **Follow-up** | [ruff Lint/Format-Drift](#follow-up-ruff-lintformat-drift-ci-lint-vorbestehend-rot) | ✅ erledigt (gepinnt + bereinigt) |

---

## P1 — JS-Test-Harness ✅

**Ziel:** Die UI-Logik wird automatisiert getestet, nicht nur per
`node --check` + Python-Smoke.

**Umgesetzt:** Die reine, DOM-freie Logik (Conditional-/Dependent-Field-
Auswertung, Sort-Cycle, Date-Hierarchy-Compose, Inline-Edit-Dirty-Tracking,
loose-equal) wurde in [adminfoundry/ui/static/admin/logic.js](../adminfoundry/ui/static/admin/logic.js)
extrahiert; `form.js`/`list.js` importieren sie (Duplikate entfernt).
Vitest-Suite unter [tests/js/logic.test.js](../tests/js/logic.test.js)
(14 Fälle) + CI-Job `js`.

**Offen (bewusst, kein Blocker):** DOM-/jsdom-Render-Tests der View-Module.
Die *Entscheidungslogik* ist jetzt gedeckt; reines DOM-Mounting bleibt per
Python-Smoke abgesichert.

---

## P1 — Contract-Snapshot-Tests ✅

**Umgesetzt:** [tests/contract/test_contract_snapshot.py](../tests/contract/test_contract_snapshot.py)
friert die volle `build_model_contract()`-Form für ein repräsentatives
Admin ein (Fieldsets, Badges, Date-Hierarchy, list_editable, Widget-
Override, Readonly, Protected, Actions), pinnt `contract_version` und
re-asserted, dass ein Protected-Feld nirgends im Contract auftaucht.

---

## P1 — `mypy` in CI ✅

**Umgesetzt:** `typecheck`-Job + `[tool.mypy]` in `pyproject.toml`,
gescopt auf die typisierte Vertragsschicht (`providers/` +
`core/config.py`) mit `follow_imports = silent`. Der eine reale Nit
(`from_env(**overrides: Any)`) ist gefixt.

**Bewusste Scope-Entscheidung:** Das Gesamtpaket trägt ~80 Framework-
Typing-Nits (SQLAlchemy/FastAPI-Generics, `type[ModelAdmin]`-vs-Instanz).
Ein voller Sweep wäre riskanter Churn ohne Laufzeitnutzen → als Follow-up
geführt (s. u.), kein 1.0-Blocker.

---

## P2 — A0.4 Field-Visibility ✅

**Entscheidung:** **festschreiben** — die drei Knöpfe (`protected_fields`,
`readonly_fields`, `AdminPolicy.field_permission`) sind *Eingaben* in
**einen** Resolver `FieldPermission.strictest()` (`WRITE < READ < HIDDEN`);
eine Policy kann nur verschärfen, nie lockern. Das ist die von A0.4
geforderte Konsolidierung — nicht drei konkurrierende Pfade. Dokumentiert
in [security.md](security.md#field-protection), getestet in
`tests/crud/test_field_permission_resolution.py`.

---

## P2 — A0.5 User-Entkopplung (Root/Audit/CLI) ✅

**Entscheidung:** **Grenze dokumentieren** statt riskanter Voll-Refactor.
Externes `user_mode` deckt Auth/CRUD/Contract vollständig über die Provider
ab; `root/*`, `audit/service.py`, `tenancy/bootstrap.py`, `cli/main.py`
bleiben bewusst Builtin-`User`-gekoppelt. Als ausdrückliche Grenze
festgehalten in [auth-architecture.md](auth-architecture.md#boundary-where-external-auth-stops-roadmap-a05)
und [security.md](security.md#known-limitations-be-honest).

**Offen (Follow-up, kein 1.0-Blocker):** vollständige Provider-Entkopplung
dieser Pfade + Fake-External-Provider-Tests — erst wenn ein Setup ohne
Builtin-`User` wirklich gebraucht wird.

---

## P3 — `protected_fields`-Singleton ✅ akzeptiert

Modul-Level-Singleton, bewusst + fail-safe (Teilen führt nur zu Über-
Protektion, kein Leck), per Tripwire-Test in
`tests/security/test_protected_field_registry.py` festgeschrieben.
**Status: akzeptiert.** Nur aufgreifen, wenn echte Per-App-Isolation
mehrerer Admin-Apps im selben Prozess gefordert wird.

---

## P3 — Custom-Component-Injektionspunkt ⏸️

„Custom Components" ist heute der Widget-Override (`ModelAdmin.widgets` →
eingebaute Widgets `select`/`textarea`). Ein echter custom JS-Renderer
braucht einen Lade-/Registry-Mechanismus, den die statische No-Build-UI
nicht hat. **Status: aufgeschoben** — nur bei konkretem Use-Case, dann eine
`registerWidget`-Registry + Injektionspunkt (analog Admin-Pages
`js_module`) entwerfen.

---

## Release- / Versionspolitik ✅

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
- [x] P1 erfüllt (JS-Harness, Contract-Snapshots, mypy-CI auf Vertragsschicht)
- [x] P2 entschieden + dokumentiert (A0.4, A0.5)
- [x] Doku ehrlich (keine veralteten „Known limitations")
- [ ] mypy aufs Gesamtpaket grün (Follow-up) — *empfohlen, nicht zwingend*
- [ ] Changelog/Release-Notes-Prozess etabliert

Sobald die letzten zwei Häkchen optional abgearbeitet sind, ist ein
`1.0` vertretbar.

---

## Follow-up — `mypy` aufs Gesamtpaket

**Ziel:** mypy von der Vertragsschicht auf `adminfoundry/` insgesamt
ausweiten.

**Befund:** ~80 Fehler, überwiegend Framework-Typing-Nits
(`var-annotated` an SQLAlchemy-Statements, `type[ModelAdmin]`-vs-Instanz in
CRUD/Contract-Signaturen, FastAPI-`lifespan`-Generics). Mehrheitlich echte,
aber benigne Signaturinkonsistenzen.

**Vorgehen wenn aufgegriffen:** kategorienweise abarbeiten
(`var-annotated` zuerst — billig; dann `type[ModelAdmin]` → `ModelAdmin`
in den CRUD/Contract-Signaturen; FastAPI-Quirks gezielt `# type: ignore`
mit Begründung), dann `[tool.mypy] files` aufs Paket erweitern.

**Status:** offen, **kein 1.0-Blocker**.

---

## Follow-up — ruff Lint/Format-Drift (CI-Lint vorbestehend rot) ✅ erledigt

**Erledigt:** ruff auf `>=0.15,<0.16` gepinnt; `ruff check . --fix` (94
Autofixes) + `ruff format .` (70 Dateien); `RUF012` auch für
`adminfoundry/admin/**` ignoriert (deklaratives `InlineAdmin`-Muster);
`UP042` an `FieldPermission(str, Enum)` per begründetem `# noqa` belassen
(StrEnum würde `str()`-Semantik ändern); `RUF002` (`×`→`x`), `B904`
(`raise … from exc`) und ein `F841` in Tests gefixt. `ruff check` +
`ruff format --check` grün; volle Suite (1390) weiterhin grün.

**Ursprünglicher Befund:** Die ruff-Config selektiert `RUF059` (erst ab ruff ≥ 0.11), aber
das Paket ist unter keiner RUF059-fähigen Version sauber: ruff 0.11.13
meldet ~110 Lint-Treffer (überwiegend `UP037` Quotes-in-Annotations, `F401`,
`I001`, `E402`, `RUF012`), und der 0.15-Formatter würde ~70 Dateien
umformatieren. Da `[project.optional-dependencies] dev` ruff ungepinnt
(`>=0.6.0`) lässt, läuft CI auf der jeweils neuesten ruff → die Lint-Stufe
ist faktisch rot, unabhängig von der Stabilisierungsrunde.

**Vorgehen (eigener, reviewbarer Change):**
1. ruff pinnen (`ruff>=0.15,<0.16`) für deterministisches CI.
2. `ruff check . --fix` (92 Autofixes: UP037/F401/I001/RUF100) + `ruff format .`.
3. Intentionale Muster per Config begründen statt umbauen:
   `RUF012` (deklarative `ModelAdmin`/`InlineAdmin` Mutable-Class-Attrs,
   in `__init_subclass__` zurückgesetzt) → `lint.ignore`; `E402`
   (bewusste Lazy-Imports) → gezielte `# noqa`.
4. Rest manuell (`B904`, `F841`, `UP042`).
5. Voller Suite-Lauf + `ruff check`/`format --check` grün.

**Status:** offen, **kein 1.0-Blocker für die Features**, aber Voraussetzung
für eine grüne CI-Lint-Stufe. Bewusst als eigener Change gehalten — ein
100+-Dateien-Autofix/Reformat gehört nicht in den Stabilisierungs-Branch.

## Erledigt in dieser Härtungsrunde

P1 (JS-Harness, Contract-Snapshots, mypy-CI) vollständig; P2 (A0.4/A0.5)
entschieden + dokumentiert; P3 + Release-Politik festgeschrieben. Offen
bleibt bewusst nur der mypy-Gesamtpaket-Follow-up und der optionale
custom-Renderer-Injektionspunkt.
