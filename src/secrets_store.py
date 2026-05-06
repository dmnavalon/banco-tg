from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken

from .db import delete_credential, get_credential_blob, list_credentials, set_credential_blob


def _master_key() -> bytes:
    key = os.environ.get("MASTER_KEY", "").strip()
    if key:
        return key.encode()
    from .utils import project_path
    path = project_path("data", ".master.key")
    if path.exists():
        return path.read_bytes().strip()
    new_key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(new_key)
    os.chmod(path, 0o600)
    return new_key


def _fernet() -> Fernet:
    return Fernet(_master_key())


def store(bank: str, rut: str, password: str) -> None:
    payload = f"{rut}\n{password}".encode("utf-8")
    blob_b64 = base64.b64encode(_fernet().encrypt(payload)).decode("ascii")
    set_credential_blob(bank, blob_b64)


def load(bank: str) -> tuple[str, str] | None:
    blob_b64 = get_credential_blob(bank)
    if not blob_b64:
        return None
    try:
        blob_bytes = base64.b64decode(blob_b64)
        decrypted = _fernet().decrypt(blob_bytes).decode("utf-8")
    except (InvalidToken, Exception):
        return None
    parts = decrypted.split("\n", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else None


def list_configured() -> list[str]:
    return list_credentials()


def delete(bank: str) -> bool:
    return delete_credential(bank)
