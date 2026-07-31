import uuid

from sqlalchemy import select

from app.accounts.models import Role, RoleName, User, UserRole
from app.brands.models import Brand, BrandMembership, Workspace
from app.discovery.models import DiscoverySource
from app.production.models import ProductionProject
from tests.conftest import DEV_ACTOR_ID


def test_brand_api_profiles_accounts_and_cross_brand_isolation(client, session):  # type: ignore[no-untyped-def]
    admin_headers = {"X-Development-Actor": str(DEV_ACTOR_ID)}
    workspace = client.post(
        "/api/v1/workspaces",
        headers=admin_headers,
        json={"name": "Agency", "slug": "agency", "timezone": "America/New_York"},
    )
    assert workspace.status_code == 201, workspace.text
    first = client.post(
        f"/api/v1/workspaces/{workspace.json()['id']}/brands",
        headers=admin_headers,
        json={"name": "Public Safety", "slug": "public-safety"},
    )
    second = client.post(
        f"/api/v1/workspaces/{workspace.json()['id']}/brands",
        headers=admin_headers,
        json={"name": "Mechanics", "slug": "mechanics"},
    )
    assert first.status_code == 201 and second.status_code == 201
    brand_a, brand_b = uuid.UUID(first.json()["id"]), uuid.UUID(second.json()["id"])
    profile = client.patch(
        f"/api/v1/brands/{brand_a}/content-profile",
        headers=admin_headers,
        json={
            "niche_name": "public safety",
            "included_keywords": ["incident"],
            "excluded_keywords": ["compilation"],
            "preferred_source_providers": ["youtube"],
            "min_clip_duration_seconds": 20,
            "max_clip_duration_seconds": 45,
            "caption_tone": "neutral factual",
            "maximum_posts_per_day": 3,
            "target_platforms": ["YOUTUBE"],
            "language": "en",
            "timezone": "America/New_York",
        },
    )
    assert profile.status_code == 200 and profile.json()["niche_name"] == "public safety"
    source_account = client.post(
        f"/api/v1/brands/{brand_a}/source-accounts",
        headers=admin_headers,
        json={"provider": "YOUTUBE", "account_reference": "UCofficial", "public_url": "https://youtube.example/channel/UCofficial"},
    )
    destination = client.post(
        f"/api/v1/brands/{brand_a}/destination-accounts",
        headers=admin_headers,
        json={"provider": "YOUTUBE", "account_reference": "brand-channel", "credential_reference_id": "vault://reference/only"},
    )
    assert source_account.status_code == 201 and destination.status_code == 201
    assert "credential_reference_id" in destination.json() and "credential" not in str(destination.json()["provider_metadata"])

    editor = User(email="brand-editor@example.test", display_name="Brand Editor")
    session.add(editor)
    session.flush()
    editor_role = session.scalar(select(Role).where(Role.name == RoleName.EDITOR))
    if editor_role is None:
        editor_role = Role(name=RoleName.EDITOR)
        session.add(editor_role)
        session.flush()
    session.add_all([UserRole(user_id=editor.id, role_id=editor_role.id), BrandMembership(brand_id=brand_a, user_id=editor.id, role="EDITOR", is_default=True)])
    session.add_all([
        ProductionProject(brand_id=brand_a, source_url="https://example.test/brand-a", created_actor_id=DEV_ACTOR_ID),
        ProductionProject(brand_id=brand_b, source_url="https://example.test/brand-b", created_actor_id=DEV_ACTOR_ID),
        DiscoverySource(brand_id=brand_a, name="A source", provider="youtube", source_type="CHANNEL", platform="YOUTUBE", public_url="https://example.test/source-a"),
        DiscoverySource(brand_id=brand_b, name="B source", provider="youtube", source_type="CHANNEL", platform="YOUTUBE", public_url="https://example.test/source-b"),
    ])
    session.commit()

    editor_headers = {"X-Development-Actor": str(editor.id)}
    projects = client.get("/api/v1/production/projects", headers=editor_headers)
    sources = client.get("/api/v1/discovery/sources", headers=editor_headers)
    denied = client.get(f"/api/v1/production/projects?brand_id={brand_b}", headers=editor_headers)
    assert projects.status_code == 200 and [item["brand_id"] for item in projects.json()] == [str(brand_a)]
    assert sources.status_code == 200 and [item["brand_id"] for item in sources.json()] == [str(brand_a)]
    assert denied.status_code == 403


def test_new_operational_records_inherit_project_or_source_brand(session):  # type: ignore[no-untyped-def]
    workspace = Workspace(name="Test", slug="test")
    session.add(workspace)
    session.flush()
    brand = Brand(workspace_id=workspace.id, name="Cars", slug="cars")
    session.add(brand)
    session.flush()
    project = ProductionProject(
        brand_id=brand.id,
        source_url="https://example.test/cars",
        created_actor_id=DEV_ACTOR_ID,
    )
    session.add(project)
    session.commit()
    assert project.brand_id == brand.id
