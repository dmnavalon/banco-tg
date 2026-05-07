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

_MONTHS_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
               "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
_DAYS_ES   = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]


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


def append_movement(mov: dict) -> None:
    """Añade una fila al Google Sheet de Movimientos.

    Lanza la excepción al caller si falla — quien decide si reintentar o avisar
    al usuario. Así evitamos marcar un movimiento como aprobado en Firestore
    cuando el sheet falla silenciosamente.
    """
    try:
        client = _client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

        date_iso = mov.get("date") or ""
        try:
            dt = datetime.strptime(date_iso, "%Y-%m-%d")
            fecha      = dt.strftime("%d/%m/%Y")
            mes        = _MONTHS_ES[dt.month - 1]
            ano        = str(dt.year)
            dia_semana = _DAYS_ES[dt.weekday()]
        except Exception:
            fecha = date_iso
            mes = ano = dia_semana = ""

        amount    = float(mov.get("amount") or 0)
        tipo      = "Abono" if amount >= 0 else "Cargo"
        monto_abs = abs(amount)

        cat = mov.get("final_category") or mov.get("suggested_category") or ""
        sub = mov.get("final_subcategory") or mov.get("suggested_subcategory") or ""
        categoria = f"{cat}/{sub}" if sub else cat

        row = [
            fecha,
            mov.get("description") or "",
            monto_abs,
            tipo,
            "",              # Saldo — no disponible en el scraper
            (mov.get("bank") or "").capitalize(),
            mes,
            ano,
            dia_semana,
            categoria,
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        log.info(f"GSheet OK: {fecha} · {mov.get('description','')[:40]}")
    except Exception:
        log.exception("GSheet falló al hacer append_row")
        raise
