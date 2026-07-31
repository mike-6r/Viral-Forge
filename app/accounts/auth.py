import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.models import Role, RoleName, User, UserRole
from app.common.config import Environment, get_settings
from app.common.db import get_session


@dataclass(frozen=True)
class Actor:
    id: uuid.UUID
    roles: frozenset[RoleName]


def actor_can(actor: Actor, *roles: RoleName) -> bool:
    return bool(actor.roles.intersection(roles))


def require_role(actor: Actor, *roles: RoleName) -> None:
    if not actor_can(actor, *roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")


def development_actor(
    x_development_actor: str | None = Header(default=None),
    session: Session = Depends(get_session),  # noqa: B008
) -> Actor:
    settings = get_settings()
    if settings.environment is Environment.PRODUCTION or not settings.enable_development_actor:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication is not configured"
        )
    if not x_development_actor:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Development-Actor is required in development",
        )
    try:
        actor_id = uuid.UUID(x_development_actor)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid development actor UUID"
        ) from error
    user = session.get(User, actor_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="development actor must be an active persisted user",
        )
    role_names = session.scalars(
        select(Role.name)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == actor_id)
    ).all()
    if not role_names:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="development actor has no assigned role"
        )
    return Actor(id=actor_id, roles=frozenset(role_names))
