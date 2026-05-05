from pydantic import BaseModel


class NavItem(BaseModel):
    model: str
    label: str
    label_plural: str
    url: str            # canonical API list URL for this resource
    tenant_scoped: bool


class NavigationResponse(BaseModel):
    items: list[NavItem]
