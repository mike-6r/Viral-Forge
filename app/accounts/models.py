import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base
from app.common.types import UUIDTimestampMixin
from app.content.models import Platform


class RoleName(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    EDITOR = "EDITOR"
    REVIEWER = "REVIEWER"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"
    SYSTEM = "SYSTEM"


class User(UUIDTimestampMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)


class Role(UUIDTimestampMixin, Base):
    __tablename__ = "roles"
    name: Mapped[RoleName] = mapped_column(Enum(RoleName), unique=True)


class UserRole(UUIDTimestampMixin, Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id"), index=True
    )
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)


class PlatformAccount(UUIDTimestampMixin, Base):
    __tablename__ = "platform_accounts"
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    external_account_id: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    __table_args__ = (
        UniqueConstraint("platform", "external_account_id", name="uq_platform_account"),
    )
