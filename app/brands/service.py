"""Safe workspace/brand selection and membership checks."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.models import RoleName
from app.brands.models import Brand, BrandMembership, ContentProfile, Workspace

LEGACY_WORKSPACE_ID = uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510101")
LEGACY_BRAND_ID = uuid.UUID("4e6768ac-d9bc-4eac-8f30-e73ffc510102")


class BrandError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def ensure_legacy_brand(session: Session) -> Brand:
    workspace = session.get(Workspace, LEGACY_WORKSPACE_ID)
    if workspace is None:
        workspace = Workspace(id=LEGACY_WORKSPACE_ID, name="Legacy Workspace", slug="legacy", is_legacy=True)
        session.add(workspace)
        session.flush()
    brand = session.get(Brand, LEGACY_BRAND_ID)
    if brand is None:
        brand = Brand(id=LEGACY_BRAND_ID, workspace_id=workspace.id, name="Legacy Brand", slug="legacy", is_legacy=True)
        session.add(brand)
        session.flush()
    if session.scalar(select(ContentProfile).where(ContentProfile.brand_id == brand.id)) is None:
        session.add(ContentProfile(brand_id=brand.id, niche_name="legacy", timezone=workspace.timezone))
    return brand


def brand_for_actor(session: Session, actor_id: uuid.UUID, roles: frozenset[RoleName], brand_id: uuid.UUID | None = None) -> Brand:
    if brand_id is not None:
        brand = session.get(Brand, brand_id)
        if brand is None and brand_id == LEGACY_BRAND_ID:
            brand = ensure_legacy_brand(session)
        if brand is None or not brand.is_active:
            raise BrandError("BRAND_NOT_FOUND", "brand was not found or is inactive")
        if RoleName.OWNER not in roles and RoleName.ADMIN not in roles:
            membership = session.scalar(select(BrandMembership).where(BrandMembership.brand_id == brand.id, BrandMembership.user_id == actor_id))
            if membership is None:
                raise BrandError("BRAND_ACCESS_DENIED", "actor is not a member of this brand")
        return brand
    membership = session.scalar(
        select(BrandMembership)
        .join(Brand, Brand.id == BrandMembership.brand_id)
        .where(BrandMembership.user_id == actor_id, Brand.is_active, BrandMembership.is_default)
        .order_by(BrandMembership.created_at)
    )
    if membership is not None:
        brand = session.get(Brand, membership.brand_id)
        assert brand is not None
        return brand
    if RoleName.OWNER in roles or RoleName.ADMIN in roles:
        return ensure_legacy_brand(session)
    membership = session.scalar(select(BrandMembership).where(BrandMembership.user_id == actor_id).order_by(BrandMembership.created_at))
    if membership is None:
        raise BrandError("BRAND_ACCESS_DENIED", "actor has no accessible brand")
    brand = session.get(Brand, membership.brand_id)
    assert brand is not None
    return brand


def assert_brand_access(session: Session, actor_id: uuid.UUID, roles: frozenset[RoleName], brand_id: uuid.UUID) -> Brand:
    return brand_for_actor(session, actor_id, roles, brand_id)


def set_default_brand(session: Session, actor_id: uuid.UUID, brand_id: uuid.UUID) -> BrandMembership:
    membership = session.scalar(select(BrandMembership).where(BrandMembership.user_id == actor_id, BrandMembership.brand_id == brand_id))
    if membership is None:
        raise BrandError("BRAND_ACCESS_DENIED", "actor is not a member of this brand")
    for row in session.scalars(select(BrandMembership).where(BrandMembership.user_id == actor_id)):
        row.is_default = row.id == membership.id
    session.commit()
    return membership
