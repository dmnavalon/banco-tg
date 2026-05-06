from __future__ import annotations

import os
import re
import threading
import time
import traceback
from typing import Any

import requests

from . import db, feedback, secrets_store, telegram_notify
from .utils import format_clp, get_logger

log = get_logger("bot")

TG_API = "https://api.telegram.org"

VALID_BANKS = ["falabella", "bancochile"]

HELP_TEXT = (
    "Comandos:\n"
    "/start, /help — esta ayuda\n"
    "/setup — guía de configuración\n"
    "/cred <banco> — configura credenciales (falabella, bancochile)\n"
    "/forget <banco> — borra credenciales y sesión\n"
    "/cancel — cancela el wizard activo\n"
    "/test <banco> — ejecuta scrape ahora\n"
    "/run — corre el daily completo\n"
    "/status — bancos configurados y estado\n"
    "/pending — re-envía pendientes no notificados\n"
    "/last [N] — últimos N movimientos (default 10, máx 50)\n"
    "\n"
    "También respondes al batch con: «1 ok», «2 alimentacion»,\n"
    "«2 alimentacion/super», «3 ignorar», «todo ok»."
)


def _bot_token() -> str:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TG_BOT_TOKEN no está configurado.")
    return token


def _authorized_chat_id() -> str:
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("TG_CHAT_ID no está configurado.")
    return chat_id


def _read_offset() -> int:
    try:
        return int(db.get_config("tg_offset") or "0")
    except Exception:
        return 0


def _write_offset(offset: int) -> None:
    try:
        db.set_config("tg_offset", str(offset))
    except Exception:
        pass


def _get_updates(offset: int, timeout: int = 25) -> list[dict[str, Any]]:
    url = f"{TG_API}/bot{_bot_token()}/getUpdates"
    try:
        r = requests.get(
            url,
            params={"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 10,
        )
        if r.status_code != 200:
            log.warning(f"getUpdates respondió {r.status_code}: {r.text[:200]}")
            return []
        data = r.json()
        if not data.get("ok"):
            return []
        return data.get("result", []) or []
    except requests.RequestException as e:
        log.warning(f"getUpdates falló: {type(e).__name__}: {e}")
        return []


def _send(text: str) -> None:
    telegram_notify.send_message(text)


def _handle_message(msg: dict[str, Any]) -> None:
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (msg.get("text") or "").strip()
    message_id = str(msg.get("message_id", ""))

    authorized = _authorized_chat_id()
    if chat_id != authorized:
        log.warning(f"Mensaje de chat no autorizado: {chat_id}. Ignorando.")
        return

    db.record_telegram_log(
        direction="in",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
    )

    if not text:
        return

    if text.lower().startswith("otp "):
        log.info("Mensaje OTP recibido — el provider lo recogerá desde telegram_log.")
        return

    if text.startswith("/"):
        _handle_command(text, chat_id)
        return

    state = db.get_wizard_state(chat_id)
    if state:
        _handle_wizard_input(text, chat_id, state)
        return

    response = feedback.apply(text, chat_id)
    _send(response)


def _handle_command(text: str, chat_id: str) -> None:
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in {"/start", "/help"}:
        _send(HELP_TEXT)
        return

    if cmd == "/setup":
        configured = set(secrets_store.list_configured())
        missing = [b for b in VALID_BANKS if b not in configured]
        if not missing:
            _send("Todos los bancos están configurados. Usa /test <banco> para probar.")
            return
        nxt = missing[0]
        _send(f"Siguiente banco a configurar: {nxt}\nMándame: /cred {nxt}")
        return

    if cmd == "/cred":
        if not args:
            _send("Uso: /cred <falabella|bancochile>")
            return
        bank = args[0].lower()
        if bank not in VALID_BANKS:
            _send(f"Banco no soportado. Opciones: {', '.join(VALID_BANKS)}")
            return
        db.set_wizard_state(chat_id, f"awaiting_rut_{bank}", {"bank": bank})
        _send(
            f"Configurando {bank}.\n"
            "1/2 → Mándame tu RUT en formato 12345678-9 (con guión, sin puntos).\n"
            "Cancela con /cancel."
        )
        return

    if cmd == "/forget":
        if not args:
            _send("Uso: /forget <falabella|bancochile>")
            return
        bank = args[0].lower()
        if bank not in VALID_BANKS:
            _send(f"Banco no soportado. Opciones: {', '.join(VALID_BANKS)}")
            return
        deleted = secrets_store.delete(bank)
        state_file = project_path("data", f"state_{bank}.json")
        if state_file.exists():
            state_file.unlink()
        _send(f"{bank}: credenciales {'borradas' if deleted else 'no había'}; sesión limpiada.")
        return

    if cmd == "/cancel":
        db.clear_wizard_state(chat_id)
        _send("Wizard cancelado.")
        return

    if cmd == "/test":
        if not args:
            _send("Uso: /test <falabella|bancochile>")
            return
        bank = args[0].lower()
        if bank not in VALID_BANKS:
            _send(f"Banco no soportado. Opciones: {', '.join(VALID_BANKS)}")
            return
        threading.Thread(target=_run_test_in_thread, args=(bank,), daemon=True).start()
        _send(f"[{bank}] Iniciando scrape en background…")
        return

    if cmd == "/run":
        threading.Thread(target=_run_daily_in_thread, daemon=True).start()
        _send("Corriendo daily completo en background…")
        return

    if cmd == "/status":
        configured = secrets_store.list_configured()
        pending = db.count_pending()
        total = db.count_total()
        rules_n = db.count_rules()
        last_err = db.get_last_error()
        err_str = "ninguno"
        if last_err:
            err_str = f"{last_err['component']}: {last_err['message'][:120]}"
        _send(
            "Estado:\n"
            f"  Bancos configurados: {', '.join(configured) if configured else '(ninguno)'}\n"
            f"  Movimientos totales: {total}\n"
            f"  Pendientes de revisar: {pending}\n"
            f"  Reglas aprendidas: {rules_n}\n"
            f"  Último error: {err_str}"
        )
        return

    if cmd == "/pending":
        threading.Thread(target=_resend_pending, daemon=True).start()
        _send("Reenviando pendientes…")
        return

    if cmd == "/last":
        n = 10
        if args:
            try:
                n = int(args[0])
            except ValueError:
                _send("Uso: /last [N]")
                return
        movs = db.get_last_movements(n)
        if not movs:
            _send("Sin movimientos.")
            return
        lines = [f"Últimos {len(movs)}:"]
        for m in movs:
            cat = m["final_category"] or m["suggested_category"] or "?"
            lines.append(f"  {m['date']} [{m['bank']}] {format_clp(m['amount'])} · {m['description'][:50]} · {cat} ({m['status']})")
        _send("\n".join(lines))
        return

    _send(f"Comando no reconocido: {cmd}. /help")


def _handle_wizard_input(text: str, chat_id: str, state: dict[str, Any]) -> None:
    state_name = state["state"]
    payload = state["payload"] or {}
    bank = payload.get("bank")

    if state_name.startswith("awaiting_rut_"):
        if not re.fullmatch(r"\d{7,8}-[\dkK]", text):
            _send("RUT inválido. Formato: 12345678-9 (con guión, sin puntos). Reintenta o /cancel.")
            return
        payload["rut"] = text.upper()
        db.set_wizard_state(chat_id, f"awaiting_pass_{bank}", payload)
        if bank == "falabella":
            _send("2/2 → Mándame tu Clave Internet (exactamente 6 dígitos).")
        else:
            _send("2/2 → Mándame tu Clave (máx 8 caracteres).")
        return

    if state_name.startswith("awaiting_pass_"):
        if bank == "falabella" and not re.fullmatch(r"\d{6}", text):
            _send("Clave Falabella inválida: deben ser 6 dígitos. Reintenta o /cancel.")
            return
        if bank == "bancochile" and len(text) > 8:
            _send("Clave BCh excede 8 caracteres. Reintenta o /cancel.")
            return
        rut = payload.get("rut", "")
        try:
            secrets_store.store(bank, rut, text)
            db.clear_wizard_state(chat_id)
            _send(f"✓ Credenciales de {bank} guardadas (cifradas).\nProbá con /test {bank}.")
        except Exception as e:
            db.record_error("bot.wizard.store", str(e), traceback.format_exc())
            _send(f"Error guardando: {type(e).__name__}: {e}")
        return

    db.clear_wizard_state(chat_id)
    _send("Estado de wizard inválido, reseteado. Reintenta /cred <banco>.")


def _run_test_in_thread(bank: str) -> None:
    try:
        from . import run_daily
        otp = run_daily.make_otp_provider()
        new = run_daily.run_for_bank_full(bank, otp)
        if new:
            _send(f"[{bank}] {len(new)} movimientos nuevos guardados.")
            telegram_notify.send_daily_batch(new)
        else:
            _send(f"[{bank}] Sin movimientos nuevos.")
    except Exception as e:
        db.record_error(f"bot.test.{bank}", str(e), traceback.format_exc())
        _send(f"[{bank}] Error: {type(e).__name__}: {e}")


def _run_daily_in_thread() -> None:
    try:
        from . import run_daily
        run_daily.main()
    except Exception as e:
        db.record_error("bot.run_daily", str(e), traceback.format_exc())
        _send(f"Daily falló: {type(e).__name__}: {e}")


def _resend_pending() -> None:
    try:
        rows = db.get_pending()
        if not rows:
            _send("Sin pendientes.")
            return
        movs = []
        for r in rows:
            movs.append({
                "id": r["id"],
                "date": r["date"],
                "description": r["description"],
                "amount": r["amount"],
                "bank": r["bank"],
                "suggested_category": r["suggested_category"],
                "suggested_subcategory": r["suggested_subcategory"],
                "confidence": r["confidence"] or 0.0,
            })
        telegram_notify.send_daily_batch(movs)
    except Exception as e:
        db.record_error("bot.pending", str(e), traceback.format_exc())
        _send(f"Error reenviando pendientes: {type(e).__name__}: {e}")


def main() -> None:
    db.init_if_needed()
    log.info("Bot iniciando long-poll…")
    offset = _read_offset()
    while True:
        try:
            updates = _get_updates(offset)
            for u in updates:
                update_id = u.get("update_id", 0)
                offset = update_id + 1
                _write_offset(offset)
                msg = u.get("message")
                if msg:
                    try:
                        _handle_message(msg)
                    except Exception as e:
                        db.record_error("bot.handle_message", str(e), traceback.format_exc())
                        log.error(f"Error procesando update {update_id}: {e}")
        except Exception as e:
            log.error(f"Loop crash: {type(e).__name__}: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
