from __future__ import annotations

import os
import time
from html import escape as _esc
from typing import Sequence

import requests

from . import db
from .utils import format_clp, get_logger

log = get_logger("telegram_notify")

TG_API = "https://api.telegram.org"

# ── Emojis por categoría ──────────────────────────────────────────────────────

CATEGORY_EMOJIS: dict[str, str] = {
    # Ingresos
    "Sueldo":                       "💼",
    "Honorarios":                   "🧾",
    "Dividendos y utilidades":      "📈",
    "Inversiones":                  "💹",
    "Arriendos":                    "🏠",
    "Reembolsos":                   "↩️",
    "Otros ingresos":               "✨",
    # Egresos
    "Hogar y alimentación":         "🍽️",
    "Vivienda":                     "🏡",
    "Servicios básicos":            "💡",
    "Educación":                    "🎓",
    "Niños":                        "🧒",
    "Salud y seguros":              "⚕️",
    "Transporte":                   "🚗",
    "Deporte y bienestar":          "🎾",
    "Vestuario y cuidado personal": "👕",
    "Entretención y vida social":   "🎉",
    "Tecnología":                   "💻",
    "Servicios domésticos":         "🧹",
    "Mascotas":                     "🐾",
    "Finanzas e impuestos":         "🏦",
    "Ahorro e inversión":           "💰",
    "Transferencias internas":      "🔁",
    "Otros":                        "📦",
}

SUBCATEGORY_EMOJIS: dict[str, str] = {
    # Hogar y alimentación
    "Supermercado":                 "🛒",
    "Alimentos":                    "🍲",
    "Panadería":                    "🥖",
    "Carnicería":                   "🥩",
    "Verduras y frutas":            "🥬",
    "Delivery":                     "🛵",
    "Restaurantes":                 "🍽️",
    "Cafeterías":                   "☕",
    # Vivienda
    "Arriendo o dividendo":         "🏠",
    "Contribuciones":               "🏛️",
    "Gastos comunes":               "🏢",
    "Mantención casa":              "🔧",
    "Jardín y piscina":             "🌿",
    "Muebles y decoración":         "🛋️",
    # Servicios básicos
    "Luz":                          "💡",
    "Agua":                         "💧",
    "Gas":                          "🔥",
    "Internet":                     "🌐",
    "Telefonía móvil":              "📱",
    "Streaming":                    "📺",
    "Alarmas y seguridad":          "🚨",
    # Educación
    "Colegio":                      "🏫",
    "Jardín infantil":              "🧸",
    "Matrícula":                    "📝",
    "Útiles escolares":             "✏️",
    "Uniformes":                    "👔",
    "Transporte escolar":           "🚌",
    "Actividades escolares":        "🎒",
    # Niños
    "Actividades extracurriculares":"🎨",
    "Juguetes":                     "🧸",
    "Cumpleaños":                   "🎂",
    "Ropa niños":                   "👕",
    "Salud niños":                  "🩺",
    "Deportes niños":               "⚽",
    # Salud y seguros
    "Farmacia":                     "💊",
    "Consultas médicas":            "🩺",
    "Dentista":                     "🦷",
    "Exámenes médicos":             "🔬",
    "Seguro de salud":              "🛡️",
    "Seguro de vida":               "❤️",
    "Terapias":                     "🧘",
    # Transporte
    "Combustible":                  "⛽",
    "Tag y peajes":                 "🛣️",
    "Estacionamientos":             "🅿️",
    "Mantención auto":              "🔧",
    "Seguro auto":                  "🚘",
    "Permiso de circulación":       "📄",
    "Uber o taxi":                  "🚕",
    "Transporte público":           "🚌",
    # Deporte y bienestar
    "Gimnasio":                     "🏋️",
    "Pádel":                        "🎾",
    "Club deportivo":               "🏟️",
    "Ropa deportiva":               "👟",
    "Implementos deportivos":       "🎒",
    "Masajes":                      "💆",
    # Vestuario y cuidado personal
    "Ropa adultos":                 "👔",
    "Zapatos":                      "👞",
    "Peluquería":                   "💇",
    "Estética":                     "💅",
    "Perfumería y cuidado personal":"🧴",
    # Entretención y vida social
    "Salidas familiares":           "👨‍👩‍👧‍👦",
    "Cine y espectáculos":          "🎬",
    "Regalos":                      "🎁",
    "Cumpleaños y eventos":         "🎉",
    "Vacaciones":                   "✈️",
    # Tecnología
    "Software y suscripciones":     "💻",
    "Hardware":                     "🖥️",
    "Celulares":                    "📱",
    "Apps":                         "📲",
    "Soporte técnico":              "🛠️",
    # Servicios domésticos
    "Nana":                         "🧑‍🍼",
    "Imposiciones nana":            "📑",
    "Aseo":                         "🧹",
    "Reparaciones menores":         "🔨",
    # Mascotas
    "Alimento mascotas":            "🐶",
    "Veterinario":                  "🐾",
    "Accesorios mascotas":          "🦴",
    # Finanzas e impuestos
    "Pago tarjeta de crédito":      "💳",
    "Intereses y comisiones":       "🏦",
    "Impuestos":                    "🏛️",
    "Contador":                     "🧮",
    "Seguros financieros":          "🛡️",
    # Ahorro e inversión
    "Ahorro mensual":               "🐷",
    "Inversión financiera":         "📈",
    "Fondo emergencia":             "🚨",
    "APV":                          "👴",
    # Transferencias internas
    "Movimiento entre cuentas":     "🔁",
    "Pago tarjeta mismo titular":   "💳",
    "Traspaso a inversión":         "💹",
    # Ingresos (subcategorías)
    "Sueldo principal":             "💼",
    "Sueldo secundario":            "💼",
    "Boletas de honorarios":        "🧾",
    "Dividendos empresas":          "📈",
    "Retiros de empresa":           "📤",
    "Intereses":                    "💹",
    "Dividendos financieros":       "💹",
    "Venta de activos":             "🏷️",
    "Ingreso por arriendo":         "🏠",
    "Reembolso empresa":            "↩️",
    "Devolución comercio":          "↩️",
    "Seguro reembolsado":           "↩️",
    "Regalos recibidos":            "🎁",
    "Ingresos extraordinarios":     "✨",
    # Otros
    "Varios":                       "📦",
    "Gastos no clasificados":       "❓",
    "Ajustes manuales":             "⚙️",
}

_TIPO_HEADER: dict[str, str] = {
    "Egreso":               "🔴 <b>Nuevo egreso detectado</b>",
    "Ingreso":              "🟢 <b>Nuevo ingreso detectado</b>",
    "Transferencia interna":"🔁 <b>Transferencia interna detectada</b>",
}


def _conf_band(conf: float) -> str:
    pct = int(conf * 100)
    if pct >= 90:
        return f"🟢 {pct}%"
    if pct >= 75:
        return f"🟡 {pct}%"
    if pct >= 50:
        return f"🟠 {pct}%"
    return f"🔴 {pct}% — revisar"


# ── API helpers ───────────────────────────────────────────────────────────────

def _bot_token() -> str:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TG_BOT_TOKEN no está configurado en .env")
    return token


def _chat_id() -> str:
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("TG_CHAT_ID no está configurado en .env")
    return chat_id


def send_message(text: str, *, chat_id: str | None = None, log_db: bool = True, parse_mode: str | None = None) -> dict | None:
    target = chat_id or _chat_id()
    url = f"{TG_API}/bot{_bot_token()}/sendMessage"
    payload: dict = {"chat_id": target, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    delays = [0, 2, 5]
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200 and r.json().get("ok"):
                data = r.json()["result"]
                if log_db:
                    try:
                        db.record_telegram_log(
                            direction="out",
                            chat_id=target,
                            message_id=str(data.get("message_id", "")),
                            text=text,
                        )
                    except Exception:
                        pass
                return data
            log.warning(f"sendMessage {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log.warning(f"sendMessage falló: {e}")

    log.error("sendMessage falló tras 3 intentos.")
    return None


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    try:
        requests.post(
            f"{TG_API}/bot{_bot_token()}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=5,
        )
    except Exception:
        pass


def edit_message_text(chat_id: str, message_id: int, text: str, parse_mode: str = "HTML") -> None:
    try:
        payload: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        requests.post(
            f"{TG_API}/bot{_bot_token()}/editMessageText",
            json=payload,
            timeout=10,
        )
    except Exception:
        pass


def edit_message_reply_markup(chat_id: str, message_id: int, reply_markup: dict) -> None:
    """Edita solo el inline keyboard de un mensaje (sin tocar texto/caption)."""
    try:
        requests.post(
            f"{TG_API}/bot{_bot_token()}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
            timeout=10,
        )
    except Exception:
        pass


def edit_message_caption(chat_id: str, message_id: int, caption: str, parse_mode: str = "HTML", reply_markup: dict | None = None) -> None:
    """Edita el caption de un mensaje sendPhoto. Necesario para tarjetas con foto:
    editMessageText falla en mensajes con foto, hay que usar editMessageCaption."""
    try:
        payload: dict = {"chat_id": chat_id, "message_id": message_id, "caption": caption}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        requests.post(
            f"{TG_API}/bot{_bot_token()}/editMessageCaption",
            json=payload,
            timeout=10,
        )
    except Exception:
        pass


def send_correction_prompt(chat_id: str, mov: dict) -> int | None:
    """Manda un mensaje con force_reply para pedir la corrección de UN movimiento.
    Telegram abre el campo de texto citando este mensaje, así no hay duda con cuál
    movimiento se está hablando aunque haya N tarjetas en el chat.

    Devuelve el message_id de Telegram (para mapear la respuesta al movimiento)
    o None si falló el envío.
    """
    desc = (mov.get("description") or "")[:60]
    amount_str = format_clp(abs(mov.get("amount") or 0))
    fecha = mov.get("date") or ""
    persona = mov.get("persona") or ""
    persona_str = f" · {_esc(persona)}" if persona else ""

    text = (
        f"✏️ <b>Corrige este movimiento</b>\n"
        f"📅 {_esc(fecha)} · 💸 {_esc(amount_str)}{persona_str}\n"
        f"<code>{_esc(desc)}</code>\n\n"
        f"Responde a <i>este</i> mensaje con texto libre.\n"
        f"Ejemplos: «es del super», «esto va a Bodemall», «pádel del trabajo».\n"
        f"Escribe «cancelar» para abortar."
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Texto libre o «cancelar»",
        },
    }
    try:
        r = requests.post(f"{TG_API}/bot{_bot_token()}/sendMessage", json=payload, timeout=10)
        if r.status_code == 200 and r.json().get("ok"):
            return r.json()["result"]["message_id"]
        log.warning(f"send_correction_prompt {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"send_correction_prompt falló: {e}")
    return None


def send_ignore_prompt(chat_id: str, mov: dict) -> int | None:
    """Manda un prompt con force_reply para pedir la razón al ignorar un movimiento.
    Mismo mecanismo que `send_correction_prompt` pero el texto pide razón en lugar
    de corrección, y el caller debe registrar el pending_user_action con
    `action="ignore"`.

    Devuelve el message_id del prompt (para mapear la respuesta al movimiento)
    o None si falló el envío.
    """
    desc = (mov.get("description") or "")[:60]
    amount_str = format_clp(abs(mov.get("amount") or 0))
    fecha = mov.get("date") or ""
    persona = mov.get("persona") or ""
    persona_str = f" · {_esc(persona)}" if persona else ""

    text = (
        f"🚫 <b>Ignorar este movimiento</b>\n"
        f"📅 {_esc(fecha)} · 💸 {_esc(amount_str)}{persona_str}\n"
        f"<code>{_esc(desc)}</code>\n\n"
        f"Responde a <i>este</i> mensaje con la razón.\n"
        f"Ejemplos: «duplicado», «movimiento de prueba», «pago entre cuentas propias».\n"
        f"Escribe «skip» para ignorar sin razón, o «cancelar» para abortar."
    )
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "force_reply": True,
            "selective": True,
            "input_field_placeholder": "Razón, «skip» o «cancelar»",
        },
    }
    try:
        r = requests.post(f"{TG_API}/bot{_bot_token()}/sendMessage", json=payload, timeout=10)
        if r.status_code == 200 and r.json().get("ok"):
            return r.json()["result"]["message_id"]
        log.warning(f"send_ignore_prompt {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"send_ignore_prompt falló: {e}")
    return None


def delete_message(chat_id: str, message_id: int) -> None:
    """Elimina un mensaje del chat. Útil para limpiar el prompt de corrección
    una vez que la respuesta ya fue procesada."""
    try:
        requests.post(
            f"{TG_API}/bot{_bot_token()}/deleteMessage",
            json={"chat_id": chat_id, "message_id": message_id},
            timeout=5,
        )
    except Exception:
        pass


# ── Card builder ──────────────────────────────────────────────────────────────

def _movement_card_text(mov: dict) -> str:
    bank = _esc((mov.get("bank") or "").capitalize())
    desc_raw = _esc(mov.get("description") or "")
    fecha = _esc(mov.get("date") or "")
    persona = _esc(mov.get("persona") or "")
    amount = mov.get("amount") or 0
    amount_str = _esc(format_clp(abs(amount)))
    comercio = _esc(mov.get("comercio") or "-")
    cat = mov.get("suggested_category") or "Sin categoría"
    sub = mov.get("suggested_subcategory")
    conf = mov.get("confidence") or 0.0
    tipo = mov.get("tipo") or ("Ingreso" if amount > 0 else "Egreso")
    pregunta = mov.get("pregunta_sugerida")

    cat_emoji = CATEGORY_EMOJIS.get(cat, "🏷️")
    sub_emoji = SUBCATEGORY_EMOJIS.get(sub, "") if sub else ""
    header = _TIPO_HEADER.get(tipo, f"🔴 <b>Nuevo movimiento</b>")

    sub_str = f" → {sub_emoji} {_esc(sub)}" if sub else ""
    persona_line = f"\n👤 <b>Persona:</b> {persona}" if persona else ""

    # Línea de cuotas: solo si la compra tiene >1 cuota. La "cuota a pagar"
    # es la mensualidad real que sale ese mes — distinta del Monto total.
    # Si cuota_monto vino del modal de Falabella, se usa tal cual. Si no
    # (mov inferido desde sufijo "(X/N)"), se deriva como amount/cuotas_total
    # — exacta para cuotas sin interés, aproximada cuando hay interés.
    cuotas_actual = mov.get("cuotas_actual")
    cuotas_total = mov.get("cuotas_total")
    cuota_monto = mov.get("cuota_monto")
    cuotas_line = ""
    if cuotas_total and cuotas_total > 1:
        if cuota_monto:
            cuota_val = abs(cuota_monto)
            cuota_str = _esc(format_clp(cuota_val))
            cuotas_line = f"\n💳 <b>Cuota:</b> {cuotas_actual} de {cuotas_total} · {cuota_str}/mes"
        else:
            cuota_val = abs(amount) / cuotas_total
            cuota_str = _esc(format_clp(cuota_val))
            cuotas_line = f"\n💳 <b>Cuota:</b> {cuotas_actual} de {cuotas_total} · ~{cuota_str}/mes"

    lines = [
        header,
        "",
        f"🏦 <b>Banco:</b> {bank}",
        f"📅 <b>Fecha:</b> {fecha}",
        f"📝 <b>Descripción:</b> <code>{desc_raw}</code>",
        f"🏪 <b>Comercio:</b> {comercio}",
        f"💸 <b>Monto:</b> {amount_str}{persona_line}{cuotas_line}",
        "",
        f"🏷️ <b>Categoría propuesta</b>",
        f"{cat_emoji} {_esc(cat)}{sub_str}",
        "",
        f"📊 <b>Confianza:</b> {_conf_band(conf)}",
    ]
    if pregunta:
        lines += ["", f"⚠️ <b>Consulta:</b> {_esc(pregunta)}"]

    # Si el mov fue ignorado con razón, mostrarla (útil cuando se reenvía la
    # ignorada en /pending y el usuario quiere recordar por qué la ignoró).
    if mov.get("status") == "ignorado":
        reason = mov.get("ignore_reason")
        if reason:
            lines += ["", f"🚫 <b>Razón ignorada:</b> {_esc(reason)}"]

    return "\n".join(lines)


def _movement_keyboard(mov_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Aprobar",  "callback_data": f"a:{mov_id}"},
            {"text": "✏️ Corregir", "callback_data": f"c:{mov_id}"},
            {"text": "🚫 Ignorar",  "callback_data": f"i:{mov_id}"},
        ]]
    }


# Cuántas tarjetas se muestran de una vez antes de exigir /next.
BATCH_PAGE_SIZE = 5


def _send_one_card(mov: dict, target: str) -> bool:
    """Envía UNA tarjeta. Si el movimiento trae screenshot_bytes, usa sendPhoto y
    persiste el file_id devuelto por Telegram para reusarlo en envíos futuros.
    Si trae tg_photo_file_id pre-existente, lo reutiliza sin re-subir bytes.
    Si no hay foto disponible, fallback a sendMessage de texto."""
    mov_id = mov["id"]
    text = _movement_card_text(mov)
    keyboard = _movement_keyboard(mov_id)
    screenshot = mov.get("screenshot_bytes")
    file_id = mov.get("tg_photo_file_id")

    # Antes había fallback a screenshot_storage.download(mov_id) acá. Removido
    # 2026-05-15: el proyecto Firebase no tiene Storage habilitado y no
    # queremos pasar a Blaze. La persistencia depende exclusivamente de
    # `tg_photo_file_id` (Firestore). Si no hay ninguno de los dos, el envío
    # cae al fallback de texto plano más abajo.

    base_url = f"{TG_API}/bot{_bot_token()}"

    for delay in [0, 2, 5]:
        if delay:
            time.sleep(delay)
        try:
            if screenshot or file_id:
                # sendPhoto — el caption acepta hasta 1024 chars con HTML.
                if screenshot:
                    files = {"photo": ("modal.png", screenshot, "image/png")}
                    data = {
                        "chat_id": target,
                        "caption": text[:1024],
                        "parse_mode": "HTML",
                        "reply_markup": __import__("json").dumps(keyboard),
                    }
                    r = requests.post(f"{base_url}/sendPhoto", data=data, files=files, timeout=30)
                else:
                    r = requests.post(f"{base_url}/sendPhoto", json={
                        "chat_id": target,
                        "photo": file_id,
                        "caption": text[:1024],
                        "parse_mode": "HTML",
                        "reply_markup": keyboard,
                    }, timeout=20)
            else:
                r = requests.post(f"{base_url}/sendMessage", json={
                    "chat_id": target,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": keyboard,
                    "disable_web_page_preview": True,
                }, timeout=20)

            if r.status_code == 200 and r.json().get("ok"):
                # Si subimos bytes nuevos, capturar el file_id de la foto más grande
                # para reusarlo en /pending o reenvíos posteriores.
                if screenshot and not file_id:
                    photos = (r.json().get("result") or {}).get("photo") or []
                    if photos:
                        # Tomar el de mayor resolución (último)
                        new_file_id = photos[-1].get("file_id")
                        if new_file_id:
                            try:
                                db.set_movement_photo_file_id(mov_id, new_file_id)
                            except Exception as e:
                                log.warning(f"No pude guardar tg_photo_file_id de {mov_id}: {e}")
                return True
            log.warning(f"send card {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log.warning(f"send card falló: {e}")
    return False


def send_movement_cards(movements: Sequence[dict]) -> bool:
    """Envía tarjetas en lotes de BATCH_PAGE_SIZE. Si hay más, guarda los IDs
    restantes en config/last_batch_remaining y avisa con un mensaje pidiendo /next."""
    if not movements:
        send_message("Sin movimientos nuevos para revisar hoy.")
        return True

    target = _chat_id()
    total = len(movements)
    first_page = list(movements[:BATCH_PAGE_SIZE])
    overflow = list(movements[BATCH_PAGE_SIZE:])

    # Persistir el payload ANTES de enviar las tarjetas. Si Diego responde
    # «1 ok» antes de que terminemos de enviar todas, feedback.apply ya tiene
    # el batch correcto. Antes el payload se persistía después del loop, lo
    # que dejaba a feedback.apply leyendo el batch ANTERIOR.
    expected_ids = [m["id"] for m in first_page]
    db.set_batch_ids(expected_ids)
    db.set_config("last_batch_payload", ",".join(expected_ids))

    ids_sent: list[str] = []
    for mov in first_page:
        if _send_one_card(mov, target):
            ids_sent.append(mov["id"])
        else:
            log.error(f"No se pudo enviar tarjeta para {mov.get('id')}")

    if ids_sent:
        # Si alguna tarjeta falló al enviarse, ajustamos el batch al subset real.
        if ids_sent != expected_ids:
            db.set_batch_ids(ids_sent)
            db.set_config("last_batch_payload", ",".join(ids_sent))
        db.mark_notified(ids_sent)
        db.record_telegram_log(
            direction="out",
            chat_id=target,
            message_id="batch",
            text=f"{len(ids_sent)}/{total} tarjetas enviadas",
            payload=",".join(ids_sent),
        )
    else:
        # Ninguna tarjeta se envió: limpiar el payload pre-persistido.
        db.set_config("last_batch_payload", "")

    # cuando hay overflow real — NO limpiamos en el else, porque esta función
    # también se reusaba para reenviar un solo movimiento tras correcciones y
    # cancelaciones, lo que borraba la cola legítima del cron. Ahora los resends
    # de una sola card usan `resend_movement_card` (no toca `last_batch_*`).
    if overflow:
        overflow_ids = [m["id"] for m in overflow]
        db.set_config("last_batch_remaining", ",".join(overflow_ids))
        send_message(
            f"📦 Te mostré {len(ids_sent)} de {total}. "
            f"Para los próximos {min(BATCH_PAGE_SIZE, len(overflow))}, mándame /next.\n"
            f"Quedan {len(overflow)} pendientes en cola."
        )

    return len(ids_sent) == len(first_page)


def resend_movement_card(mov: dict) -> bool:
    """Reenvía UNA card sin tocar el estado de batch (`last_batch_*`).

    Usar este path cuando el bot reenvía una sola tarjeta tras correcciones,
    cancelaciones, o un /test puntual. `send_movement_cards([single])` lo
    hace funcionalmente pero pisa `last_batch_payload` y borraba la cola del cron.
    """
    return _send_one_card(mov, _chat_id())


def send_next_batch_page() -> str:
    """Envía la siguiente página del batch acumulado en config/last_batch_remaining.
    Devuelve el mensaje de status para responder al comando /next."""
    raw = db.get_config("last_batch_remaining") or ""
    remaining_ids = [x.strip() for x in raw.split(",") if x.strip()]
    if not remaining_ids:
        return "No hay más pendientes en cola. Manda /pending si quieres reenviar todos."

    take = remaining_ids[:BATCH_PAGE_SIZE]
    rest = remaining_ids[BATCH_PAGE_SIZE:]

    movs = db.get_movements_by_ids(take)
    if not movs:
        # Los IDs en cola ya no existen — limpieza.
        db.set_config("last_batch_remaining", "")
        return "Los pendientes en cola ya no existen en la base. Cola limpiada."

    # Persistir el payload ANTES de enviar (mismo patrón que send_movement_cards
    # para evitar race condition con feedback.apply).
    expected_ids = [m["id"] for m in movs]
    db.set_batch_ids(expected_ids)
    db.set_config("last_batch_payload", ",".join(expected_ids))

    target = _chat_id()
    ids_sent: list[str] = []
    for mov in movs:
        if _send_one_card(mov, target):
            ids_sent.append(mov["id"])

    if ids_sent:
        if ids_sent != expected_ids:
            db.set_batch_ids(ids_sent)
            db.set_config("last_batch_payload", ",".join(ids_sent))
        db.mark_notified(ids_sent)
    else:
        db.set_config("last_batch_payload", "")

    db.set_config("last_batch_remaining", ",".join(rest))

    if rest:
        return f"📦 Te mostré {len(ids_sent)} más. Quedan {len(rest)}. /next para los próximos."
    return f"✅ Te mostré los últimos {len(ids_sent)}. No quedan más en cola."


# Alias de compatibilidad
send_daily_batch = send_movement_cards
