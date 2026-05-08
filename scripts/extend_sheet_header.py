"""Extiende el header del Google Sheet para incluir las 3 columnas nuevas
de cuotas (Cuota actual, Cuotas total, Cuota a pagar) sin tocar las filas
existentes — sus celdas en N/O/P quedan vacías hasta que se vuelvan a
aprobar/corregir (ahí el upsert_movement las llena).

Idempotente: si el header ya tiene las 16 columnas, no hace nada.

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


def main() -> int:
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file("data/gsheet_service_account.json", scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    current = sheet.row_values(1)
    print(f"Header actual ({len(current)} cols): {current}")
    print(f"Header esperado ({len(SHEET_HEADER)} cols): {SHEET_HEADER}")

    if current == SHEET_HEADER:
        print("\n✅ Header ya está al día. Nada que hacer.")
        return 0

    # Caso: el header tiene menos columnas (las primeras N coinciden).
    if len(current) < len(SHEET_HEADER) and SHEET_HEADER[: len(current)] == current:
        new_cells = SHEET_HEADER[len(current):]
        # Calcular rango destino. Letras de columna A=1, B=2, etc.
        start_col = len(current) + 1
        end_col = len(SHEET_HEADER)
        start_letter = _col_letter(start_col)
        end_letter = _col_letter(end_col)
        cell_range = f"{start_letter}1:{end_letter}1"
        sheet.update(cell_range, [new_cells], value_input_option="USER_ENTERED")
        print(f"\n✅ Agregadas {len(new_cells)} columnas al header en rango {cell_range}: {new_cells}")
        print(f"   Las filas existentes preservan su contenido — celdas {start_letter}..{end_letter} quedan vacías.")
        return 0

    # Caso: el header tiene columnas distintas o está fuera de orden.
    print("\n⚠️  El header actual no es un prefijo del esperado. NO se sobrescribe automáticamente.")
    print("    Si querés forzar la actualización (perdiendo nombres viejos), edita la fila 1")
    print("    manualmente desde Google Sheets para que coincida con SHEET_HEADER.")
    return 1


def _col_letter(n: int) -> str:
    """1 → A, 2 → B, ..., 27 → AA."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


if __name__ == "__main__":
    sys.exit(main())
