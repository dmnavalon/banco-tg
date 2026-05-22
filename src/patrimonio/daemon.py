"""Daemon que corre 24/7 en la Mac de Diego y ejecuta scrapers cuando hay un
request encolado en Firestore desde el dashboard de producción.

Loop:
  1. heartbeat → escribe `daemon_heartbeat_at` (para que el dashboard pueda
     advertir si la Mac está dormida)
  2. lee el doc `config/patrimonio_state`
  3. si `last_request_at > last_processed_at` (o no hay processed_at todavía),
     hay request nuevo → ejecuta `runner.run_all()` y guarda resultado
  4. sleep 30s

Ejecución:
  python -m src.patrimonio.daemon

Programado por launchd: `scripts/com.diego.patrimonio.daemon.plist`
(KeepAlive=true, RunAtLoad=true) — si crashea launchd lo relanza.

Variables ambiente respetadas:
  PATRIMONIO_DAEMON_POLL_SECONDS  (default 30)
"""
from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime

from .. import db
from ..utils import get_logger
from . import keychain
from .runner import run_all

log = get_logger("patrimonio.daemon")

POLL_SECONDS = int(os.environ.get("PATRIMONIO_DAEMON_POLL_SECONDS") or "30")

_stop = False


def _handle_signal(signum, _frame):
    global _stop
    log.info("Recibida señal %s — terminando ordenadamente al fin del loop.", signum)
    _stop = True


def _should_process(state: dict) -> bool:
    """True si hay un request más reciente que el último procesado."""
    last_req = state.get("last_request_at")
    if not last_req:
        return False
    if state.get("running"):
        # Otro intento del daemon (o este crasheó antes de marcar processed).
        # No re-disparamos para evitar duplicados; el siguiente loop lo
        # detectará si quedó zombi (running=true por mucho tiempo).
        return False
    last_proc = state.get("last_processed_at")
    if not last_proc:
        return True
    return last_req > last_proc


def _zombie_request(state: dict) -> bool:
    """True si `running=True` pero hace >10 min que no se actualiza —
    probablemente el daemon crasheó mid-run y dejó el flag colgado."""
    if not state.get("running"):
        return False
    started = state.get("started_at")
    if not started:
        return True
    try:
        started_dt = datetime.strptime(started, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    age = datetime.now() - started_dt
    return age.total_seconds() > 600  # 10 min


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Smoke check: si no estamos en Mac, abortamos limpio.
    try:
        keychain._assert_macos()
    except Exception as e:
        log.error("Daemon NO arranca: %s", e)
        return 1

    log.info("Daemon de patrimonio arrancando. Poll cada %ss.", POLL_SECONDS)

    while not _stop:
        try:
            db.patrimonio_daemon_heartbeat()
            state = db.get_patrimonio_state()

            if _zombie_request(state):
                log.warning("Detecté running=True hace >10min sin update — limpiando flag.")
                db.set_patrimonio_running(False)
                state = db.get_patrimonio_state()

            if _should_process(state):
                log.info("Request nuevo: %s. Ejecutando run_all()…",
                         state.get("last_request_at"))
                started = _now_iso()
                db.set_patrimonio_running(True, started_at=started)
                try:
                    summary = run_all()
                    db.set_patrimonio_result(summary, None, _now_iso())
                    log.info(
                        "run_all() OK · total CLP %s · ok=%s err=%s",
                        summary.get("total_clp"),
                        summary.get("ok"),
                        summary.get("errors"),
                    )
                except Exception as e:
                    log.exception("run_all() falló.")
                    db.set_patrimonio_result(
                        None,
                        f"{type(e).__name__}: {e}",
                        _now_iso(),
                    )
        except Exception as e:
            log.exception("Loop daemon falló — sigo después de pausa.")
            # Sleep extra para no quemar Firestore si hay un error persistente
            time.sleep(60)
            continue

        # Sleep que respeta SIGTERM (1s a la vez)
        for _ in range(POLL_SECONDS):
            if _stop:
                break
            time.sleep(1)

    log.info("Daemon terminado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
