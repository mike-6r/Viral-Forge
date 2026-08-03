from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from alembic import command
from app.common.db import Base
from scripts.schema_drift import check_sqlite_schema


def migration_config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url(database_path))
    return config


def database_url(database_path: Path) -> str:
    return f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"


def test_initial_migration_is_self_contained_and_explicit():
    source = Path("alembic/versions/0001_foundation.py").read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert "import app." not in source
    assert "op.create_table" in source
    assert "op.drop_table" in source
    assert "contentstatus" in source


def test_migration_history_has_one_manual_publish_mobile_download_head():
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert heads == ["0033_manual_publish_download"]


def test_migration_upgrade_downgrade_reupgrade_and_schema_parity(tmp_path: Path):
    database_path = tmp_path / "migration.db"
    config = migration_config(database_path)
    command.upgrade(config, "head")
    inspector = inspect(create_engine(database_url(database_path)))
    assert set(Base.metadata.tables).issubset(inspector.get_table_names())
    assert {index["name"] for index in inspector.get_indexes("content_items")} >= {
        "ix_content_items_created_at",
        "ix_content_items_status",
    }
    assert check_sqlite_schema(database_path) == []
    assert "preview_grants" in inspector.get_table_names()
    assert "discord_tickets" in inspector.get_table_names()
    assert "producer_recommendations" in inspector.get_table_names()
    assert "clip_quality_reports" in inspector.get_table_names()
    assert "rendered_media_inspections" in inspector.get_table_names()
    assert "rendered_media_inspection_issues" in inspector.get_table_names()
    assert "clip_correction_plans" in inspector.get_table_names()
    assert "clip_correction_actions" in inspector.get_table_names()
    assert "operations_alerts" in inspector.get_table_names()
    assert "operator_tasks" in inspector.get_table_names()
    assert "operations_reports" in inspector.get_table_names()
    assert "clip_download_grants" in inspector.get_table_names()
    assert "manual_publications" in inspector.get_table_names()
    assert "manual_analytics_checkpoints" in inspector.get_table_names()


def test_schema_hardening_indexes_nullability_and_foreign_keys(tmp_path: Path):
    database_path = tmp_path / "hardening.db"
    config = migration_config(database_path)
    command.upgrade(config, "head")
    inspector = inspect(create_engine(database_url(database_path)))
    expected_indexes = {
        "ix_brand_memberships_brand_id",
        "ix_brand_memberships_is_default",
        "ix_brand_memberships_user_id",
        "ix_destination_accounts_brand_id",
        "ix_production_projects_created_actor_id",
    }
    actual_indexes = {
        index["name"]
        for table in (
            "brand_memberships",
            "destination_accounts",
            "production_projects",
        )
        for index in inspector.get_indexes(table)
    }
    assert expected_indexes <= actual_indexes
    assert len(actual_indexes) == len(set(actual_indexes))
    audit_brand = next(
        column for column in inspector.get_columns("audit_events") if column["name"] == "brand_id"
    )
    assert audit_brand["nullable"] is False
    selected_source_foreign_keys = {
        tuple(item["constrained_columns"]): item["referred_table"]
        for item in inspector.get_foreign_keys("production_projects")
    }
    assert selected_source_foreign_keys[("selected_source_id",)] == "production_sources"
    snapshot_columns = {
        column["name"]: column for column in inspector.get_columns("post_analytics_snapshots")
    }
    assert snapshot_columns["publish_request_id"]["nullable"] is True
    assert "manual_publication_id" in snapshot_columns
    # Exercise the new forward-only TikTok revision independently before the
    # longer base cycle below; existing publishing rows must remain untouched.
    command.downgrade(config, "0024_download_progress")
    assert "tiktok_oauth_states" not in inspect(create_engine(database_url(database_path))).get_table_names()
    command.upgrade(config, "head")
    assert check_sqlite_schema(database_path) == []
    command.downgrade(config, "base")
    assert (
        "content_items" not in inspect(create_engine(database_url(database_path))).get_table_names()
    )
    command.upgrade(config, "head")
    assert check_sqlite_schema(database_path) == []
