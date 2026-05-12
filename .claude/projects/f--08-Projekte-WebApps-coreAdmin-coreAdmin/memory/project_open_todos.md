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

4. **`_check_model_access` DB-Permissions** — `_check_model_access` in `router.py` prüft noch nicht die DB-Permissions (`RolePermission`-Tabelle). Nur `effective_model_caps()` tut es. Muss noch in die Route-Ebene integriert werden.

## UI / UX

5. **Inline-Relations UI** — `inline_fields` und Contract sind implementiert. Offen: Volle UI-Darstellung im admin.js (Inline-Formular rendern, Speichern).

## Signals / Events

6. **Signals `post_login` / `post_logout`** — Core-Signals (`post_create`, `post_update`, `pre_delete`, `post_delete`) sind implementiert und werden im Admin-Router gefeuert. Webhooks (`adminfoundry.webhooks`) ebenfalls implementiert (HMAC-SHA256, httpx, async). **Offen:** `post_login` und `post_logout` werden im Auth-Router noch nicht gefeuert.

## Soft-Delete / Recycle Bin

7. **Soft-Delete** — `deleted_at`-Flag statt Hard-Deletes. Separate Trash-Ansicht pro Model, Restore per Klick. Opt-in per `ModelAdmin(soft_delete=True)`. Alle List/Detail-Queries auto-filtern `WHERE deleted_at IS NULL`.

   **DSGVO-Pflichtanforderungen:**
   - Soft-delete erfüllt **nicht** das Recht auf Löschung (Art. 17 DSGVO) — personenbezogene Daten müssen bei Löschungsantrag hart gelöscht oder anonymisiert werden.
   - Zwingend: separater **Hard-Delete**-Button (Superadmin-only) für GDPR-Anfragen — löscht Record permanent + anonymisiert betroffene Audit-Log-Einträge.
   - **Anonymize-on-request**: PII-Felder (E-Mail, Name etc.) in soft-deleted Records durch Platzhalter ersetzen (`anonymized@gdpr.invalid`).
   - **Retention Policy**: `soft_delete_retention_days` auf ModelAdmin — konfigurierbare automatische Bereinigung nach X Tagen.
   - **Audit-Log**: GDPR-Delete-Workflow sollte E-Mail/Namen im Audit-Log durch pseudonymisierte ID ersetzen.

## Erweiterbarkeit / Public Package

8. **Pluggable User-Model (Option B)** — `create_adminfoundry(app, config=CoreAdminConfig(user_model=MyUser))` ermöglichen. adminfoundry prüft übergebenes Model gegen ein `UserProtocol` (Pflichtfelder: `email`, `is_active`, `is_superadmin`, `id`). Erst relevant bei öffentlicher Veröffentlichung.
