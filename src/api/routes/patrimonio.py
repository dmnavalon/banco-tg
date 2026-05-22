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

from flask import Blueprint, jsonify

from ... import db
from ...utils import get_logger
from ..auth import require_token

log = get_logger("api.patrimonio")

bp = Blueprint("patrimonio", __name__, url_prefix="/api/patrimonio")


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
