from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

import re

from .utils import get_logger

log = get_logger("gsheet")


def _safe_text(s: str) -> str:
    """Prefixea con `'` si el string podría interpretarse como fórmula en Sheets.
    Sheets evalúa cualquier celda que empiece con `=`, `+`, `-`, `@`."""
    if not s:
        return s
    if s[0] in "=+-@":
        return "'" + s
    return s


def _norm_desc(s: str) -> str:
    """Normaliza la descripción para comparar filas (case + espacios + NBSP)."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip().upper()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1bcH0Hu2_z_yVxZY3BuTkGaDzlsQQZYRCD1ayY-Pb6XM"
SHEET_NAME = "Movimientos"

_DAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# Header oficial del sheet — 24 columnas. Si lo cambias, sincroniza la fila 1
# del sheet (ver scripts/extend_sheet_header.py).
#
# Layout:
#   1-13:  Datos del movimiento (gestión del bot)
#   14-16: Metadata de cuotas (gestión del bot, vacías en compras "1/1")
#   17-24: Columnas del dashboard del usuario (Diego). El bot las preserva en
#          append (las deja vacías) y NUNCA las toca en update — las llena
#          el dashboard via fórmulas o input manual.
SHEET_HEADER = [
    # Bot:
    "Fecha", "Día", "Mes", "Año", "Día Semana",
    "Banco", "Persona",
    "Descripción", "Monto", "Tipo", "Saldo",
    "Categoría", "Subcategoría",
    "Cuota actual", "Cuotas total", "Cuota a pagar",
    # Dashboard de Diego — el bot solo append-vacío:
    "Moneda", "MontoCLP", "Esencial", "Fijo",
    "Recurrente", "Extraordinario", "Excluido", "Notas",
]

# Última columna que el bot escribe en update. Todo lo de la col 17 en adelante
# es del usuario y NO se toca.
_LAST_BOT_COL = SHEET_HEADER.index("Cuota a pagar") + 1  # 16


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


# Mapping del header oficial → columna 1-indexed (lo necesita gspread para update).
_COL_CATEGORIA = SHEET_HEADER.index("Categoría") + 1     # 12
_COL_SUBCATEGORIA = SHEET_HEADER.index("Subcategoría") + 1  # 13
_COL_FECHA = SHEET_HEADER.index("Fecha") + 1             # 1
_COL_DESCRIPCION = SHEET_HEADER.index("Descripción") + 1  # 8
_COL_MONTO = SHEET_HEADER.index("Monto") + 1             # 9


def _find_existing_row(sheet, fecha_dmy: str, descripcion: str, monto_abs: float) -> int | None:
    """Busca el row 1-indexed cuya fila coincida con (fecha DD/MM/YYYY, descripción, monto).
    Devuelve None si no encuentra. Skipea fila 1 (header).

    Normaliza la descripción (case + espacios + NBSP) para tolerar diferencias
    accidentales entre el bot y filas escritas por otros procesos."""
    try:
        all_rows = sheet.get_all_values()
    except Exception as e:
        log.warning(f"GSheet get_all_values falló: {e}")
        return None
    target_fecha = (fecha_dmy or "").strip()
    target_desc_norm = _norm_desc(descripcion or "")
    # Si la descripción venía con prefijo `'` (anti-fórmula), el sheet lo guarda
    # sin él pero `get_all_values` puede devolverlo según contexto. Normalizamos.
    if target_desc_norm.startswith("'"):
        target_desc_norm = target_desc_norm[1:]
    # gsheet guarda monto como número; al leer get_all_values vuelve como string.
    # Comparamos como float para evitar problemas con formato (',' vs '.').
    try:
        target_monto = float(monto_abs)
    except Exception:
        target_monto = 0.0

    for i, row in enumerate(all_rows[1:], start=2):  # i empieza en 2 (fila 2 = primer data)
        if len(row) < max(_COL_FECHA, _COL_DESCRIPCION, _COL_MONTO):
            continue
        f = row[_COL_FECHA - 1].strip()
        d_norm = _norm_desc(row[_COL_DESCRIPCION - 1])
        if d_norm.startswith("'"):
            d_norm = d_norm[1:]
        m_raw = row[_COL_MONTO - 1].strip().replace(".", "").replace(",", ".")
        try:
            m = float(m_raw)
        except (ValueError, TypeError):
            continue
        if f == target_fecha and d_norm == target_desc_norm and abs(m - target_monto) < 0.5:
            return i
    return None


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


def upsert_movement(mov: dict) -> None:
    """Añade o actualiza una fila en el sheet, según si el movimiento ya existe.

    Identifica la fila existente por triple (fecha DD/MM/YYYY, descripción,
    monto absoluto) — único en la práctica. Si la encuentra, actualiza solo
    Categoría y Subcategoría (preservando el resto del row, ej. si el usuario
    editó algo a mano en Saldo). Si no la encuentra, append al final.

    Esto permite que cuando el usuario corrige un movimiento aprobado (con el
    botón "Corregir nuevamente"), la fila vieja del sheet se actualice en
    lugar de duplicarse.

    Lanza excepción al caller si falla (igual que el viejo append_movement).
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

        cat = _safe_text(mov.get("final_category") or mov.get("suggested_category") or "")
        sub = _safe_text(mov.get("final_subcategory") or mov.get("suggested_subcategory") or "")
        persona = _normalize_persona(mov.get("persona"))
        descripcion = _safe_text(mov.get("description") or "")

        # Metadata de cuotas (solo se llena cuando hay >1 cuota).
        cuotas_actual = mov.get("cuotas_actual")
        cuotas_total = mov.get("cuotas_total")
        cuota_monto = mov.get("cuota_monto")
        cuota_actual_cell = cuotas_actual if (cuotas_total and cuotas_total > 1) else ""
        cuotas_total_cell = cuotas_total if (cuotas_total and cuotas_total > 1) else ""
        cuota_pagar_cell = abs(cuota_monto) if (cuota_monto and cuotas_total and cuotas_total > 1) else ""

        # Saldo: hoy solo BCh lo trae (cuenta corriente). Falabella es tarjeta
        # de crédito → no aplica saldo (queda en None → celda vacía).
        saldo = mov.get("saldo")
        saldo_cell = saldo if saldo is not None else ""

        existing_row = _find_existing_row(sheet, fecha, descripcion, monto_abs)

        if existing_row:
            # Update SOLO de las cols del bot: Categoría (L), Subcategoría (M),
            # Cuota actual (N), Cuotas total (O), Cuota a pagar (P). Las cols
            # 17-24 (Moneda, MontoCLP, Esencial, etc.) son del dashboard del
            # usuario — NO se tocan, preservan cualquier fórmula o valor manual.
            sheet.update(
                f"L{existing_row}:P{existing_row}",
                [[cat, sub, cuota_actual_cell, cuotas_total_cell, cuota_pagar_cell]],
                value_input_option="USER_ENTERED",
            )
            log.info(f"GSheet UPDATE row {existing_row}: {fecha} · {descripcion[:40]} → {cat}/{sub}")
        else:
            # Append: las cols 17-24 (dashboard del usuario) van vacías. Si tu
            # dashboard tiene fórmulas (ej. =F2 = Banco) que se autoaplican a
            # filas nuevas, se siguen evaluando porque las cols 1-13 sí están.
            row = [
                fecha,                                         # 1.  Fecha (DD/MM/YYYY)
                dia,                                           # 2.  Día (número)
                mes,                                           # 3.  Mes (número)
                ano,                                           # 4.  Año (número)
                dia_semana,                                    # 5.  Día Semana (texto)
                (mov.get("bank") or "").capitalize(),          # 6.  Banco
                persona,                                       # 7.  Persona
                descripcion,                                   # 8.  Descripción
                monto_abs,                                     # 9.  Monto
                tipo,                                          # 10. Tipo (Abono/Cargo)
                saldo_cell,                                    # 11. Saldo (BCh; vacío para Falabella)
                cat,                                           # 12. Categoría
                sub,                                           # 13. Subcategoría
                cuota_actual_cell,                             # 14. Cuota actual
                cuotas_total_cell,                             # 15. Cuotas total
                cuota_pagar_cell,                              # 16. Cuota a pagar
                # 17-24 (dashboard del usuario): el bot deja vacío.
                "", "", "", "", "", "", "", "",
            ]
            sheet.append_row(row, value_input_option="USER_ENTERED")
            log.info(f"GSheet APPEND: {fecha} · {descripcion[:40]} → {cat}/{sub}")
    except Exception:
        log.exception("GSheet falló al hacer upsert")
        raise


# Alias retrocompatible: append_movement ahora hace upsert (idempotente para los
# call sites existentes y para que la corrección post-aprobación actualice en
# vez de duplicar).
def append_movement(mov: dict) -> None:
    upsert_movement(mov)
