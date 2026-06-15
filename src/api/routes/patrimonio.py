"""Endpoints HTTP para la sección Patrimonio del dashboard.

POST /api/patrimonio/sync   → escribe request a Firestore, devuelve 202 + job_id
GET  /api/patrimonio/status → lee estado de Firestore (running, summary, etc.)

Patrimonio scrapers solo pueden correr en la Mac de Diego (necesitan Keychain
y archivos `state_*.json.enc` locales). Para que el botón "Actualizar ahora"
del dashboard de producción funcione, usamos Firestore como buzón:

  Railway: POST /sync → db.request_patrimonio_sync() (escribe last_request_at)
  Mac:     daemon polea Firestore cada 30s, si hay request nuevo corre run_all()
  Railway: GET /status → lee db.get_patrimonio_state() (running, summary, ...)
  Dashboard: polea GET /status cada 5s hasta que last_processed_at > started_at

El campo `daemon_heartbeat_at` permite al dashboard advertir si la Mac de
Diego está dormida o el daemon caído (heartbeat > 2 min = problema).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from ... import db
from ...utils import get_logger
from ..auth import require_token

log = get_logger("api.patrimonio")

bp = Blueprint("patrimonio", __name__, url_prefix="/api/patrimonio")
deudas_bp = Blueprint("deudas", __name__, url_prefix="/api/deudas")
inversiones_bp = Blueprint("inversiones", __name__, url_prefix="/api/inversiones")


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


@bp.post("/sync")
@require_token
def sync_now():
    """Encola un sync. Si ya hay uno running, rechaza con 409."""
    try:
        state = db.get_patrimonio_state()
    except Exception as e:
        log.exception("Patrimonio sync: error leyendo Firestore")
        return jsonify({"error": "firestore_unavailable", "message": str(e)}), 503

    if state.get("running"):
        return jsonify({
            "status": "already_running",
            "started_at": state.get("started_at"),
        }), 409

    try:
        request_at = db.request_patrimonio_sync()
    except Exception as e:
        log.exception("Patrimonio sync: error escribiendo request a Firestore")
        return jsonify({"error": "firestore_unavailable", "message": str(e)}), 503

    return jsonify({
        "status": "queued",
        "request_at": request_at,
        "note": "El daemon en la Mac de Diego polea Firestore cada 30s y procesará este request.",
    }), 202


@bp.get("/status")
@require_token
def status():
    try:
        state = db.get_patrimonio_state()
    except Exception as e:
        log.exception("Patrimonio status: error leyendo Firestore")
        return jsonify({"error": "firestore_unavailable", "message": str(e)}), 503

    # Asegurar que ciertos campos siempre existan para que el cliente no
    # tenga que defenderse de undefined.
    out = {
        "running": bool(state.get("running")),
        "started_at": state.get("started_at"),
        "last_request_at": state.get("last_request_at"),
        "last_processed_at": state.get("last_processed_at"),
        "daemon_heartbeat_at": state.get("daemon_heartbeat_at"),
        "summary": state.get("summary"),
        "error": state.get("error"),
    }
    return jsonify(out)


# ── Snapshots de patrimonio (entrada manual desde el dashboard) ───────────

@bp.get("/snapshots")
@require_token
def patrimonio_snapshots():
    return jsonify({"items": db.get_patrimonio_snapshots()})


@bp.put("/snapshots/<mes>")
@require_token
def upsert_patrimonio_snapshot(mes: str):
    p = request.get_json(silent=True) or {}
    db.set_patrimonio_snapshot(mes, {
        "caja_liquida": _num(p.get("caja_liquida")),
        "activos_invertidos": _num(p.get("activos_invertidos")),
        "activos_iliquidos": _num(p.get("activos_iliquidos")),
        "activos_totales": _num(p.get("activos_totales")),
        "pasivos_totales": _num(p.get("pasivos_totales")),
        "patrimonio_neto": _num(p.get("patrimonio_neto")),
        "notas": str(p.get("notas") or ""),
    })
    return jsonify({"ok": True, "mes": mes}), 200


# ── Deudas (maestro + snapshots mensuales, entrada manual) ────────────────

@deudas_bp.get("/maestro")
@require_token
def deudas_maestro():
    return jsonify({"items": db.get_deudas_maestro()})


@deudas_bp.put("/maestro/<deuda_id>")
@require_token
def upsert_deuda_maestro(deuda_id: str):
    p = request.get_json(silent=True) or {}
    db.set_deuda_maestro(deuda_id, {
        "institucion": str(p.get("institucion") or ""),
        "tipo": str(p.get("tipo") or ""),
        "moneda": str(p.get("moneda") or "CLP"),
        "saldo_original": _num(p.get("saldo_original")),
        "tasa_anual": _num(p.get("tasa_anual")),
        "cuota": _num(p.get("cuota")),
        "cuotas_restantes": _num(p.get("cuotas_restantes")),
        "proximo_vencimiento": str(p.get("proximo_vencimiento") or ""),
        "activa": bool(p.get("activa", True)),
    })
    return jsonify({"ok": True, "id": deuda_id}), 200


@deudas_bp.get("/snapshots")
@require_token
def deudas_snapshots():
    return jsonify({"items": db.get_deudas_snapshots()})


@deudas_bp.put("/snapshots/<mes>/<deuda_id>")
@require_token
def upsert_deuda_snapshot(mes: str, deuda_id: str):
    p = request.get_json(silent=True) or {}
    db.set_deuda_snapshot(mes, deuda_id, {
        "saldo_actual": _num(p.get("saldo_actual")),
        "saldo_clp": _num(p.get("saldo_clp")),
        "intereses_pagados_mes": _num(p.get("intereses_pagados_mes")),
        "capital_pagado_mes": _num(p.get("capital_pagado_mes")),
    })
    return jsonify({"ok": True, "mes": mes, "id": deuda_id}), 200


# ── Inversiones (solo lectura; las escriben los scrapers vía firestore_writer) ─

@inversiones_bp.get("/maestro")
@require_token
def inversiones_maestro():
    return jsonify({"items": db.get_inversiones_maestro()})


@inversiones_bp.get("/snapshots")
@require_token
def inversiones_snapshots():
    return jsonify({"items": db.get_inversiones_snapshots()})
