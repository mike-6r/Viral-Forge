import os
import uuid

os.environ["VIRALFORGE_ENVIRONMENT"] = "test"
os.environ["VIRALFORGE_ENABLE_DEVELOPMENT_ACTOR"] = "true"
os.environ["VIRALFORGE_TRUSTED_HOSTS"] = "testserver"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.accounts.models  # noqa: F401
import app.analytics.models  # noqa: F401
import app.audit.models  # noqa: F401
import app.brands.models  # noqa: F401
import app.content.models  # noqa: F401
import app.content_packages.models  # noqa: F401
import app.discord_business.models  # noqa: F401
import app.ingestion.models  # noqa: F401
import app.media_preview.models  # noqa: F401
import app.moderation.models  # noqa: F401
import app.production.models  # noqa: F401
import app.publishing.models  # noqa: F401
import app.ranking.models  # noqa: F401
import app.review.models  # noqa: F401
import app.rights.models  # noqa: F401
import app.sources.models  # noqa: F401
from app.accounts.models import Role, RoleName, User, UserRole
from app.api import create_app
from app.common.config import get_settings
from app.common.db import Base, get_session

DEV_ACTOR_ID = uuid.UUID("a1111111-1111-1111-1111-111111111111")


@pytest.fixture()
def session():  # type: ignore[no-untyped-def]
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = User(
            id=DEV_ACTOR_ID, email="dev-admin@example.test", display_name="Development Admin"
        )
        role = Role(name=RoleName.ADMIN)
        db.add_all([user, role])
        db.flush()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        db.commit()
        yield db
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(session):  # type: ignore[no-untyped-def]
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
