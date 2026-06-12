"""Deduplica movimientos en Firestore + GSheet.

Causa raíz documentada: entre commits `4055587` y `2eb0a82` (mayo 2026), el
parser de Falabella incluía sufijos `(N/M)` en la descripción. Al quitarlos,
los mismos movimientos físicos generaron un segundo doc en Firestore con un
hash distinto. Este script:

  1. Agrupa por clave canónica (date, amount, bank, account, canon_desc).
  2. Elige un winner por grupo (más progreso: sheet_row_id > final_category >
     review_status approved-like > inserted_at más viejo).
  3. Merge soft: copia campos no nulos del loser al winner antes de borrar
     (no perdés categoría/comercio/etc. si el loser tenía más info).
  4. Borra losers de Firestore.
  5. Borra filas del GSheet apuntadas por loser.sheet_row_id (si las hay).
  6. Genera un backup completo y un log JSON con el plan.

Uso:
  python -m scripts.dedupe_movements                 # dry-run (default)
  python -m scripts.dedupe_movements --apply         # ejecuta
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import db
from src.utils import canonical_description, get_logger, movement_id, normalize, project_path

log = get_logger("dedupe")


_APPROVED_STATUSES = {"approved", "corrected_approved"}


def _is_test_category(cat: str | None) -> bool:
    """Filtro defensivo: descarta cats tipo `QA-PW-xxx` que vinieron de scripts de prueba."""
    if not cat:
        return False
    c = cat.strip().upper()
    return c.startswith("QA-") or c.startswith("TEST-") or c.startswith("DEBUG-")


def _real_final_category(doc: dict) -> str | None:
    cat = doc.get("final_category")
    if _is_test_category(cat):
        return None
    return cat


def _decision_time(doc: dict) -> str:
    """Cuándo se decidió por última vez la categoría. Usa el mayor entre
    corrected_at y decided_at. Inserted_at como tiebreaker."""
    return max(
        doc.get("corrected_at") or "",
        doc.get("decided_at") or "",
        doc.get("updated_at") or "",
        doc.get("inserted_at") or "",
    )


def _is_canonical_id(doc: dict) -> bool:
    """True si el id del doc coincide con el hash que produce el parser ACTUAL.
    Un doc no-canónico (ej. hash calculado con sufijo `(N/M)` en la descripción)
    no debe ganar nunca: si gana y se borra el canónico, el scrape diario
    re-crea el doc borrado con el hash actual y el duplicado reaparece — eso
    fue exactamente lo que pasó tras el run del 2026-05-21."""
    for dup_idx in range(3):
        try:
            expected = movement_id(
                date_iso=doc.get("date") or "",
                amount=float(doc.get("amount") or 0),
                description=doc.get("description") or "",
                bank=doc.get("bank") or "",
                account=doc.get("account"),
                dup_idx=dup_idx,
            )
        except Exception:
            return False
        if doc.get("id") == expected:
            return True
    return False


def _progress_score(doc: dict) -> tuple:
    """Score para elegir winner. Más alto = se queda. Tuplas comparan
    elemento a elemento, así que el orden expresa prioridad:

      1. Id canónico (re-generable por el parser actual — si gana otro, el
         scrape diario re-crea al borrado y el duplicado vuelve)
      2. Tener sheet_row_id (ya está en el GSheet — preservar esa fila)
      3. Tener final_category válida (no QA-*)
      4. Estar aprobado
      5. Tener comercio
      6. inserted_at más viejo (estabilidad histórica)
    """
    is_canonical = 1 if _is_canonical_id(doc) else 0
    has_sheet_row = 1 if doc.get("sheet_row_id") else 0
    has_final_cat = 1 if _real_final_category(doc) else 0
    is_approved = 1 if (doc.get("review_status") in _APPROVED_STATUSES
                        or doc.get("status") == "aprobado") else 0
    has_comercio = 1 if (doc.get("comercio_final") or doc.get("comercio")) else 0
    inserted = doc.get("inserted_at") or ""
    # inserted_at más viejo gana → negar para que score más alto = más viejo.
    inserted_neg = tuple(-ord(c) for c in inserted[:19])
    return (is_canonical, has_sheet_row, has_final_cat, is_approved, has_comercio, inserted_neg)


def _key_for(doc: dict) -> tuple:
    return (
        doc.get("date", ""),
        round(float(doc.get("amount") or 0), 2),
        (doc.get("bank") or "").lower(),
        normalize(doc.get("account") or ""),
        normalize(canonical_description(doc.get("description"))),
    )


_MERGE_FIELDS = [
    "final_category", "final_subcategory",
    "suggested_category", "suggested_subcategory",
    "comercio", "comercio_final",
    "tipo", "persona",
    "cuotas_actual", "cuotas_total", "cuota_monto", "saldo",
    "tg_photo_file_id", "raw_blob",
    "ignore_reason", "comment", "pregunta_sugerida",
    # OJO: sheet_row_id NO se mergea — la fila del loser se borra; la fila
    # correcta del winner la fija _reindex_sheet_row_ids al final.
]


def _build_merge_patch(winner: dict, losers: list[dict]) -> dict:
    """Estrategia de merge:

      * Campos vacíos del winner se rellenan con el primer loser que tenga valor.
      * `final_category`/`final_subcategory`: si winner y loser ambos tienen
        valor pero distinto, gana el de `decision_time` más reciente (refleja
        la última decisión del usuario). Cat tipo `QA-*` se ignoran.
      * Si el patch sobrescribe `final_category` del winner, se marca con
        `_resync_gsheet=True` (no se escribe a Firestore — lo usa apply_plan
        para decidir si re-upsert al sheet).
    """
    patch: dict[str, Any] = {}

    # Fill-nulls primero.
    for f in _MERGE_FIELDS:
        if winner.get(f) not in (None, ""):
            continue
        for L in losers:
            v = L.get(f)
            if f == "final_category" and _is_test_category(v):
                continue
            if v not in (None, ""):
                patch[f] = v
                break

    # Override de categoría si hay decisión más fresca en un loser.
    # Guard: una corrección humana del winner (corrected_by seteado) solo puede
    # ser pisada por otra corrección humana — nunca por una clasificación
    # automática del loser que casualmente tenga timestamp más nuevo (ej. por
    # un resync).
    w_decision = _decision_time(winner)
    w_cat = _real_final_category(winner)
    w_sub = winner.get("final_subcategory")
    w_human = bool(winner.get("corrected_by"))
    best_loser_cat = None
    best_loser_sub = None
    best_decision = w_decision
    for L in losers:
        l_cat = _real_final_category(L)
        if not l_cat:
            continue
        if w_human and not L.get("corrected_by"):
            continue
        l_decision = _decision_time(L)
        if l_decision > best_decision and l_cat != w_cat:
            best_decision = l_decision
            best_loser_cat = l_cat
            best_loser_sub = L.get("final_subcategory")
    if best_loser_cat:
        patch["final_category"] = best_loser_cat
        if best_loser_sub:
            patch["final_subcategory"] = best_loser_sub
        else:
            # Si el loser corrigió a una cat distinta, no asumimos que la sub vieja sirve.
            patch["final_subcategory"] = None
        patch["_resync_gsheet"] = True  # señal in-memory, se filtra antes del update

    return patch


def _backup_collection(client) -> str:
    """Dump completo de la collection movements a JSON local antes de mutar."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = project_path("logs", f"backup_movements_{ts}.json")
    docs = list(client.collection("movements").get())
    data = [d.to_dict() for d in docs]
    out_path.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=2))
    log.info(f"Backup: {len(data)} docs → {out_path}")
    return str(out_path)


def _delete_gsheet_row(sheet, row_idx: int) -> None:
    """Borra fila por índice 1-based. Usa delete_rows (gspread)."""
    sheet.delete_rows(row_idx)


def plan_dedupe(client) -> dict:
    docs = [d.to_dict() for d in client.collection("movements").get()]
    log.info(f"Total movs en Firestore: {len(docs)}")

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for d in docs:
        groups[_key_for(d)].append(d)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    log.info(f"Grupos duplicados: {len(dup_groups)}  ·  Docs a borrar: "
             f"{sum(len(v)-1 for v in dup_groups.values())}")

    plan = {
        "generated_at": datetime.now().isoformat(),
        "total_docs": len(docs),
        "dup_groups": len(dup_groups),
        "delete_count": 0,
        "groups": [],
    }
    for key, members in sorted(dup_groups.items(), key=lambda x: x[0]):
        ranked = sorted(members, key=_progress_score, reverse=True)
        winner = ranked[0]
        losers = ranked[1:]
        patch = _build_merge_patch(winner, losers)
        plan["groups"].append({
            "key": {
                "date": key[0], "amount": key[1], "bank": key[2],
                "account": key[3], "canon_desc": key[4],
            },
            "winner": {
                "id": winner.get("id"),
                "description": winner.get("description"),
                "status": winner.get("status"),
                "review_status": winner.get("review_status"),
                "final_category": winner.get("final_category"),
                "sheet_row_id": winner.get("sheet_row_id"),
                "inserted_at": winner.get("inserted_at"),
            },
            "losers": [
                {
                    "id": L.get("id"),
                    "description": L.get("description"),
                    "status": L.get("status"),
                    "review_status": L.get("review_status"),
                    "final_category": L.get("final_category"),
                    "sheet_row_id": L.get("sheet_row_id"),
                    "inserted_at": L.get("inserted_at"),
                }
                for L in losers
            ],
            "merge_patch": patch,
        })
        plan["delete_count"] += len(losers)
    return plan


def apply_plan(client, plan: dict, extra_rows: list[int] | None = None) -> dict:
    """Aplica el plan. `extra_rows` son filas duplicadas del GSheet detectadas
    aparte (sin doc Firestore loser); se borran en la MISMA pasada descendente
    que las filas de losers — borrar en dos pasadas separadas corre los índices
    y termina borrando filas equivocadas."""
    from src import gsheet
    sheet = None
    try:
        sheet = gsheet._client().open_by_key(gsheet.SPREADSHEET_ID).worksheet(gsheet.SHEET_NAME)
    except Exception as e:
        log.warning(f"No pude abrir GSheet — se omite limpieza de filas: {e}")

    rows_to_delete: list[int] = list(extra_rows or [])
    resync_winners: list[str] = []
    results = {
        "updated": 0, "deleted": 0,
        "gsheet_rows_deleted": 0, "gsheet_rows_updated": 0,
        "errors": [],
    }

    for grp in plan["groups"]:
        winner_id = grp["winner"]["id"]
        raw_patch = dict(grp["merge_patch"])
        needs_resync = bool(raw_patch.pop("_resync_gsheet", False))
        try:
            if raw_patch:
                client.collection("movements").document(winner_id).update(raw_patch)
                results["updated"] += 1
                log.info(f"MERGE → {winner_id}: {list(raw_patch.keys())}")
        except Exception as e:
            results["errors"].append({"op": "merge", "winner": winner_id, "err": str(e)})
            log.exception(f"Merge falló para {winner_id}")

        # upsert_movement busca la fila por columna MovementId, no por
        # sheet_row_id (que puede estar nulo o corrido) — resync siempre.
        if needs_resync:
            resync_winners.append(winner_id)

        for L in grp["losers"]:
            lid = L["id"]
            sheet_row = L.get("sheet_row_id")
            try:
                client.collection("movements").document(lid).delete()
                results["deleted"] += 1
                log.info(f"DELETE loser → {lid}  (sheet_row={sheet_row})")
            except Exception as e:
                results["errors"].append({"op": "delete", "loser": lid, "err": str(e)})
                log.exception(f"Delete falló para {lid}")
            if isinstance(sheet_row, int) and sheet_row != grp["winner"].get("sheet_row_id"):
                rows_to_delete.append(sheet_row)

    if sheet and rows_to_delete:
        for r in sorted(set(rows_to_delete), reverse=True):
            try:
                _delete_gsheet_row(sheet, r)
                results["gsheet_rows_deleted"] += 1
                log.info(f"GSheet DELETE row {r}")
                time.sleep(1.1)
            except Exception as e:
                results["errors"].append({"op": "gsheet_delete", "row": r, "err": str(e)})
                log.exception(f"GSheet delete falló para row {r}")

    # Re-upsert al GSheet de winners cuya categoría se sobrescribió tras el merge.
    if resync_winners:
        for wid in resync_winners:
            try:
                doc = client.collection("movements").document(wid).get().to_dict()
                if not doc:
                    continue
                gsheet.upsert_movement(doc)
                results["gsheet_rows_updated"] += 1
                log.info(f"GSheet RESYNC winner {wid} → cat={doc.get('final_category')}")
                time.sleep(1.1)
            except Exception as e:
                results["errors"].append({"op": "gsheet_resync", "id": wid, "err": str(e)})
                log.exception(f"Resync GSheet falló para {wid}")
    return results


def _reindex_sheet_row_ids(client, sheet) -> dict:
    """Tras borrar filas, los sheet_row_id de TODOS los docs con fila debajo
    quedan corridos. Lee la columna MovementId completa y corrige en Firestore
    los que cambiaron. Idempotente."""
    from src import gsheet
    results = {"checked": 0, "fixed": 0, "errors": []}
    ID = gsheet._COL_MOVEMENT_ID - 1
    all_rows = sheet.get_all_values()
    row_by_id: dict[str, int] = {}
    for i, row in enumerate(all_rows[1:], start=2):
        mid = (row[ID] if len(row) > ID else "").lstrip("'").strip()
        if mid and mid not in row_by_id:
            row_by_id[mid] = i

    for d in client.collection("movements").get():
        doc = d.to_dict()
        mid = doc.get("id") or d.id
        current = doc.get("sheet_row_id")
        actual = row_by_id.get(mid)
        if actual is None:
            continue
        results["checked"] += 1
        if current != actual:
            try:
                client.collection("movements").document(d.id).update({"sheet_row_id": actual})
                results["fixed"] += 1
                log.info(f"REINDEX sheet_row_id {mid}: {current} → {actual}")
            except Exception as e:
                results["errors"].append({"op": "reindex", "id": mid, "err": str(e)})
    log.info(f"Re-index sheet_row_id: {results['fixed']} corregidos de {results['checked']} con fila.")
    return results


def _detect_gsheet_extra_dupes(client, plan: dict) -> list[dict]:
    """Busca filas duplicadas EN EL GSHEET por (fecha, descripción canónica, monto)
    que no fueron capturadas por sheet_row_id (movs legacy sin id sincronizado)."""
    from src import gsheet
    try:
        sheet = gsheet._client().open_by_key(gsheet.SPREADSHEET_ID).worksheet(gsheet.SHEET_NAME)
        all_rows = sheet.get_all_values()
    except Exception as e:
        log.warning(f"No pude leer GSheet para detección extra: {e}")
        return []

    F = gsheet._COL_FECHA - 1
    D = gsheet._COL_DESCRIPCION - 1
    M = gsheet._COL_MONTO - 1
    ID = gsheet._COL_MOVEMENT_ID - 1

    grouped: dict[tuple, list[tuple[int, list[str]]]] = defaultdict(list)
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) <= max(F, D, M):
            continue
        fecha = row[F].strip()
        desc_norm = normalize(canonical_description(row[D]))
        try:
            monto_raw = row[M].strip().replace(".", "").replace(",", ".")
            monto = round(float(monto_raw), 2)
        except (ValueError, TypeError):
            continue
        grouped[(fecha, desc_norm, monto)].append((i, row))

    # Ids y filas de losers ya planificados en Firestore: sus filas se borran
    # por el plan principal, y sus MovementIds dejarán de existir — una fila
    # que apunte a un loser nunca puede ser winner aquí.
    planned_loser_ids = {L["id"] for g in plan["groups"] for L in g["losers"]}
    planned_loser_rows = {
        L["sheet_row_id"] for g in plan["groups"] for L in g["losers"]
        if isinstance(L.get("sheet_row_id"), int)
    }

    extras: list[dict] = []
    for key, rows in grouped.items():
        if len(rows) <= 1:
            continue
        # Winner: primera fila con MovementId que NO sea un loser planificado;
        # si no hay, primera con MovementId; si ninguna, la primera.
        ids = [(idx, (r[ID] if len(r) > ID else "").lstrip("'").strip()) for idx, r in rows]
        surviving = [t for t in ids if t[1] and t[1] not in planned_loser_ids]
        with_id = [t for t in ids if t[1]]
        if surviving:
            winner_row = surviving[0][0]
        elif with_id:
            winner_row = with_id[0][0]
        else:
            winner_row = rows[0][0]
        losers = [idx for idx, _ in rows
                  if idx != winner_row and idx not in planned_loser_rows]
        if not losers:
            continue
        extras.append({
            "key": {"fecha": key[0], "desc_canon": key[1], "monto": key[2]},
            "winner_row": winner_row,
            "loser_rows": losers,
        })
    return extras


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Ejecuta la limpieza (sin esto, dry-run)")
    parser.add_argument("--skip-gsheet-extra", action="store_true",
                        help="No buscar duplicados extra solo en GSheet (filas sin contraparte Firestore).")
    args = parser.parse_args()

    db.init_if_needed()
    client = db._db()

    plan = plan_dedupe(client)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan_path = project_path("logs", f"dedupe_plan_{ts}.json")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, default=str, indent=2))
    log.info(f"Plan: {plan_path}")
    log.info(f"  → grupos duplicados: {plan['dup_groups']}")
    log.info(f"  → docs a borrar:     {plan['delete_count']}")
    merges = sum(1 for g in plan["groups"] if g["merge_patch"])
    log.info(f"  → merges con patch:  {merges}")

    extras: list[dict] = []
    if not args.skip_gsheet_extra:
        extras = _detect_gsheet_extra_dupes(client, plan)
        log.info(f"GSheet — duplicados extra detectados (filas sin movement_id consistente): {len(extras)}")
        if extras:
            extras_path = project_path("logs", f"dedupe_gsheet_extras_{ts}.json")
            extras_path.write_text(json.dumps(extras, ensure_ascii=False, default=str, indent=2))
            log.info(f"Extras GSheet: {extras_path}")

    if not args.apply:
        log.info("DRY-RUN — no se modificó nada. Rerun con --apply para ejecutar.")
        return 0

    _backup_collection(client)
    extra_rows = sorted({r for e in extras for r in e["loser_rows"]})
    results = apply_plan(client, plan, extra_rows=extra_rows)

    # Re-index: tras borrar filas, los sheet_row_id almacenados quedan corridos.
    try:
        from src import gsheet
        sheet = gsheet._client().open_by_key(gsheet.SPREADSHEET_ID).worksheet(gsheet.SHEET_NAME)
        results["reindex"] = _reindex_sheet_row_ids(client, sheet)
    except Exception as e:
        log.warning(f"Re-index de sheet_row_id falló: {e}")
        results["errors"].append({"op": "reindex_global", "err": str(e)})

    results_path = project_path("logs", f"dedupe_results_{ts}.json")
    results_path.write_text(json.dumps(results, ensure_ascii=False, default=str, indent=2))
    log.info(f"Resultados: {results_path}")
    log.info(f"  → docs actualizados:  {results['updated']}")
    log.info(f"  → docs borrados:      {results['deleted']}")
    log.info(f"  → filas GSheet del:   {results['gsheet_rows_deleted']}")
    log.info(f"  → errores:            {len(results['errors'])}")
    return 0 if not results["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
