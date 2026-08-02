"""Compare an Alembic-created disposable schema with current ORM metadata."""

import argparse
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

import app.accounts.models  # noqa: F401
import app.analysis.models  # noqa: F401
import app.analytics.models  # noqa: F401
import app.audit.models  # noqa: F401
import app.brands.models  # noqa: F401
import app.content.models  # noqa: F401
import app.content_packages.models  # noqa: F401
import app.discord_business.models  # noqa: F401
import app.discovery.models  # noqa: F401
import app.ingestion.models  # noqa: F401
import app.media_preview.models  # noqa: F401
import app.moderation.models  # noqa: F401
import app.opportunities.models  # noqa: F401
import app.producer.models  # noqa: F401
import app.production.models  # noqa: F401
import app.publishing.models  # noqa: F401
import app.ranking.models  # noqa: F401
import app.rendered_media.models  # noqa: F401
import app.review.models  # noqa: F401
import app.rights.models  # noqa: F401
import app.sources.models  # noqa: F401
from alembic import command
from app.common.db import Base


def check_sqlite_schema(database_path: Path) -> list[str]:
    config = Config("alembic.ini")
    database_url = f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    inspector = inspect(create_engine(database_url))
    actual_tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)
    differences = [f"missing table: {name}" for name in sorted(expected_tables - actual_tables)]
    differences.extend(
        f"unexpected table: {name}"
        for name in sorted(actual_tables - expected_tables - {"alembic_version"})
    )
    for table_name in sorted(expected_tables & actual_tables):
        table = Base.metadata.tables[table_name]
        actual_column_rows = {
            column["name"]: column for column in inspector.get_columns(table_name)
        }
        actual_columns = set(actual_column_rows)
        expected_columns = {column.name for column in table.columns}
        differences.extend(
            f"{table_name}: missing column {name}"
            for name in sorted(expected_columns - actual_columns)
        )
        differences.extend(
            f"{table_name}: unexpected column {name}"
            for name in sorted(actual_columns - expected_columns)
        )
        for column in table.columns:
            actual = actual_column_rows.get(column.name)
            if actual and not column.primary_key and bool(actual["nullable"]) != column.nullable:
                differences.append(
                    f"{table_name}.{column.name}: nullability {actual['nullable']} != {column.nullable}"
                )
        actual_indexes = {index["name"] for index in inspector.get_indexes(table_name)}
        expected_indexes = {index.name for index in table.indexes if index.name}
        differences.extend(
            f"{table_name}: missing index {name}"
            for name in sorted(expected_indexes - actual_indexes)
        )
        actual_foreign_keys = {
            (tuple(foreign_key["constrained_columns"]), foreign_key["referred_table"])
            for foreign_key in inspector.get_foreign_keys(table_name)
        }
        expected_foreign_keys = {
            (
                tuple(foreign_key.parent.name for foreign_key in constraint.elements),
                constraint.referred_table.name,
            )
            for constraint in table.foreign_key_constraints
        }
        differences.extend(
            f"{table_name}: missing foreign key {columns} -> {target}"
            for columns, target in sorted(expected_foreign_keys - actual_foreign_keys)
        )
    return differences


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    arguments = parser.parse_args()
    drift = check_sqlite_schema(arguments.database)
    if drift:
        raise SystemExit("Schema drift:\n" + "\n".join(drift))
    print("No schema drift detected.")
