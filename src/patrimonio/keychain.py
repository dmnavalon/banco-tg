"""Wrapper de Mac Keychain para credenciales de sitios de patrimonio.

Diseñado para correr SOLO en macOS. Si lo importas en otro sistema operativo
falla en el primer `security` exec. No es un problema porque este módulo
nunca se carga desde el bot de Railway.

Modelo:
- Servicio: SERVICE constante. Una sola entrada Keychain por sitio.
- Cuenta: nombre del sitio (`fintual`, `racional`, `bch_inv`, `nauta`,
  `mercadopago`). Lowercase, ASCII.
- Comment: JSON con `{"user": "...", "added_at": "...", "updated_at": "..."}`
  para que `list_sites()` devuelva metadata sin extra Keychain access prompts.

La clave en sí va en el payload del item Keychain (`-w`). El usuario (RUT,
email, lo que sea) NO va en el `-a` del item — eso es el SLUG del sitio.
El user real va dentro del comment como parte del JSON, accesible con
`security find-generic-password -s SERVICE -a <site> -g` que prints el
comment a stderr.

Para evitar abrir un prompt de contraseña por cada acceso, los items se
crean con `-T /usr/bin/security` (la propia herramienta está autorizada).
Aún así, el primer `python -m src.patrimonio.cli run` puede pedir clave
del Mac a Diego — es normal.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from datetime import datetime

from ..utils import get_logger

log = get_logger("patrimonio.keychain")

SERVICE = "control-gastos.patrimonio"
MASTER_KEY_ACCOUNT = "__master_key__"


class KeychainError(RuntimeError):
    pass


class CredentialNotFound(KeychainError):
    pass


def _assert_macos() -> None:
    if platform.system() != "Darwin":
        raise KeychainError(
            "keychain.py solo funciona en macOS. Patrimonio scrapers no están "
            "soportados en otros sistemas."
        )
    if not shutil.which("security"):
        raise KeychainError("/usr/bin/security no encontrado en PATH.")


def _run(args: list[str], *, input_bytes: bytes | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Ejecuta `security` capturando stdout+stderr. No imprime nada de la clave.

    Nota: `security find-generic-password -w` imprime la clave a STDOUT (no
    stderr — eso es para `-g`). Por eso siempre capture_output=True y la clave
    nunca llega al terminal del usuario.
    """
    return subprocess.run(
        ["/usr/bin/security", *args],
        input=input_bytes,
        capture_output=True,
        check=check,
    )


def _make_comment(user: str, existing_comment: str = "") -> str:
    now = datetime.now().isoformat(timespec="seconds")
    try:
        prev = json.loads(existing_comment) if existing_comment else {}
    except json.JSONDecodeError:
        prev = {}
    added_at = prev.get("added_at") or now
    return json.dumps({"user": user, "added_at": added_at, "updated_at": now}, ensure_ascii=False)


def set_credential(site: str, user: str, password: str) -> None:
    """Guarda (o actualiza) la credencial de un sitio en el Keychain.

    `-U` (update) hace que sea idempotente: si ya existe, lo sobrescribe sin
    pedir confirmación. `-T /usr/bin/security` evita que macOS pregunte
    "permitir acceso?" cada vez que el script lee.
    """
    _assert_macos()
    site = site.strip().lower()
    user = user.strip()
    if not site or not user or not password:
        raise ValueError("site, user, password no pueden ser vacíos")

    existing = ""
    try:
        existing = _read_comment(site)
    except CredentialNotFound:
        pass

    comment = _make_comment(user, existing)
    _run([
        "add-generic-password",
        "-U",
        "-s", SERVICE,
        "-a", site,
        "-w", password,
        "-j", comment,
        "-T", "/usr/bin/security",
    ])
    log.info("Credencial guardada en Keychain: %s/%s", SERVICE, site)


def get_credential(site: str) -> tuple[str, str]:
    """Devuelve (user, password) del Keychain. Levanta CredentialNotFound si no existe."""
    _assert_macos()
    site = site.strip().lower()

    res_pwd = _run(
        ["find-generic-password", "-s", SERVICE, "-a", site, "-w"],
        check=False,
    )
    if res_pwd.returncode != 0:
        raise CredentialNotFound(f"No hay credencial para {site} en {SERVICE}.")
    password = res_pwd.stdout.decode("utf-8").rstrip("\n")
    comment = _read_comment(site)
    try:
        user = json.loads(comment).get("user", "")
    except json.JSONDecodeError:
        user = ""
    return user, password


def delete_credential(site: str) -> bool:
    """Borra la credencial. Devuelve True si existía, False si no."""
    _assert_macos()
    site = site.strip().lower()
    res = _run(
        ["delete-generic-password", "-s", SERVICE, "-a", site],
        check=False,
    )
    return res.returncode == 0


def list_sites() -> list[dict]:
    """Lista todos los sitios configurados. Devuelve [{site, user, updated_at}].

    No hay un comando `security` para enumerar items de un servicio sin saber
    la account. Por eso lo resolvemos con `security dump-keychain` y grep —
    es la única forma sin abrir un Cocoa GUI app. Es lento (~1s) pero solo
    corre en CLI interactivo, no en el hot path.
    """
    _assert_macos()
    res = _run(["dump-keychain"], check=False)
    if res.returncode != 0:
        return []
    text = res.stdout.decode("utf-8", errors="replace")

    sites: list[dict] = []
    seen: set[str] = set()
    current_svc: str | None = None
    current_acc: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('"svce"'):
            current_svc = _extract_quoted_value(line)
        elif line.startswith('"acct"'):
            current_acc = _extract_quoted_value(line)
        elif line.startswith("keychain:") and current_svc and current_acc:
            if current_svc == SERVICE and current_acc != MASTER_KEY_ACCOUNT:
                if current_acc not in seen:
                    seen.add(current_acc)
                    sites.append({"site": current_acc})
            current_svc = None
            current_acc = None

    # Enrich con metadata del comment
    enriched = []
    for s in sites:
        try:
            comment = _read_comment(s["site"])
            meta = json.loads(comment) if comment else {}
        except (CredentialNotFound, json.JSONDecodeError):
            meta = {}
        enriched.append({
            "site": s["site"],
            "user": meta.get("user", ""),
            "added_at": meta.get("added_at", ""),
            "updated_at": meta.get("updated_at", ""),
        })
    enriched.sort(key=lambda x: x["site"])
    return enriched


def _read_comment(site: str) -> str:
    """Lee el campo `-j` (comment, "icmt" en dump) sin exponer la clave."""
    res = _run(
        ["find-generic-password", "-s", SERVICE, "-a", site, "-g"],
        check=False,
    )
    if res.returncode != 0:
        raise CredentialNotFound(f"No hay credencial para {site} en {SERVICE}.")
    # `-g` printa los metadatos a STDERR. La clave también va a stderr en
    # formato `password: "..."`. Para el comment buscamos `"icmt"<blob>=`.
    stderr = res.stderr.decode("utf-8", errors="replace")
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith('"icmt"'):
            val = _extract_quoted_value(line)
            return val or ""
    return ""


def _extract_quoted_value(line: str) -> str:
    """Extrae el valor entre comillas final de líneas tipo `"svce"<blob>="control-gastos.patrimonio"`."""
    if "=" not in line:
        return ""
    rhs = line.split("=", 1)[1].strip()
    if rhs.startswith('"') and rhs.endswith('"'):
        return rhs[1:-1]
    return ""


# ----------------------------------------------------------------------------
# Master key para cifrar storage_state de Playwright
# ----------------------------------------------------------------------------

def get_or_create_master_key() -> bytes:
    """Master key Fernet para cifrar las sesiones Playwright (`state_*.json.enc`).

    Si no existe en el Keychain, la genera y la guarda. Idempotente.
    """
    _assert_macos()
    try:
        _, key = get_credential(MASTER_KEY_ACCOUNT)
        return key.encode("ascii")
    except CredentialNotFound:
        from cryptography.fernet import Fernet
        new_key = Fernet.generate_key()
        # `set_credential` usa `-U`, ok aunque entremos por race.
        set_credential(MASTER_KEY_ACCOUNT, "__system__", new_key.decode("ascii"))
        log.info("Master key generada y guardada en Keychain.")
        return new_key
