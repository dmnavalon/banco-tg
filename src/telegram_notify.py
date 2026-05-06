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


# ── Card builder ──────────────────────────────────────────────────────────────

def _movement_card_text(mov: dict) -> str:
    bank = _esc((mov.get("bank") or "").capitalize())
    desc = _esc(mov.get("description") or "")
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

    lines = [
        header,
        "",
        f"🏦 <b>Banco:</b> {bank}",
        f"🏪 <b>Comercio:</b> {comercio}",
        f"💸 <b>Monto:</b> {amount_str}",
        "",
        f"🏷️ <b>Categoría propuesta</b>",
        f"{cat_emoji} {_esc(cat)}{sub_str}",
        "",
        f"📊 <b>Confianza:</b> {_conf_band(conf)}",
    ]
    if pregunta:
        lines += ["", f"⚠️ <b>Consulta:</b> {_esc(pregunta)}"]

    return "\n".join(lines)


def _movement_keyboard(mov_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Aprobar",  "callback_data": f"a:{mov_id}"},
            {"text": "✏️ Corregir", "callback_data": f"c:{mov_id}"},
            {"text": "🚫 Ignorar",  "callback_data": f"i:{mov_id}"},
        ]]
    }


def send_movement_cards(movements: Sequence[dict]) -> bool:
    if not movements:
        send_message("Sin movimientos nuevos para revisar hoy.")
        return True

    target = _chat_id()
    url = f"{TG_API}/bot{_bot_token()}/sendMessage"
    ids_sent: list[str] = []

    for mov in movements:
        mov_id = mov["id"]
        text = _movement_card_text(mov)
        keyboard = _movement_keyboard(mov_id)

        sent = False
        for delay in [0, 2, 5]:
            if delay:
                time.sleep(delay)
            try:
                r = requests.post(url, json={
                    "chat_id": target,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": keyboard,
                    "disable_web_page_preview": True,
                }, timeout=20)
                if r.status_code == 200 and r.json().get("ok"):
                    ids_sent.append(mov_id)
                    sent = True
                    break
                log.warning(f"sendMessage card {r.status_code}: {r.text[:200]}")
            except requests.RequestException as e:
                log.warning(f"send card falló: {e}")

        if not sent:
            log.error(f"No se pudo enviar tarjeta para {mov_id}")

    if ids_sent:
        db.set_batch_ids(ids_sent)
        db.mark_notified(ids_sent)
        db.record_telegram_log(
            direction="out",
            chat_id=target,
            message_id="batch",
            text=f"{len(ids_sent)} tarjetas enviadas",
            payload=",".join(ids_sent),
        )

    return len(ids_sent) == len(movements)


# Alias de compatibilidad
send_daily_batch = send_movement_cards
