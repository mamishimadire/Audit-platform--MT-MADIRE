import uuid

from app.schemas.common import OrmModel


class PermissionOut(OrmModel):
    permission_id: uuid.UUID
    permission_name: str
    description: str | None


class RoleOut(OrmModel):
    role_id: uuid.UUID
    role_name: str
    description: str | None
    role_scope: str
    # Derived from role_scope, not a stored column — 'platform' means this
    # role belongs to MT Audit's own staff (INTERNAL), 'client' means it
    # belongs to a client organization's own users (EXTERNAL). Surfaced
    # under this name because "platform/client" reads ambiguously in the
    # UI, but the underlying enforcement (app.services.user_service,
    # user_organization_scope) still keys off role_scope.
    account_classification: str
    permissions: list[str] = []


class SodWorkflowOut(OrmModel):
    """Describes one maker-checker workflow actually enforced in the
    service layer (see app/services/*_service.py) — kept here, next to
    the roles/permissions reference data, so 'who can approve what' and
    'who checks whose work' are answerable from one page instead of
    reading source code."""

    action: str
    states: list[str]
    maker: str
    checker: str
    enforcement: str
    mandatory: bool


class RoleAssign(OrmModel):
    role_name: str
