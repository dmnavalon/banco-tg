"""Configura credenciales bancarias localmente (sin pasar por Telegram).

Las credenciales se piden con `getpass()` (no se muestran al tipear), se cifran
con la `master.key` local usando Fernet, y se guardan en Firestore. NUNCA pasan
por Telegram, logs, ni quedan en el historial del shell.

Es el equivalente seguro al wizard `/cred <banco>` del bot. Mismo cifrado, mismo
formato — sólo cambia el canal de entrada.

Uso interactivo:
    cd "Gestión de Gastos"
    source .venv/bin/activate
    python -m scripts.set_credentials              # te pregunta el banco
    python -m scripts.set_credentials bancochile   # banco predefinido
    python -m scripts.set_credentials falabella

Borrar:
    python -m scripts.set_credentials bancochile --delete

Listar bancos configurados (sin mostrar secrets):
    python -m scripts.set_credentials --list
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db, secrets_store  # noqa: E402

VALID_BANKS = ["falabella", "bancochile"]


def _ask_bank() -> str:
    print("Bancos disponibles:")
    for i, b in enumerate(VALID_BANKS, 1):
        print(f"  {i}. {b}")
    while True:
        ans = input("Elegí (número o nombre): ").strip().lower()
        if ans.isdigit() and 1 <= int(ans) <= len(VALID_BANKS):
            return VALID_BANKS[int(ans) - 1]
        if ans in VALID_BANKS:
            return ans
        print(f"Inválido. Opciones: {', '.join(VALID_BANKS)}.")


def _ask_rut() -> str:
    while True:
        rut = input("RUT (formato 12345678-9, sin puntos): ").strip().upper()
        if re.fullmatch(r"\d{7,8}-[\dK]", rut):
            return rut
        print("RUT inválido. Tiene que ser 7-8 dígitos, guión, dígito verificador (0-9 o K). Sin puntos.")


def _ask_password(bank: str) -> str:
    if bank == "falabella":
        prompt = "Clave Internet Falabella (6 dígitos, no se muestra al tipear): "
        validator = re.compile(r"\d{6}")
        max_len = 6
        invalid_msg = "Clave Falabella: deben ser exactamente 6 dígitos."
    elif bank == "bancochile":
        prompt = "Clave BCh (máx 8 chars, no se muestra al tipear): "
        validator = re.compile(r".{1,8}")
        max_len = 8
        invalid_msg = "Clave BCh: 1 a 8 caracteres."
    else:
        prompt = "Clave (no se muestra al tipear): "
        validator = re.compile(r".{1,32}")
        max_len = 32
        invalid_msg = "Clave inválida."

    while True:
        pw1 = getpass.getpass(prompt)
        if not validator.fullmatch(pw1):
            print(f"❌ {invalid_msg}")
            continue
        pw2 = getpass.getpass("Repetí la clave para confirmar: ")
        if pw1 != pw2:
            print("❌ Las dos claves no coinciden. Reintenta.")
            continue
        return pw1


def cmd_set(bank: str) -> int:
    rut = _ask_rut()
    password = _ask_password(bank)

    print(f"\nGuardando credenciales para {bank} (cifrado Fernet → Firestore)…")
    secrets_store.store(bank, rut, password)

    # Verificar leyendo de vuelta (sin imprimir la clave).
    loaded = secrets_store.load(bank)
    if not loaded:
        print(f"❌ Error: no pude leer de vuelta las credenciales de {bank}.")
        return 1
    rut_back, pw_back = loaded
    if rut_back != rut or pw_back != password:
        print(f"❌ Error: las credenciales leídas de vuelta no coinciden con las guardadas.")
        return 1
    print(f"✅ Credenciales de {bank} guardadas y verificadas correctamente.")
    print(f"   RUT: {rut}")
    print(f"   Clave: {len(password)} chars (cifrada en Firestore, no visible).")
    print()
    print(f"Probá con /test {bank} desde Telegram.")
    return 0


def cmd_delete(bank: str) -> int:
    print(f"Borrando credenciales de {bank}…")
    deleted = secrets_store.delete(bank)
    if deleted:
        print(f"✅ Credenciales de {bank} borradas de Firestore.")
    else:
        print(f"⚠️  No había credenciales de {bank} para borrar.")
    return 0


def cmd_list() -> int:
    configured = secrets_store.list_configured()
    if not configured:
        print("Sin credenciales configuradas.")
        return 0
    print("Bancos configurados (cifrados en Firestore):")
    for b in sorted(configured):
        print(f"  • {b}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Configura credenciales bancarias localmente (sin Telegram).")
    parser.add_argument("bank", nargs="?", choices=VALID_BANKS, help="Banco a configurar (si se omite, te pregunta).")
    parser.add_argument("--delete", action="store_true", help="Borrar las credenciales del banco indicado.")
    parser.add_argument("--list", action="store_true", help="Listar bancos configurados (sin mostrar secrets).")
    args = parser.parse_args()

    db.init_if_needed()

    if args.list:
        return cmd_list()

    bank = args.bank or _ask_bank()

    if args.delete:
        return cmd_delete(bank)

    return cmd_set(bank)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(130)
