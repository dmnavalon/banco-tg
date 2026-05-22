"""Endpoints HTTP para la sección Patrimonio del dashboard.

POST /api/patrimonio/sync   → dispara run_all() en background, 202 + ack
GET  /api/patrimonio/status → último resumen de corrida (en memoria del proceso)
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify

from ...utils import get_logger
from ..auth import require_token

log = get_logger("api.patrimonio")

bp = Blueprint("patrimonio", __name__, url_prefix="/api/patrimonio")

# Estado in-memory del proceso. Single-source: el thread escribe acá, el
# endpoint GET lee. Si el bot se reinicia, se pierde — está OK porque el
# resultado canónico vive en el GSheet (`Inversiones_Snapshot`).
_state_lock = threading.Lock()
_state: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "summary": None,  # dict de runner.run_all()
    "error": None,
}


def _run_in_thread():
    """Worker que corre run_all() y guarda resultado en `_state`."""
    from ...patrimonio.runner import run_all

    with _state_lock:
        _state["running"] = True
        _state["started_at"] = datetime.now().isoformat(timespec="seconds")
        _state["finished_at"] = None
        _state["summary"] = None
        _state["error"] = None
    try:
        summary = run_all()
        with _state_lock:
            _state["summary"] = summary
    except Exception as e:
        log.exception("Patrimonio run_all falló")
        with _state_lock:
            _state["error"] = f"{type(e).__name__}: {e}"
    finally:
        with _state_lock:
            _state["running"] = False
            _state["finished_at"] = datetime.now().isoformat(timespec="seconds")


@bp.post("/sync")
@require_token
def sync_now():
    with _state_lock:
        if _state["running"]:
            return jsonify({
                "status": "already_running",
                "started_at": _state["started_at"],
            }), 409
        # Lanzar el thread mientras tenemos el lock para evitar doble dispatch.
        # `daemon=True` para que no bloquee el shutdown del proceso si Diego
        # cierra el bot mientras corre un sync.
        t = threading.Thread(target=_run_in_thread, daemon=True, name="patrimonio-sync")
        t.start()
        started_at = datetime.now().isoformat(timespec="seconds")
        _state["running"] = True  # optimista, el thread re-confirma
        _state["started_at"] = started_at
    return jsonify({"status": "started", "started_at": started_at}), 202


@bp.get("/status")
@require_token
def status():
    with _state_lock:
        return jsonify(dict(_state))
