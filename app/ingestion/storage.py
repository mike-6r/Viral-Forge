"""Storage abstraction for ingestion.  Keys are provider-relative and opaque."""

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int


class Storage(Protocol):
    def create_temporary(self) -> str: ...
    def write_chunk(self, temporary_key: str, chunk: bytes) -> None: ...
    def read_prefix(self, key: str, limit: int) -> bytes: ...
    def finalize(self, temporary_key: str, suffix: str = "") -> StoredObject: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def metadata(self, key: str) -> StoredObject: ...
    def delete(self, key: str) -> None: ...
    def cleanup_temporary(self, older_than_seconds: int, dry_run: bool = False) -> list[str]: ...


class S3CompatibleStorage:
    """Reserved provider boundary; credentials and remote operations are deferred."""

    def __init__(self, *_: object, **__: object) -> None:
        raise NotImplementedError("S3-compatible storage is not part of Milestone 2B")


class LocalFilesystemStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.tmp_root = self.root / "tmp"
        self.assets_root = self.root / "assets"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.assets_root.mkdir(parents=True, exist_ok=True)
        if self.tmp_root.resolve() == self.assets_root.resolve():
            raise ValueError("temporary and asset storage directories must differ")

    def _path(self, key: str, expected_root: Path | None = None) -> Path:
        if not key or "/" not in key or "\\" in key or Path(key).is_absolute():
            raise ValueError("unsafe storage key")
        candidate = (self.root / key).resolve()
        root = (expected_root or self.root).resolve()
        if candidate == root or root not in candidate.parents:
            raise ValueError("storage key escapes configured root")
        return candidate

    def create_temporary(self) -> str:
        key = f"tmp/{uuid.uuid4().hex}.part"
        path = self._path(key, self.tmp_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=False)
        return key

    def write_chunk(self, temporary_key: str, chunk: bytes) -> None:
        path = self._path(temporary_key, self.tmp_root)
        if not path.is_file() or path.is_symlink():
            raise ValueError("temporary storage object is unavailable")
        with path.open("ab") as handle:
            handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())

    def read_prefix(self, key: str, limit: int) -> bytes:
        path = self._path(key)
        with path.open("rb") as handle:
            return handle.read(limit)

    def finalize(self, temporary_key: str, suffix: str = "") -> StoredObject:
        source = self._path(temporary_key, self.tmp_root)
        if not source.is_file() or source.is_symlink():
            raise ValueError("temporary storage object is unavailable")
        clean_suffix = suffix.lower() if suffix.startswith(".") and len(suffix) <= 10 else ""
        key = f"assets/{uuid.uuid4().hex}{clean_suffix}"
        destination = self._path(key, self.assets_root)
        if destination.exists():
            raise FileExistsError("generated asset key already exists")
        os.replace(source, destination)
        return StoredObject(key=key, size_bytes=destination.stat().st_size)

    def open(self, key: str) -> BinaryIO:
        path = self._path(key, self.assets_root)
        if path.is_symlink():
            raise ValueError("symlinked storage objects are not permitted")
        return path.open("rb")

    def exists(self, key: str) -> bool:
        try:
            path = self._path(key)
        except ValueError:
            return False
        return path.is_file() and not path.is_symlink()

    def metadata(self, key: str) -> StoredObject:
        path = self._path(key)
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("storage object does not exist")
        return StoredObject(key=key, size_bytes=path.stat().st_size)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists() and not path.is_symlink():
            path.unlink()

    def cleanup_temporary(self, older_than_seconds: int, dry_run: bool = False) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        removed: list[str] = []
        for candidate in self.tmp_root.iterdir():
            resolved = candidate.resolve()
            if (
                self.tmp_root.resolve() not in resolved.parents
                or candidate.is_symlink()
                or not candidate.is_file()
            ):
                continue
            modified = datetime.fromtimestamp(candidate.stat().st_mtime, UTC)
            if modified < cutoff:
                removed.append(candidate.relative_to(self.root).as_posix())
                if not dry_run:
                    candidate.unlink()
        return removed
