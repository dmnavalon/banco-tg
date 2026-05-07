from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from .utils import get_logger

log = get_logger("gsheet")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1bcH0Hu2_z_yVxZY3BuTkGaDzlsQQZYRCD1ayY-Pb6XM"
SHEET_NAME = "Movimientos"

_DAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# Header oficial del sheet — 13 columnas. Si lo cambias, sincroniza la fila 1 del sheet.
SHEET_HEADER = [
    "Fecha", "Día", "Mes", "Año", "Día Semana",
    "Banco", "Persona",
    "Descripción", "Monto", "Tipo", "Saldo",
    "Categoría", "Subcategoría",
]


def _client() -> gspread.Client:
    # Cloud: JSON string in env var
    key_json = os.environ.get("GSHEET_KEY_JSON", "").strip()
    if key_json:
        info = json.loads(key_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds)

    # Local: file path
    key_path = os.environ.get("GSHEET_KEY_PATH", "").strip()
    if not key_path:
        raise RuntimeError("Configura GSHEET_KEY_JSON (cloud) o GSHEET_KEY_PATH (local) en .env")
    creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _normalize_persona(raw: str | None) -> str:
    """Devuelve 'Titular' o 'Adicional'. Si raw es un nombre propio, asume 'Adicional'.
    Si es None o vacío, default 'Titular'."""
    if not raw:
        return "Titular"
    s = raw.strip()
    if not s:
        return "Titular"
    upper = s.upper()
    if upper == "TITULAR":
        return "Titular"
    if upper == "ADICIONAL":
        return "Adicional"
    # Cualquier otro texto (ej. nombre del adicional como "RAFFAELLA CIUFFARDI")
    return "Adicional"


def append_movement(mov: dict) -> None:
    """Añade una fila al Google Sheet de Movimientos.

    Lanza la excepción al caller si falla — quien decide si reintentar o avisar
    al usuario. Así evitamos marcar un movimiento como aprobado en Firestore
    cuando el sheet falla silenciosamente.

    El sheet tiene 13 columnas — ver SHEET_HEADER. Día/Mes/Año son numéricos
    para que se puedan usar en fórmulas. Categoría y Subcategoría van en
    columnas separadas.
    """
    try:
        client = _client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

        date_iso = mov.get("date") or ""
        try:
            dt = datetime.strptime(date_iso, "%Y-%m-%d")
            fecha = dt.strftime("%d/%m/%Y")
            dia = dt.day
            mes = dt.month
            ano = dt.year
            dia_semana = _DAYS_ES[dt.weekday()]
        except Exception:
            fecha = date_iso
            dia = mes = ano = ""
            dia_semana = ""

        amount = float(mov.get("amount") or 0)
        tipo = "Abono" if amount >= 0 else "Cargo"
        monto_abs = abs(amount)

        cat = mov.get("final_category") or mov.get("suggested_category") or ""
        sub = mov.get("final_subcategory") or mov.get("suggested_subcategory") or ""
        persona = _normalize_persona(mov.get("persona"))

        row = [
            fecha,                                         # 1.  Fecha (DD/MM/YYYY)
            dia,                                           # 2.  Día (número)
            mes,                                           # 3.  Mes (número)
            ano,                                           # 4.  Año (número)
            dia_semana,                                    # 5.  Día Semana (texto)
            (mov.get("bank") or "").capitalize(),          # 6.  Banco
            persona,                                       # 7.  Persona
            mov.get("description") or "",                  # 8.  Descripción
            monto_abs,                                     # 9.  Monto
            tipo,                                          # 10. Tipo (Abono/Cargo)
            "",                                            # 11. Saldo (vacío, no disponible)
            cat,                                           # 12. Categoría
            sub,                                           # 13. Subcategoría
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        log.info(f"GSheet OK: {fecha} · {mov.get('description','')[:40]}")
    except Exception:
        log.exception("GSheet falló al hacer append_row")
        raise
