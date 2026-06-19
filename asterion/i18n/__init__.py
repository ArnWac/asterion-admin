"""Server-side internationalization for emails, API messages, and CLI output.

The JS i18n bundle (admin.i18n.js) handles browser-side admin UI text.
This module handles server-side strings: email subjects/bodies, API error
messages, and CLI output.

Usage::

    from asterion.i18n import t, add_catalog

    # Always pass lang explicitly (falls back to "en"):
    print(t("email_password_reset_subject", lang="de"))
    # → "Passwort zurücksetzen"

    # Interpolation:
    print(t("email_welcome_subject", lang="de", title="SimpleTimes"))
    # → "Willkommen bei SimpleTimes"

    # Add custom strings for your app:
    add_catalog("de", {"my_key": "Mein Text"})
"""

from __future__ import annotations

_CATALOGS: dict[str, dict[str, str]] = {
    "en": {
        # Errors
        "error_not_found": "Not found",
        "error_unauthorized": "Unauthorized",
        "error_forbidden": "Forbidden",
        "error_conflict": "Conflict",
        "error_validation": "Validation error",
        "error_internal": "Internal server error",
        # Password reset email
        "email_password_reset_subject": "Password reset",
        "email_password_reset_body": (
            "You requested a password reset. Click the link below to set a new password:\n\n"
            "{url}\n\n"
            "This link expires in {minutes} minutes. If you did not request this, ignore this email."
        ),
        # Welcome email
        "email_welcome_subject": "Welcome to {title}",
        "email_welcome_body": (
            "Hello {name},\n\nYour account has been created at {title}.\n\nEmail: {email}"
        ),
        # Audit
        "audit_created": "Created",
        "audit_updated": "Updated",
        "audit_deleted": "Deleted",
    },
    "de": {
        # Errors
        "error_not_found": "Nicht gefunden",
        "error_unauthorized": "Nicht autorisiert",
        "error_forbidden": "Zugriff verweigert",
        "error_conflict": "Konflikt",
        "error_validation": "Validierungsfehler",
        "error_internal": "Interner Serverfehler",
        # Password reset email
        "email_password_reset_subject": "Passwort zurücksetzen",
        "email_password_reset_body": (
            "Sie haben eine Passwortzurücksetzung angefordert. "
            "Klicken Sie auf den folgenden Link, um ein neues Passwort festzulegen:\n\n"
            "{url}\n\n"
            "Dieser Link läuft in {minutes} Minuten ab. "
            "Falls Sie dies nicht angefordert haben, ignorieren Sie diese E-Mail."
        ),
        # Welcome email
        "email_welcome_subject": "Willkommen bei {title}",
        "email_welcome_body": (
            "Hallo {name},\n\nIhr Konto wurde bei {title} erstellt.\n\nE-Mail: {email}"
        ),
        # Audit
        "audit_created": "Erstellt",
        "audit_updated": "Aktualisiert",
        "audit_deleted": "Gelöscht",
    },
    "fr": {
        "email_password_reset_subject": "Réinitialisation du mot de passe",
        "email_password_reset_body": (
            "Vous avez demandé une réinitialisation du mot de passe. "
            "Cliquez sur le lien ci-dessous :\n\n{url}\n\n"
            "Ce lien expire dans {minutes} minutes."
        ),
        "email_welcome_subject": "Bienvenue sur {title}",
        "email_welcome_body": "Bonjour {name},\n\nVotre compte a été créé sur {title}.\n\nEmail : {email}",
    },
    "es": {
        "email_password_reset_subject": "Restablecer contraseña",
        "email_password_reset_body": (
            "Ha solicitado restablecer su contraseña. Haga clic en el enlace:\n\n{url}\n\n"
            "Este enlace caduca en {minutes} minutos."
        ),
        "email_welcome_subject": "Bienvenido a {title}",
        "email_welcome_body": "Hola {name},\n\nSu cuenta ha sido creada en {title}.\n\nCorreo: {email}",
    },
    "pt": {
        "email_password_reset_subject": "Redefinir senha",
        "email_password_reset_body": (
            "Você solicitou a redefinição de senha. Clique no link:\n\n{url}\n\n"
            "Este link expira em {minutes} minutos."
        ),
        "email_welcome_subject": "Bem-vindo ao {title}",
        "email_welcome_body": "Olá {name},\n\nSua conta foi criada em {title}.\n\nEmail: {email}",
    },
}


def t(key: str, lang: str | None = None, **vars: str) -> str:
    """Translate *key* to *lang* (falls back to English, then the key itself)."""
    en = _CATALOGS.get("en") or {}
    catalog = _CATALOGS.get(lang or "en") or en
    text = catalog.get(key) or en.get(key, key)
    if vars:
        for k, v in vars.items():
            text = text.replace(f"{{{k}}}", str(v))
    return text


def add_catalog(lang: str, strings: dict[str, str]) -> None:
    """Merge additional translation strings into an existing or new language catalog."""
    if lang not in _CATALOGS:
        _CATALOGS[lang] = {}
    _CATALOGS[lang].update(strings)


def supported_languages() -> list[str]:
    return list(_CATALOGS.keys())
