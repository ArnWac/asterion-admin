---
name: V1 Release — Offene Punkte
description: Aufgaben die vor einem production-ready V1 Release erledigt werden müssen
type: project
---

Stand 2026-05-05 — 320/320 Tests grün, Codebase bereinigt.

Verbleibende Punkte vor V1:

1. **Alembic-Initialmigration erzeugen** — `migrations/shared/versions/` und `migrations/tenant/versions/` sind leer. `alembic revision --autogenerate -m "initial"` für beide Configs ausführen.

2. **Token-Blacklist persistieren** — `coreAdmin_api/token_blacklist.py` ist In-Memory. Ersatz: DB-Tabelle `revoked_tokens(jti, exp)` via SQLAlchemy. Überlebt Neustarts, funktioniert mit mehreren Workers. Details in `project_token_blacklist.md`.

3. **Rate-Limiter persistieren** — `coreAdmin_api/middleware/rate_limit.py` nutzt In-Memory `_SlidingWindowLimiter`. Bei mehreren Prozessen/Instanzen kein shared State. Ersatz: Redis oder DB-basiert.

4. **SECRET_KEY-Default absichern** — `settings.py` hat `SECRET_KEY = "change-me-in-production"` als Fallback. Startup-Check einbauen der bei diesem Wert eine Warnung oder Exception wirft.

**How to apply:** Diese Punkte bei der nächsten Arbeitssession am Projekt angehen, bevor ein erstes Release gebaut wird.
