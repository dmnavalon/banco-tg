"""Verifica las credenciales guardadas en el secrets_store contra lo que el
usuario espera. Decifra el blob de Fernet en Firestore y muestra el RUT (completo)
y la clave parcialmente censurada para que Diego confirme que están bien.

Uso:
    cd "Gestión de Gastos"
    source .venv/bin/activate
    python -m scripts.check_credentials bancochile
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import secrets_store, db  # noqa: E402

VALID_BANKS = {"falabella", "bancochile"}


def main(bank: str) -> int:
    bank = bank.lower()
    if bank not in VALID_BANKS:
        print(f"Banco no soportado: {bank}. Opciones: {', '.join(VALID_BANKS)}")
        return 1

    db.init_if_needed()
    creds = secrets_store.load(bank)
    if not creds:
        print(f"❌ No hay credenciales guardadas para {bank}.")
        print(f"   Ejecutá /cred {bank} desde Telegram para configurarlas.")
        return 1

    rut, password = creds

    print(f"=== Credenciales guardadas para {bank} ===")
    print(f"RUT  : {rut!r}")
    print(f"       (largo: {len(rut)} chars)")
    print(f"Clave: largo={len(password)} chars")
    if password:
        # Censurar fuerte: solo primer carácter + asteriscos. NO mostramos bytes
        # plaintext para no filtrar la clave en logs/transcripts.
        if len(password) <= 2:
            masked = "*" * len(password)
        else:
            masked = f"{password[0]}{'*' * (len(password) - 1)}"
        print(f"       muestra: {masked!r}")
        # Diagnóstico SIN exponer la clave: solo flags de problemas.
        non_ascii_count = sum(1 for c in password if ord(c) > 127)
        has_leading_ws = password != password.lstrip()
        has_trailing_ws = password != password.rstrip()
        has_uppercase = any(c.isupper() for c in password)
        has_lowercase = any(c.islower() for c in password)
        has_digit = any(c.isdigit() for c in password)
        has_special = any(not c.isalnum() for c in password)
        print(f"       composición: upper={has_uppercase} lower={has_lowercase} digit={has_digit} special={has_special}")
        if non_ascii_count:
            print(f"       ⚠️  contiene {non_ascii_count} caracteres no-ASCII (acentos, ñ, etc.) — BCh podría rechazar")
        if has_leading_ws or has_trailing_ws:
            print(f"       ⚠️  espacios al inicio/final (leading={has_leading_ws} trailing={has_trailing_ws})")

    print()
    print("Verificá:")
    print(f"  1. ¿El RUT {rut!r} es exactamente el tuyo? Mismo dígito verificador?")
    print(f"  2. ¿La clave tiene la longitud que esperás? (sin espacios, sin caracteres raros)")
    print()
    print("Si hay algo raro, ejecutá desde Telegram:")
    print(f"  /forget {bank}")
    print(f"  /cred {bank}")
    print(f"y volvé a configurar las credenciales correctas.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: python -m scripts.check_credentials <{ '|'.join(VALID_BANKS)}>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
