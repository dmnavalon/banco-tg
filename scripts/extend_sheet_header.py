"""Extiende el header del Google Sheet para incluir las 3 columnas nuevas
de cuotas (Cuota actual, Cuotas total, Cuota a pagar) en posiciones 14-16,
después de Subcategoría.

Si el sheet ya tenía columnas adicionales del usuario (Moneda, MontoCLP,
Esencial, Fijo, Recurrente, Extraordinario, Excluido, Notas, etc.) en
posiciones 14+, las EMPUJA a la derecha (Moneda pasa de N a Q, etc.).
Las celdas existentes preservan su contenido, gspread maneja el shift.

Idempotente: si las 3 cols de cuotas ya están en algún lugar del header,
no hace nada.

Uso:
    cd "Gestión de Gastos"
    source .venv/bin/activate
    python -m scripts.extend_sheet_header
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gspread  # noqa: E402
from google.oauth2.service_account import Credentials  # noqa: E402

from src.gsheet import SHEET_HEADER, SHEET_NAME, SPREADSHEET_ID  # noqa: E402

CUOTAS_COLS = ["Cuota actual", "Cuotas total", "Cuota a pagar"]
INSERT_AT = 14  # 1-indexed: justo después de Subcategoría (col 13)
BOT_BASE_HEADER = SHEET_HEADER[:13]  # Fecha..Subcategoría — gestionadas por el bot


def main() -> int:
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file("data/gsheet_service_account.json", scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    current = sheet.row_values(1)
    print(f"Header actual ({len(current)} cols): {current}")

    if all(c in current for c in CUOTAS_COLS):
        positions = [(c, current.index(c) + 1) for c in CUOTAS_COLS]
        print(f"\n✅ Las 3 columnas de cuotas ya existen en el header: {positions}")
        print("    Nada que hacer.")
        return 0

    if current[: len(BOT_BASE_HEADER)] != BOT_BASE_HEADER:
        print("\n⚠️  Las primeras 13 columnas no coinciden con el header esperado.")
        print(f"    Esperado: {BOT_BASE_HEADER}")
        print("    No puedo insertar automáticamente. Edita el header manualmente.")
        return 1

    print(f"\nInsertando 3 columnas en posición {INSERT_AT} (después de Subcategoría)…")
    print(f"  Las cols actuales 14+ ({current[13:]}) se empujarán a 17+.")

    # gspread.insert_cols inserta `values` (lista de listas, una por columna)
    # en `col` (1-indexed). Empuja las existentes a la derecha. Solo
    # rellenamos la fila 1 (header); las demás filas mantienen sus celdas
    # originales en la nueva posición.
    sheet.insert_cols(
        [["Cuota actual"], ["Cuotas total"], ["Cuota a pagar"]],
        col=INSERT_AT,
    )

    print(f"\n✅ Listo. Header extendido a {len(SHEET_HEADER)} columnas.")
    new_header = sheet.row_values(1)
    print(f"   Header nuevo: {new_header}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
