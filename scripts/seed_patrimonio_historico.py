"""Seedea a Firestore el histórico de patrimonio/deudas/inversiones desde sus
pestañas GSheet. Tras la migración el dashboard lee estas colecciones en vez
del Sheet.

Pestañas → colecciones:
  Patrimonio          → patrimonio_snapshots   (doc id = mes)
  Deudas_Maestro      → deudas_maestro          (doc id = id)
  Deudas_Snapshot     → deudas_snapshots        (doc id = mes_id)
  Inversiones_Maestro → inversiones_maestro     (doc id = id)
  Inversiones_Snapshot→ inversiones_snapshots   (doc id = mes_id)

Idempotente (upsert). Pestañas ausentes se saltan con aviso.

Uso:
    cd "Gestión de Gastos"
    .venv/bin/python -m scripts.seed_patrimonio_historico --dry-run  # default
    .venv/bin/python -m scripts.seed_patrimonio_historico --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gspread  # noqa: E402

from src import db  # noqa: E402
from src.gsheet import SPREADSHEET_ID, _client  # noqa: E402


def _num(s: str) -> float:
    s = (s or "").strip().replace("$", "").replace(".", "").replace(",", ".").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _bool(s: str) -> bool:
    return (s or "").strip().upper() in ("TRUE", "VERDADERO", "SÍ", "SI", "1")


def _g(row: list[str], i: int) -> str:
    return row[i].strip() if 0 <= i < len(row) else ""


def _read(ss, tab: str) -> list[list[str]]:
    try:
        return ss.worksheet(tab).get_all_values()[1:]
    except gspread.WorksheetNotFound:
        print(f"  ⚠️  pestaña '{tab}' no existe — se salta.")
        return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    ss = _client().open_by_key(SPREADSHEET_ID)
    writes: list[tuple] = []  # (descripción, callable)

    # Patrimonio: Mes, CajaLíquida, ActivosInvertidos, ActivosIlíquidos, ActivosTotales, PasivosTotales, PatrimonioNeto, Notas
    for r in _read(ss, "Patrimonio"):
        mes = _g(r, 0)
        if not mes:
            continue
        data = {
            "caja_liquida": _num(_g(r, 1)), "activos_invertidos": _num(_g(r, 2)),
            "activos_iliquidos": _num(_g(r, 3)), "activos_totales": _num(_g(r, 4)),
            "pasivos_totales": _num(_g(r, 5)), "patrimonio_neto": _num(_g(r, 6)),
            "notas": _g(r, 7),
        }
        writes.append((f"patrimonio_snapshots/{mes}", lambda mes=mes, d=data: db.set_patrimonio_snapshot(mes, d)))

    # Deudas_Maestro: Id, Institución, Tipo, Moneda, SaldoOriginal, TasaAnual, Cuota, CuotasRestantes, ProximoVencimiento, Activa
    for r in _read(ss, "Deudas_Maestro"):
        did = _g(r, 0)
        if not did:
            continue
        data = {
            "institucion": _g(r, 1), "tipo": _g(r, 2), "moneda": _g(r, 3) or "CLP",
            "saldo_original": _num(_g(r, 4)), "tasa_anual": _num(_g(r, 5)),
            "cuota": _num(_g(r, 6)), "cuotas_restantes": _num(_g(r, 7)),
            "proximo_vencimiento": _g(r, 8), "activa": _bool(_g(r, 9)),
        }
        writes.append((f"deudas_maestro/{did}", lambda did=did, d=data: db.set_deuda_maestro(did, d)))

    # Deudas_Snapshot: Mes, Id, SaldoActual, SaldoCLP, InteresesPagadosMes, CapitalPagadoMes
    for r in _read(ss, "Deudas_Snapshot"):
        mes, did = _g(r, 0), _g(r, 1)
        if not mes or not did:
            continue
        data = {
            "saldo_actual": _num(_g(r, 2)), "saldo_clp": _num(_g(r, 3)),
            "intereses_pagados_mes": _num(_g(r, 4)), "capital_pagado_mes": _num(_g(r, 5)),
        }
        writes.append((f"deudas_snapshots/{mes}_{did}", lambda mes=mes, did=did, d=data: db.set_deuda_snapshot(mes, did, d)))

    # Inversiones_Maestro: Id, Activo, Clase, Subclase, Moneda, País, Institución, Liquidez, FechaInicio, Activa
    for r in _read(ss, "Inversiones_Maestro"):
        iid = _g(r, 0)
        if not iid:
            continue
        data = {
            "activo": _g(r, 1), "clase": _g(r, 2), "subclase": _g(r, 3), "moneda": _g(r, 4) or "CLP",
            "pais": _g(r, 5), "institucion": _g(r, 6), "liquidez": _g(r, 7),
            "fecha_inicio": _g(r, 8), "activa": _bool(_g(r, 9)),
        }
        writes.append((f"inversiones_maestro/{iid}", lambda iid=iid, d=data: db.set_inversion_maestro(iid, d)))

    # Inversiones_Snapshot: Mes, Id, AportesDelMes, RetirosDelMes, ValorMonedaOrig, TipoCambioCierre, ValorCLP, Notas
    for r in _read(ss, "Inversiones_Snapshot"):
        mes, iid = _g(r, 0), _g(r, 1)
        if not mes or not iid:
            continue
        data = {
            "aportes_del_mes": _num(_g(r, 2)), "retiros_del_mes": _num(_g(r, 3)),
            "valor_moneda_orig": _num(_g(r, 4)), "tipo_cambio_cierre": _num(_g(r, 5)),
            "valor_clp": _num(_g(r, 6)), "notas": _g(r, 7),
        }
        writes.append((f"inversiones_snapshots/{mes}_{iid}", lambda mes=mes, iid=iid, d=data: db.set_inversion_snapshot(mes, iid, d)))

    print(f"\n{len(writes)} docs a escribir:")
    for desc, _ in writes:
        print(f"  → {desc}")

    if not apply:
        print(f"\n[dry-run] No se escribió nada. Corre con --apply para persistir {len(writes)} docs.")
        return

    for _, fn in writes:
        fn()
    print(f"\n✅ {len(writes)} docs escritos.")


if __name__ == "__main__":
    main()
