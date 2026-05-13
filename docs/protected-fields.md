# Protected Fields

## GLOBALLY_PROTECTED

Defined in `adminfoundry/admin/model_admin.py`. These fields are stripped from all admin schemas unconditionally — list, detail, create, update, contract, and UI payloads.

```python
GLOBALLY_PROTECTED: frozenset[str] = frozenset({
    "hashed_password",
    "password",
    "pin_hash",
    "shared_secret",
    "tenant_salt",
    "setup_code",
    "qr_bootstrap_token",
})
```

Do not add secrets to `ModelAdmin.protected_fields` when they should be globally protected — add them to `GLOBALLY_PROTECTED` instead so all models benefit.

## Per-Admin Protected Fields

```python
class UserAdmin(ModelAdmin):
    protected_fields = ["tenant_id"]  # merged with GLOBALLY_PROTECTED at schema build time
```

These are merged with `GLOBALLY_PROTECTED` in `schema_builder.py`. The full effective set is:

```python
admin.all_protected  # → GLOBALLY_PROTECTED | set(protected_fields)
```

## How Enforcement Works

`schema_builder.py` generates Pydantic schemas dynamically from SQLAlchemy introspection. Any field in `all_protected` is excluded from both read and write schemas. There is no way for a client to request or mutate a protected field via the admin API — the field does not appear in the schema at all.

`GLOBALLY_PROTECTED` is checked at the field iteration level in `schema_builder.py` lines 67–125.

## Adding a New Protected Field

1. If it applies to **all models** — add to `GLOBALLY_PROTECTED` in `model_admin.py`.
2. If it applies to **one model** — add to that `ModelAdmin.protected_fields`.

Never rely on the UI hiding the field — enforce it at the API/schema boundary.
