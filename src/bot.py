from __future__ import annotations

import os
import re
import threading
import time
import traceback
from html import escape as _hesc
from typing import Any

import requests

from . import db, feedback, gsheet, secrets_store, telegram_notify
from .utils import format_clp, get_logger, project_path

log = get_logger("bot")

TG_API = "https://api.telegram.org"

VALID_BANKS = ["falabella", "bancochile"]

_GREET_RE = re.compile(
    r"^(hola+|hi|hey|ey+|buenas?(\s+(d[ií]as?|tardes?|noches?))?|buenos\s+d[ií]as?|saludos?|qu[eé]\s+tal|ola)[\s!.,😊👋]*$",
    re.IGNORECASE,
)

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
            params={"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
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

    if _GREET_RE.match(text):
        threading.Thread(target=_handle_greeting, daemon=True).start()
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


def _handle_callback(callback: dict[str, Any]) -> None:
    callback_id = callback.get("id", "")
    chat = (callback.get("message") or {}).get("chat") or {}
    chat_id = str(chat.get("id", ""))
    message_id = (callback.get("message") or {}).get("message_id")
    data = (callback.get("data") or "").strip()

    authorized = _authorized_chat_id()
    if chat_id != authorized:
        telegram_notify.answer_callback_query(callback_id)
        return

    telegram_notify.answer_callback_query(callback_id)

    if not data or ":" not in data:
        return

    action, mov_id = data.split(":", 1)
    movs = db.get_movements_by_ids([mov_id])
    if not movs:
        if message_id:
            telegram_notify.edit_message_text(chat_id, message_id, "[Movimiento no encontrado]")
        return

    mov = movs[0]

    if action == "a":
        cat = mov.get("suggested_category") or "Otro"
        sub = mov.get("suggested_subcategory")
        db.update_decision(mov_id, status="aprobado", final_category=cat, final_subcategory=sub, decided_by=chat_id)
        try:
            gsheet.append_movement({**mov, "final_category": cat, "final_subcategory": sub})
        except Exception as e:
            log.warning(f"gsheet.append_movement falló: {e}")
        label = f"{_hesc(cat)} / {_hesc(sub)}" if sub else _hesc(cat)
        if message_id:
            from .telegram_notify import _movement_card_text
            telegram_notify.edit_message_text(
                chat_id, message_id,
                f"{_movement_card_text(mov)}\n\n✅ <b>Aprobado:</b> {label}",
            )

    elif action == "i":
        db.update_decision(mov_id, status="ignorado", final_category=None, final_subcategory=None, decided_by=chat_id)
        if message_id:
            from .telegram_notify import _movement_card_text
            telegram_notify.edit_message_text(
                chat_id, message_id,
                f"{_movement_card_text(mov)}\n\n⚫ <b>Ignorado</b>",
            )

    elif action == "c":
        db.set_wizard_state(chat_id, "correcting", {"mov_id": mov_id, "message_id": message_id})
        if message_id:
            from .telegram_notify import _movement_card_text
            telegram_notify.edit_message_text(
                chat_id,
                message_id,
                f"{_movement_card_text(mov)}\n\n✏️ <b>Escribe la corrección</b>\n"
                "Formato: <code>Categoria</code> o <code>Categoria/Subcategoria</code>",
            )


def _handle_wizard_input(text: str, chat_id: str, state: dict[str, Any]) -> None:
    state_name = state["state"]
    payload = state["payload"] or {}
    bank = payload.get("bank")

    if state_name == "correcting":
        mov_id = payload.get("mov_id", "")
        message_id = payload.get("message_id")
        db.clear_wizard_state(chat_id)
        if not mov_id:
            _send("Error interno: mov_id no encontrado. Reintenta.")
            return
        movs = db.get_movements_by_ids([mov_id])
        if not movs:
            _send("Movimiento no encontrado en la base.")
            return
        mov = movs[0]
        cat, sub = _split_cat_sub(text)
        db.update_decision(mov_id, status="corregido", final_category=cat, final_subcategory=sub, decided_by=chat_id)
        try:
            gsheet.append_movement({**mov, "final_category": cat, "final_subcategory": sub})
        except Exception as e:
            log.warning(f"gsheet.append_movement falló: {e}")
        pattern = _extract_pattern(mov.get("description", ""))
        learned_html = ""
        if pattern:
            rule_id = db.add_rule(match_type="contains", pattern=pattern, category=cat, subcategory=sub)
            if rule_id:
                learned_html = f"\n📚 Regla aprendida: <code>{_hesc(pattern)}</code> → {_hesc(cat)}"
        label = f"{_hesc(cat)} / {_hesc(sub)}" if sub else _hesc(cat)
        if message_id:
            from .telegram_notify import _movement_card_text
            telegram_notify.edit_message_text(
                chat_id, message_id,
                f"{_movement_card_text(mov)}\n\n✏️ <b>Corregido:</b> {label}{learned_html}",
            )
        else:
            learned_plain = f"\n📚 Regla aprendida: {pattern} → {cat}" if pattern and learned_html else ""
            _send(f"Corregido: {label}{learned_plain}")
        return

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


def _handle_greeting() -> None:
    try:
        rows = db.get_all_pending()
        if not rows:
            _send(
                "👋 Hola, Diego.\n\n"
                "✅ Todo al día por ahora.\n"
                "No tengo movimientos nuevos pendientes de revisión.\n\n"
                "Si quieres revisar pendientes anteriores, escribe /pending."
            )
            return

        n = len(rows)
        plural = "s" if n != 1 else ""
        _send(f"👋 Hola, Diego.\n\nTienes {n} movimiento{plural} pendiente{plural} de revisión:")
        movs = [
            {
                "id": r["id"],
                "date": r.get("date", ""),
                "description": r.get("description", ""),
                "amount": r.get("amount", 0),
                "bank": r.get("bank", ""),
                "suggested_category": r.get("suggested_category"),
                "suggested_subcategory": r.get("suggested_subcategory"),
                "confidence": r.get("confidence") or 0.0,
                "comercio": r.get("comercio"),
                "tipo": r.get("tipo") or "Egreso",
                "requiere_revision": r.get("requiere_revision", False),
                "pregunta_sugerida": r.get("pregunta_sugerida"),
            }
            for r in rows
        ]
        telegram_notify.send_movement_cards(movs)
    except Exception as e:
        db.record_error("bot.greeting", str(e), traceback.format_exc())
        _send(f"Error al consultar pendientes: {type(e).__name__}: {e}")


def _split_cat_sub(s: str) -> tuple[str, str | None]:
    parts = [p.strip() for p in s.split("/", 1)]
    cat = parts[0].capitalize() if parts[0] else "Otro"
    sub = parts[1].capitalize() if len(parts) > 1 and parts[1] else None
    return cat, sub


def _extract_pattern(description: str) -> str | None:
    from .utils import normalize
    norm = normalize(description)
    if not norm:
        return None
    for token in re.split(r"[^A-Z]+", norm):
        if len(token) >= 4:
            return token
    return None


def _run_test_in_thread(bank: str) -> None:
    try:
        from . import run_daily
        otp = run_daily.make_otp_provider()
        new = run_daily.run_for_bank_full(bank, otp, progress=_send)
        if new:
            n = len(new)
            _send(f"✅ [{bank}] {n} movimiento(s) clasificado(s). Enviando tarjetas…")
            telegram_notify.send_movement_cards(new)
        # Si no hay nuevos, run_for_bank_full ya envió el mensaje "Sin movimientos"
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
                "comercio": r.get("comercio"),
                "tipo": r.get("tipo") or "Egreso",
                "requiere_revision": r.get("requiere_revision", False),
                "pregunta_sugerida": r.get("pregunta_sugerida"),
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
                cbq = u.get("callback_query")
                if cbq:
                    try:
                        _handle_callback(cbq)
                    except Exception as e:
                        db.record_error("bot.handle_callback", str(e), traceback.format_exc())
                        log.error(f"Error procesando callback {update_id}: {e}")
        except Exception as e:
            log.error(f"Loop crash: {type(e).__name__}: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
