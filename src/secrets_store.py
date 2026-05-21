from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken

from .db import (
    delete_credential,
    get_credential_blob,
    list_credential_docs,
    list_credentials,
    mark_credential_invalid,
    set_credential_blob,
)
from .utils import get_logger

log = get_logger("secrets_store")


class CredentialDecryptError(Exception):
    """Las credenciales existen en Firestore pero no pudieron descifrarse
    (master key rotada, blob corrupto, etc.)."""


def _master_key() -> bytes:
    key = os.environ.get("MASTER_KEY", "").strip()
    if key:
        return key.encode()
    from .utils import project_path
    path = project_path("data", ".master.key")
    if path.exists():
        return path.read_bytes().strip()
    # Crea atomicamente con O_EXCL para evitar race entre dos procesos que
    # arrancan simultáneamente sin master key — antes ambos podían generar
    # keys distintas y sobrescribir, dejando credenciales irrecuperables.
    path.parent.mkdir(parents=True, exist_ok=True)
    new_key = Fernet.generate_key()
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, new_key)
        finally:
            os.close(fd)
        return new_key
    except FileExistsError:
        # Otro proceso ganó la carrera. Leer la key que ese proceso escribió.
        return path.read_bytes().strip()


def _fernet() -> Fernet:
    return Fernet(_master_key())


def store(bank: str, rut: str, password: str) -> None:
    rut = (rut or "").strip()
    password = (password or "").strip()
    payload = f"{rut}\n{password}".encode("utf-8")
    blob_b64 = base64.b64encode(_fernet().encrypt(payload)).decode("ascii")
    set_credential_blob(bank, blob_b64)


def load(bank: str) -> tuple[str, str] | None:
    """Carga (rut, password) desde Firestore. Retorna None si no hay credenciales.
    Si las credenciales existen pero no pueden descifrarse (master key rotada,
    blob corrupto), levanta `CredentialDecryptError` para distinguir del caso
    'no configurado'."""
    blob_b64 = get_credential_blob(bank)
    if not blob_b64:
        return None
    try:
        blob_bytes = base64.b64decode(blob_b64)
        decrypted = _fernet().decrypt(blob_bytes).decode("utf-8")
    except InvalidToken as e:
        log.exception("Master key no descifra el blob de %s (rotada o blob corrupto).", bank)
        raise CredentialDecryptError(
            f"Credencial de {bank} existe pero no pude descifrarla. "
            f"Master key rotada o blob corrupto. Re-configura con /cred {bank}."
        ) from e
    except Exception as e:
        log.exception("Error inesperado descifrando credencial de %s.", bank)
        raise CredentialDecryptError(
            f"Error descifrando credencial de {bank}: {type(e).__name__}: {e}"
        ) from e
    parts = decrypted.split("\n", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else None


def list_configured(include_invalid: bool = False) -> list[str]:
    """Lista bancos con credenciales configuradas. Por defecto excluye los
    bancos cuyo último intento fue rechazado por el banco (campo
    `invalid_since`), para que `run_daily` no siga quemando intentos
    fallidos contra una credencial que el banco ya rechazó. El flag se
    limpia automáticamente cuando el usuario hace `/cred <banco>` (porque
    `set_credential_blob` pisa el doc completo)."""
    if include_invalid:
        return list_credentials()
    return [d["bank"] for d in list_credential_docs() if not d.get("invalid_since")]


def is_invalid(bank: str) -> bool:
    for d in list_credential_docs():
        if d["bank"] == bank.lower():
            return bool(d.get("invalid_since"))
    return False


def mark_invalid(bank: str, reason: str) -> None:
    """Marca la credencial como rechazada por el banco. El siguiente
    `run_daily` saltea este banco hasta que el usuario haga `/cred <banco>`."""
    mark_credential_invalid(bank, reason)


def delete(bank: str) -> bool:
    return delete_credential(bank)
