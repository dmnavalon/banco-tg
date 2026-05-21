"""Setup de pestañas y columnas para el Dashboard de Finanzas Personales.

Pasos del plan de habilitación incremental:
  1. Crear/poblar pestaña TaxonomíaExtendida (fuente de verdad sobre el classifier).
  2. Extender Movimientos a 21 columnas + backfill (Moneda=CLP, MontoCLP=Monto,
     fórmulas VLOOKUP a TaxonomíaExtendida para Esencial/Fijo).
  3-11. Crear pestañas estructurales vacías (solo headers) para que el usuario las pueble:
        TipoCambio, Presupuesto, Deudas_Maestro, Deudas_Snapshot,
        Inversiones_Maestro (renombre de Inversiones), Inversiones_Snapshot,
        InversionesObjetivo, ActivosIlíquidos, Patrimonio, Metas,
        IngresosEsperados, EgresosEsperados.

Reentrante: pestañas existentes con data no se sobrescriben; las que están vacías
o no existen se crean/inicializan.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SPREADSHEET_ID = "1bcH0Hu2_z_yVxZY3BuTkGaDzlsQQZYRCD1ayY-Pb6XM"

# Header oficial post-migración 2026-05-08: 24 columnas.
# Las primeras 13 las escribe el bot (gsheet.py). Las cols 14-16 (Cuota actual,
# Cuotas total, Cuota a pagar) las llena también el bot cuando hay info de cuotas.
# Las cols 17-24 (Q-X) las gestiona este script y se rellenan automáticamente
# vía fórmulas/defaults para soportar el dashboard.
MOVIMIENTOS_NUEVAS_COL = [
    "Moneda", "MontoCLP", "Esencial", "Fijo", "Recurrente",
    "Extraordinario", "Excluido", "Notas",
]
# Rango donde viven esas 8 cols (post-migración)
DASH_COLS_START = "Q"  # 17
DASH_COLS_END = "X"    # 24

TAXONOMIA_HEADER = [
    "Categoría", "Subcategoría", "Esencial", "Fijo",
    "RecurrentePorDefecto", "TipoMovimiento",
]

# Defaults — combinan la taxonomía oficial del classifier con las reglas
# de la sección K del spec funcional.
# Subcategoría vacía = aplica a toda la categoría.
TAXONOMIA_DEFAULTS: list[tuple[str, str, str, str, str, str]] = [
    # INGRESOS
    ("Sueldo",                       "", "FALSE", "TRUE",  "TRUE",  "Ingreso"),
    ("Honorarios",                   "", "FALSE", "FALSE", "TRUE",  "Ingreso"),
    ("Dividendos y utilidades",      "", "FALSE", "FALSE", "FALSE", "Ingreso"),
    ("Inversiones",                  "", "FALSE", "FALSE", "FALSE", "Ingreso"),
    ("Arriendos",                    "", "FALSE", "TRUE",  "TRUE",  "Ingreso"),
    ("Reembolsos",                   "", "FALSE", "FALSE", "FALSE", "Devolución"),
    ("Otros ingresos",               "", "FALSE", "FALSE", "FALSE", "Ingreso"),
    # GASTOS — esenciales + fijos
    ("Vivienda",                     "", "TRUE",  "TRUE",  "TRUE",  "GastoReal"),
    ("Servicios básicos",            "", "TRUE",  "TRUE",  "TRUE",  "GastoReal"),
    ("Salud y seguros",              "", "TRUE",  "TRUE",  "TRUE",  "GastoReal"),
    ("Educación",                    "", "TRUE",  "TRUE",  "TRUE",  "GastoReal"),
    ("Servicios domésticos",         "", "TRUE",  "TRUE",  "TRUE",  "GastoReal"),
    ("Finanzas e impuestos",         "", "TRUE",  "TRUE",  "TRUE",  "GastoReal"),
    # GASTOS — esenciales + variables
    ("Hogar y alimentación",         "", "TRUE",  "FALSE", "TRUE",  "GastoReal"),
    ("Niños",                        "", "TRUE",  "FALSE", "TRUE",  "GastoReal"),
    ("Transporte",                   "", "TRUE",  "FALSE", "TRUE",  "GastoReal"),
    ("Mascotas",                     "", "TRUE",  "FALSE", "TRUE",  "GastoReal"),
    # GASTOS — discrecionales
    ("Vestuario y cuidado personal", "", "FALSE", "FALSE", "TRUE",  "GastoReal"),
    ("Entretención y vida social",   "", "FALSE", "FALSE", "TRUE",  "GastoReal"),
    ("Deporte y bienestar",          "", "FALSE", "FALSE", "TRUE",  "GastoReal"),
    ("Tecnología",                   "", "FALSE", "FALSE", "FALSE", "GastoReal"),
    ("Otros",                        "", "FALSE", "FALSE", "FALSE", "GastoReal"),
    # MOVIMIENTOS NO OPERATIVOS
    ("Transferencias internas",      "", "FALSE", "FALSE", "FALSE", "MovimientoInterno"),
    ("Ahorro e inversión",           "", "FALSE", "FALSE", "FALSE", "AporteInversión"),
    ("Gastos por rendir",            "", "FALSE", "FALSE", "FALSE", "GastoPorRendir"),
]


def _client() -> gspread.Client:
    key_path = os.environ.get("GSHEET_KEY_PATH", "").strip()
    if not key_path:
        sys.exit("ERROR: falta GSHEET_KEY_PATH en .env")
    creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    return gspread.authorize(creds)


def setup_taxonomia(sh: gspread.Spreadsheet) -> None:
    """Pobla TaxonomíaExtendida MERGEANDO defaults con filas manuales del usuario.

    Si la pestaña no existe → crea con defaults.
    Si existe → preserva toda fila cuya (Categoría, Subcategoría) ya esté presente
    (incluso si los defaults dicen otra cosa); agrega solo las filas faltantes.

    Antes el script hacía `ws.clear()` y reescribía, destruyendo edits manuales
    (ej. usuario marca "Niños/Ropa niños" como Esencial=TRUE).
    """
    mov = sh.worksheet("Movimientos")
    rows = mov.get_all_values()
    cats_reales: set[str] = set()
    if len(rows) > 1:
        for r in rows[1:]:
            if len(r) >= 13 and (r[11] or "").strip():
                cats_reales.add(r[11].strip())
    print(f"  → categorías detectadas en Movimientos: {sorted(cats_reales) or '(ninguna)'}")

    cubiertas = {t[0] for t in TAXONOMIA_DEFAULTS}
    faltantes = sorted(cats_reales - cubiertas)
    extra_rows: list[tuple[str, str, str, str, str, str]] = [
        (c, "", "FALSE", "FALSE", "FALSE", "GastoReal") for c in faltantes
    ]
    if faltantes:
        print(f"  → categorías sin defaults oficiales (se agregan conservadores): {faltantes}")

    default_rows = [tuple(t) for t in TAXONOMIA_DEFAULTS] + [tuple(t) for t in extra_rows]

    try:
        ws = sh.worksheet("TaxonomíaExtendida")
        existing = ws.get_all_values()
        # Indexar filas existentes por (categoría, subcategoría) — preservamos
        # cualquier customización del usuario.
        existing_keys: set[tuple[str, str]] = set()
        existing_data_rows: list[list[str]] = []
        for r in existing[1:] if existing else []:
            if not r or not (r[0] or "").strip():
                continue
            cat = (r[0] or "").strip()
            sub = (r[1] or "").strip() if len(r) > 1 else ""
            existing_keys.add((cat, sub))
            existing_data_rows.append(r + [""] * (6 - len(r)))
        new_rows = [list(t) for t in default_rows if (t[0], t[1]) not in existing_keys]
        if not existing or existing[0] != TAXONOMIA_HEADER:
            # Header faltante o mal: lo escribimos sin tocar las filas de data.
            ws.update("A1", [TAXONOMIA_HEADER], value_input_option="USER_ENTERED")
        if new_rows:
            # Append al final, sin tocar filas existentes.
            start_row = len(existing_data_rows) + 2
            ws.update(f"A{start_row}", new_rows, value_input_option="USER_ENTERED")
            print(f"  ✓ TaxonomíaExtendida: agregadas {len(new_rows)} filas faltantes (preservadas {len(existing_data_rows)} existentes)")
        else:
            print(f"  → TaxonomíaExtendida ya completa ({len(existing_data_rows)} filas) — no se modificó")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="TaxonomíaExtendida", rows=200, cols=10)
        body = [list(t) for t in default_rows]
        ws.update("A1", [TAXONOMIA_HEADER] + body, value_input_option="USER_ENTERED")
        print(f"  ✓ TaxonomíaExtendida creada con {len(body)} filas")

    ws.format("A1:F1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)


def extend_movimientos(sh: gspread.Spreadsheet) -> None:
    """Asegura que Movimientos tenga las 8 columnas Q-X del dashboard con sus fórmulas.
    Idempotente: si ya están presentes y bien formadas, no hace nada.

    Layout post-migración 2026-05-08 (24 col):
      A-M (1-13): bot · escribe bot.py / gsheet.py
      N-P (14-16): Cuota actual, Cuotas total, Cuota a pagar (las llena el bot)
      Q-X (17-24): este script · Moneda, MontoCLP, Esencial, Fijo, Recurrente,
                   Extraordinario, Excluido, Notas
    """
    ws = sh.worksheet("Movimientos")
    rows = ws.get_all_values()
    if not rows:
        print("  ⚠ Movimientos vacía, skip")
        return

    header = rows[0]
    expected_q_x = MOVIMIENTOS_NUEVAS_COL
    actual_q_x = header[16:24] if len(header) >= 24 else []
    if actual_q_x == expected_q_x:
        print("  → headers Q-X ya presentes — skip header write")
    else:
        if ws.col_count < 24:
            ws.resize(rows=ws.row_count, cols=max(24, ws.col_count))
        ws.update(range_name=f"{DASH_COLS_START}1:{DASH_COLS_END}1",
                  values=[MOVIMIENTOS_NUEVAS_COL], value_input_option="USER_ENTERED")
        ws.format(f"{DASH_COLS_START}1:{DASH_COLS_END}1", {"textFormat": {"bold": True}})
        print(f"  ✓ headers {DASH_COLS_START}-{DASH_COLS_END} escritos en Movimientos")

    # Backfill: una fila por movimiento existente.
    # OJO: estas fórmulas NO se sobrescriben en filas que ya tienen valores —
    # GSheet propaga las fórmulas a filas nuevas via auto-fill, así que solo
    # backfilleamos al setup inicial.
    n = len(rows) - 1
    if n <= 0:
        print("  → sin filas de data para backfill")
        return

    # Para Esencial/Fijo: VLOOKUP a TaxonomíaExtendida (col 3 y 4 respectivamente).
    # Para MontoCLP: si Moneda=CLP entonces Monto; sino, Monto * VLOOKUP a TipoCambio
    # (con IFERROR para que funcione antes de crear TipoCambio).
    # Referencias post-migración:
    #   $I{i} = Monto (sin cambio)
    #   $L{i} = Categoría (sin cambio)
    #   $Q{i} = Moneda (era $N{i} pre-migración)
    backfill = []
    for i, _ in enumerate(rows[1:], start=2):
        backfill.append([
            "CLP",                                                                                          # Q: Moneda
            f'=IF($Q{i}="CLP", $I{i}, IFERROR($I{i}*VLOOKUP($Q{i},TipoCambio!$B:$C,2,FALSE), $I{i}))',     # R: MontoCLP
            f'=IFERROR(VLOOKUP($L{i},TaxonomíaExtendida!$A:$F,3,FALSE), "FALSE")',                          # S: Esencial
            f'=IFERROR(VLOOKUP($L{i},TaxonomíaExtendida!$A:$F,4,FALSE), "FALSE")',                          # T: Fijo
            "FALSE",                                                                                        # U: Recurrente
            "FALSE",                                                                                        # V: Extraordinario
            "FALSE",                                                                                        # W: Excluido
            "",                                                                                             # X: Notas
        ])
    ws.update(range_name=f"{DASH_COLS_START}2:{DASH_COLS_END}{n+1}",
              values=backfill, value_input_option="USER_ENTERED")
    print(f"  ✓ backfill aplicado a {n} filas")


# Headers de las pestañas estructurales (vacías, solo headers — usuario pobla)
ESTRUCTURALES: list[tuple[str, list[str]]] = [
    ("TipoCambio", [
        "Fecha", "Moneda", "ValorCLP",
    ]),
    ("Presupuesto", [
        "Año", "Mes", "Categoría", "Subcategoría", "MontoCLP", "Notas",
    ]),
    ("Deudas_Maestro", [
        "ID", "Institución", "Tipo", "Moneda", "SaldoOriginal",
        "TasaAnual", "Cuota", "CuotasRestantes", "ProximoVencimiento", "Activa",
    ]),
    ("Deudas_Snapshot", [
        "Mes", "ID", "SaldoActual", "SaldoCLP",
        "InteresesPagadosMes", "CapitalPagadoMes",
    ]),
    ("Inversiones_Snapshot", [
        "Mes", "ID", "AportesDelMes", "RetirosDelMes",
        "ValorMonedaOrig", "TipoCambioCierre", "ValorCLP", "Notas",
    ]),
    ("InversionesObjetivo", [
        "ClaseDeActivo", "PorcentajeObjetivo", "ToleranciaPP",
    ]),
    ("ActivosIlíquidos", [
        "ID", "Tipo", "Descripción", "ValorEstimadoCLP", "FechaValuación", "Notas",
    ]),
    ("Patrimonio", [
        "Mes", "CajaLíquida", "ActivosInvertidos", "ActivosIlíquidos",
        "ActivosTotales", "PasivosTotales", "PatrimonioNeto", "Notas",
    ]),
    ("Metas", [
        "Tipo", "Descripción", "ValorObjetivoCLP", "FechaObjetivo",
        "ValorActual", "%Avance",
    ]),
    ("IngresosEsperados", [
        "Concepto", "MontoCLP", "FechaEstimada", "Frecuencia", "Confirmado",
    ]),
    ("EgresosEsperados", [
        "Concepto", "MontoCLP", "FechaEstimada", "Frecuencia", "Categoría", "Confirmado",
    ]),
]

# Header del maestro de Inversiones (la pestaña ya existe vacía y se renombra a Inversiones_Maestro)
INVERSIONES_MAESTRO_HEADER = [
    "ID", "Activo", "Clase", "Subclase", "Moneda", "País",
    "Institución", "Liquidez", "FechaInicio", "Activa",
]


def setup_inversiones_maestro(sh: gspread.Spreadsheet) -> None:
    """Renombra Inversiones (vacía) a Inversiones_Maestro y le pone headers.

    Si Inversiones tiene data o ya fue renombrada, no toca nada.
    """
    try:
        ws = sh.worksheet("Inversiones_Maestro")
        rows = ws.get_all_values()
        if rows and rows[0] == INVERSIONES_MAESTRO_HEADER:
            print("  → Inversiones_Maestro ya existe con headers correctos — skip")
            return
        # Existe pero sin headers (caso raro): los pone
        ws.update(range_name="A1", values=[INVERSIONES_MAESTRO_HEADER], value_input_option="USER_ENTERED")
        ws.format("A1:J1", {"textFormat": {"bold": True}})
        ws.freeze(rows=1)
        print("  ✓ Inversiones_Maestro · headers escritos")
        return
    except gspread.WorksheetNotFound:
        pass

    try:
        old = sh.worksheet("Inversiones")
        rows = old.get_all_values()
        has_data = any(any(c.strip() for c in r) for r in rows)
        if has_data:
            print("  ⚠ Inversiones ya tiene data — no se renombra. Crea Inversiones_Maestro manualmente o limpia primero.")
            return
        # Renombrar pestaña vacía
        old.update_title("Inversiones_Maestro")
        old.update(range_name="A1", values=[INVERSIONES_MAESTRO_HEADER], value_input_option="USER_ENTERED")
        old.format("A1:J1", {"textFormat": {"bold": True}})
        old.freeze(rows=1)
        print("  ✓ Inversiones renombrada a Inversiones_Maestro + headers escritos")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title="Inversiones_Maestro", rows=200, cols=15)
        ws.update(range_name="A1", values=[INVERSIONES_MAESTRO_HEADER], value_input_option="USER_ENTERED")
        ws.format("A1:J1", {"textFormat": {"bold": True}})
        ws.freeze(rows=1)
        print("  ✓ Inversiones_Maestro creada con headers")


def setup_estructural(sh: gspread.Spreadsheet, title: str, header: list[str]) -> None:
    """Crea pestaña con headers si no existe. Si existe con headers correctos, no toca."""
    try:
        ws = sh.worksheet(title)
        rows = ws.get_all_values()
        if rows and rows[0] == header:
            print(f"  → {title} ya OK · skip")
            return
        if rows and any(any(c.strip() for c in r) for r in rows):
            print(f"  ⚠ {title} tiene data inesperada — no se sobrescribe")
            return
        # Existe vacía: poner headers
        ws.update(range_name="A1", values=[header], value_input_option="USER_ENTERED")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=200, cols=max(10, len(header) + 2))
        ws.update(range_name="A1", values=[header], value_input_option="USER_ENTERED")

    last_col = chr(ord("A") + len(header) - 1)
    ws.format(f"A1:{last_col}1", {"textFormat": {"bold": True}})
    ws.freeze(rows=1)
    print(f"  ✓ {title} · {len(header)} columnas")


def main() -> None:
    print(f"[start] Setup dashboard sheets · spreadsheet {SPREADSHEET_ID}")
    sh = _client().open_by_key(SPREADSHEET_ID)

    print("\n[1/4] TaxonomíaExtendida")
    setup_taxonomia(sh)

    print("\n[2/4] Movimientos · extender a 21 columnas")
    extend_movimientos(sh)

    print("\n[3/4] Inversiones_Maestro (renombrar pestaña Inversiones vacía)")
    setup_inversiones_maestro(sh)

    print("\n[4/4] Pestañas estructurales restantes")
    for title, header in ESTRUCTURALES:
        setup_estructural(sh, title, header)

    print("\n[done] Setup completado.")
    print("Pestañas listas para que el usuario pueble: TipoCambio, Presupuesto,")
    print("Deudas_Maestro, Deudas_Snapshot, Inversiones_Maestro, Inversiones_Snapshot,")
    print("InversionesObjetivo, ActivosIlíquidos, Patrimonio, Metas,")
    print("IngresosEsperados, EgresosEsperados.")


if __name__ == "__main__":
    main()
