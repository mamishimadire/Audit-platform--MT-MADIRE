import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin, uuid_pk


class User(Base, TimestampMixin):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = uuid_pk("user_id")
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE")
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # pending_approval -> (approved) pending -> active, or -> rejected.
    # A brand-new user starts at pending_approval and cannot log in or
    # even activate their own account until a DIFFERENT authorized user
    # approves them (see user_service.approve_pending_user) — 'pending'
    # itself keeps its original meaning of "approved, just waiting on
    # the person to set their own password."
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending_approval")
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Deactivation/removal — same dual-control shape as device revocation/
    # deletion (see device_service.py). active -> pending_deactivation ->
    # inactive, or active/inactive/pending/locked -> pending_removal ->
    # removed (a soft delete; the row and its history stay, it just can
    # never log in again). Both identity-checked: requested_by !=
    # whoever approves, in user_service.approve_deactivation/approve_removal.
    deactivation_requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    deactivation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deactivation_reason: Mapped[str | None] = mapped_column(Text)
    removal_requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    removal_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removal_reason: Mapped[str | None] = mapped_column(Text)
    # What status to restore if the removal request is rejected — a user
    # has no self-correcting heartbeat like a device does, so this has to
    # be remembered explicitly rather than falling back to a fixed value.
    removal_prior_status: Mapped[str | None] = mapped_column(String(20))
    # Visible/copyable in the UI until this user activates their account and
    # sets their own password — cleared the moment that happens (see
    # auth_service.activate_pending_user). Explicit product decision, not an
    # oversight: see migration 0010.
    temporary_password_plaintext: Mapped[str | None] = mapped_column(String(255))
    # When the CURRENT password_hash was set — activation and every
    # self-service change (auth_service.activate_pending_user/change_password)
    # both update this. Drives the 30-day rotation policy in
    # auth_service.PASSWORD_ROTATION_DAYS: /auth/me reports must_change_
    # password once a password is that old, and ProtectedRoute (every page
    # in the app sits behind it) redirects to /profile until it's changed,
    # with a reminder shown in the days leading up to it.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    role_id: Mapped[uuid.UUID] = uuid_pk("role_id")
    role_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # 'platform' = our own staff (System Owner side); 'client' = a specific
    # client organization's own users. A platform user can only hold
    # platform-scoped roles, a client user (organization_id set) only
    # client-scoped roles — see migration 0005 for the full rationale.
    role_scope: Mapped[str] = mapped_column(String(20), nullable=False, server_default="client")


class Permission(Base):
    __tablename__ = "permissions"

    permission_id: Mapped[uuid.UUID] = uuid_pk("permission_id")
    permission_name: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)

    user_role_id: Mapped[uuid.UUID] = uuid_pk("user_role_id")
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id"),)

    role_permission_id: Mapped[uuid.UUID] = uuid_pk("role_permission_id")
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.role_id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("permissions.permission_id", ondelete="CASCADE"), nullable=False
    )


class UserOrganizationScope(Base):
    """
    Which client organizations a platform-level user (organization_id IS
    NULL on `users`) may access. A client user never needs a row here —
    their own `organization_id` is already their one and only scope.
    A user holding the 'Platform Super Admin' role bypasses this table
    entirely (see app.services.tenant_scope_service); every other
    platform role is scoped to exactly the organizations listed here,
    which is what lets one auditor be assigned to 30 of 10,000 clients
    without touching the other 9,970.
    """

    __tablename__ = "user_organization_scope"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)

    scope_id: Mapped[uuid.UUID] = uuid_pk("scope_id")
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"

    session_id: Mapped[uuid.UUID] = uuid_pk("session_id")
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    login_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    logout_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
