import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///basic_multi_tenant.db")
os.environ.setdefault("MULTI_TENANT", "true")
os.environ.setdefault("TENANT_RESOLUTION_STRATEGY", "subdomain")
