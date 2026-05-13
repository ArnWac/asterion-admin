"""TenantStrategy Protocol — implement to plug in custom tenancy backends."""
from __future__ import annotations

from typing import AsyncGenerator, Protocol, runtime_checkable

from adminfoundry.tenancy.context import TenantContext


@runtime_checkable
class TenantStrategy(Protocol):
    async def resolve_tenant(self, request) -> TenantContext | None: ...
    async def get_session(self, schema_name: str) -> AsyncGenerator: ...
    def apply_query_scope(self, stmt, tenant: TenantContext): ...
    def assert_write_allowed(self, tenant: TenantContext) -> None: ...
