"""External credential-store boundary.

Database records retain opaque references only.  The encrypted-file backend is
intended for a single VPS where its Fernet key is injected separately through
an environment or mounted secret; it never derives encryption from database
state or writes token-shaped values to filenames or logs.
"""

import json
import os
import secrets
import stat
import threading
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.common.config import Settings
from app.publishing.service import EnvironmentCredentialResolver, PublishingError


class CredentialStore(Protocol):
    def create(self, payload: dict[str, object], namespace: str = "token") -> str: ...
    def read(self, reference: str) -> dict[str, object]: ...
    def replace(self, reference: str, payload: dict[str, object]) -> None: ...
    def delete(self, reference: str) -> None: ...
    def exists(self, reference: str) -> bool: ...
    def health(self) -> dict[str, str]: ...


def _validate_payload(payload: dict[str, object]) -> None:
    if not isinstance(payload, dict) or not payload:
        raise PublishingError("CREDENTIAL_PAYLOAD_INVALID", "credential payload is invalid")
    if any(not isinstance(key, str) or len(key) > 100 for key in payload):
        raise PublishingError("CREDENTIAL_PAYLOAD_INVALID", "credential payload is invalid")


class EnvironmentCredentialStore:
    """Read-only adapter for pre-provisioned ``env://`` references."""

    def __init__(self, resolver: EnvironmentCredentialResolver | None = None) -> None:
        self.resolver = resolver or EnvironmentCredentialResolver()

    def create(self, payload: dict[str, object], namespace: str = "token") -> str:
        raise PublishingError("CREDENTIAL_STORE_READ_ONLY", "env:// credentials cannot be created dynamically")

    def read(self, reference: str) -> dict[str, object]:
        try:
            parsed = json.loads(self.resolver.resolve(reference))
        except json.JSONDecodeError as error:
            raise PublishingError("CREDENTIAL_FORMAT_INVALID", "credential reference did not resolve to JSON") from error
        if not isinstance(parsed, dict):
            raise PublishingError("CREDENTIAL_FORMAT_INVALID", "credential reference did not resolve to an object")
        return parsed

    def replace(self, reference: str, payload: dict[str, object]) -> None:
        raise PublishingError("CREDENTIAL_STORE_READ_ONLY", "env:// credentials cannot be replaced dynamically")

    def delete(self, reference: str) -> None:
        raise PublishingError("CREDENTIAL_STORE_READ_ONLY", "env:// credentials cannot be deleted dynamically")

    def exists(self, reference: str) -> bool:
        try:
            self.resolver.resolve(reference)
        except PublishingError:
            return False
        return True

    def health(self) -> dict[str, str]:
        return {"backend": "env", "status": "read_only"}


class EncryptedFileCredentialStore:
    """Small, atomic encrypted credential store for the current single-VPS deployment."""

    _lock = threading.RLock()
    _prefix = "file://"

    def __init__(self, path: str | Path, master_key: str) -> None:
        try:
            self._fernet = Fernet(master_key.encode())
        except (ValueError, TypeError) as error:
            raise PublishingError("CREDENTIAL_MASTER_KEY_INVALID", "credential-store master key is invalid") from error
        self.path = Path(path)

    def _reference_key(self, reference: str) -> str:
        if not reference.startswith(self._prefix):
            raise PublishingError("INVALID_CREDENTIAL_REFERENCE", "credential reference is invalid")
        key = reference.removeprefix(self._prefix)
        if not key or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in key):
            raise PublishingError("INVALID_CREDENTIAL_REFERENCE", "credential reference is invalid")
        return key

    def _read_all(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            encrypted = self.path.read_bytes()
            decoded = self._fernet.decrypt(encrypted)
            payload = json.loads(decoded)
        except (OSError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PublishingError("CREDENTIAL_STORE_CORRUPT", "credential store is unavailable or corrupted") from error
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("records"), dict):
            raise PublishingError("CREDENTIAL_STORE_CORRUPT", "credential store is unavailable or corrupted")
        records = payload["records"]
        return {str(key): value for key, value in records.items() if isinstance(value, dict)}

    def _write_all(self, records: dict[str, dict[str, object]]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)
        encrypted = self._fernet.encrypt(json.dumps({"version": 1, "records": records}, separators=(",", ":")).encode())
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def create(self, payload: dict[str, object], namespace: str = "token") -> str:
        _validate_payload(payload)
        if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in namespace):
            raise PublishingError("INVALID_CREDENTIAL_REFERENCE", "credential namespace is invalid")
        with self._lock:
            records = self._read_all()
            key = f"{namespace}_{secrets.token_urlsafe(24).replace('-', '_')}"
            records[key] = payload
            self._write_all(records)
        return f"{self._prefix}{key}"

    def read(self, reference: str) -> dict[str, object]:
        key = self._reference_key(reference)
        with self._lock:
            value = self._read_all().get(key)
        if value is None:
            raise PublishingError("CREDENTIAL_UNAVAILABLE", "the referenced publishing credential is unavailable")
        return value.copy()

    def replace(self, reference: str, payload: dict[str, object]) -> None:
        _validate_payload(payload)
        key = self._reference_key(reference)
        with self._lock:
            records = self._read_all()
            if key not in records:
                raise PublishingError("CREDENTIAL_UNAVAILABLE", "the referenced publishing credential is unavailable")
            records[key] = payload
            self._write_all(records)

    def delete(self, reference: str) -> None:
        key = self._reference_key(reference)
        with self._lock:
            records = self._read_all()
            records.pop(key, None)
            self._write_all(records)

    def exists(self, reference: str) -> bool:
        try:
            key = self._reference_key(reference)
            with self._lock:
                return key in self._read_all()
        except PublishingError:
            return False

    def health(self) -> dict[str, str]:
        try:
            with self._lock:
                self._read_all()
            permissions = "not_applicable"
            if os.name != "nt" and self.path.exists():
                permissions = oct(stat.S_IMODE(self.path.stat().st_mode))
            return {"backend": "encrypted_file", "status": "ok", "permissions": permissions}
        except PublishingError:
            return {"backend": "encrypted_file", "status": "unavailable"}


def credential_store(settings: Settings) -> CredentialStore:
    if settings.credential_store_backend == "encrypted_file":
        reference = settings.credential_store_master_key_reference
        master_key = EnvironmentCredentialResolver().resolve(reference)
        return EncryptedFileCredentialStore(settings.credential_store_file_path, master_key)
    return EnvironmentCredentialStore()
