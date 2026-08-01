import os
import subprocess
import sys


def _isolated_metadata_tables(module: str) -> set[str]:
    code = f"import {module}; from app.common.db import Base; print(','.join(Base.metadata.tables))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "VIRALFORGE_ENVIRONMENT": "test"},
    )
    return set(result.stdout.strip().split(","))


def test_audit_model_registers_its_brand_foreign_key_target() -> None:
    assert "brands" in _isolated_metadata_tables("app.audit.models")


def test_media_preview_service_registers_legacy_source_target() -> None:
    assert "sources" in _isolated_metadata_tables("app.media_preview.service")
