from __future__ import annotations
from pydantic import BaseModel


class ClientConfigResponse(BaseModel):
    """
    Bootstrap configuration for external renderer clients (e.g. Flutter).

    Clients use this endpoint to discover the active contract version,
    renderer support matrix, endpoint map, and deprecation policy without
    requiring ORM inspection or built-in-UI assumptions.

    Breaking-change policy: major contract_version bump signals a
    backward-incompatible change; additive fields do not change the version.
    """
    contract_version: str
    renderer_id: str
    renderer_version: str
    supported_features: dict[str, bool]
    endpoints: dict[str, str]
    breaking_change_policy: str
    additive_change_policy: str
