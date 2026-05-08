from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

# gRPC se ensucia con FDs heredados cuando Playwright forkea procesos hijos
# (Chromium). Síntoma: `DeadlineExceeded: 504` aleatorio en operaciones Firestore
# durante/después del scrape. Las env vars hay que setearlas ANTES del import
# de firebase_admin/google.cloud porque grpc.aio inicializa estructuras al
# load-time según ellas.
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "1")
os.environ.setdefault("GRPC_POLL_STRATEGY", "poll")

import firebase_admin
from firebase_admin import credentials, firestore as fstore
from google.api_core import exceptions as gax_exceptions

_client: Any = None


def _db():
    global _client
    if _client is None:
        key_json = os.environ.get("FIREBASE_KEY_JSON", "").strip()
        if key_json:
            cred = credentials.Certificate(json.loads(key_json))
        else:
            from .utils import project_path
            path = project_path("data", "firebase_service_account.json")
            cred = credentials.Certificate(str(path))
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        _client = fstore.client()
    return _client


def init_if_needed() -> None:
    _db()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_TRANSIENT_GRPC = (
    gax_exceptions.DeadlineExceeded,
    gax_exceptions.ServiceUnavailable,
    gax_exceptions.Aborted,
    gax_exceptions.InternalServerError,
    gax_exceptions.RetryError,
)


def _with_retry(fn, *, max_attempts: int = 3, base_delay: float = 1.5):
    """Reintenta una operación contra Firestore ante errores transient gRPC.
    No es un decorador para mantener el callsite simple — se llama como
    `_with_retry(lambda: ref.set(data))`."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except _TRANSIENT_GRPC as e:
            last_exc = e
            if attempt == max_attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            import logging
            logging.getLogger("db").warning(
                f"Firestore transient ({type(e).__name__}); retry {attempt}/{max_attempts} en {delay:.1f}s"
            )
            time.sleep(delay)
    if last_exc:
        raise last_exc


# ── movements ──────────────────────────────────────────────────────────────

def insert_movement(
    *,
    mov_id: str,
    date_iso: str,
    description: str,
    amount: float,
    movement_type: str | None,
    account: str | None,
    bank: str,
    raw_blob: str | None,
    suggested_category: str | None = None,
    suggested_subcategory: str | None = None,
    confidence: float | None = None,
    classifier_source: str | None = None,
    tipo: str | None = None,
    requiere_revision: bool = False,
    pregunta_sugerida: str | None = None,
    persona: str | None = None,
) -> bool:
    ref = _db().collection("movements").document(mov_id)
    if _with_retry(lambda: ref.get().exists):
        return False
    payload = {
        "id": mov_id,
        "date": date_iso,
        "description": description,
        "amount": amount,
        "movement_type": movement_type,
        "account": account,
        "bank": bank,
        "raw_blob": raw_blob,
        "suggested_category": suggested_category,
        "suggested_subcategory": suggested_subcategory,
        "confidence": confidence,
        "classifier_source": classifier_source,
        "comercio": None,
        "tipo": tipo or ("Ingreso" if (amount or 0) > 0 else "Egreso"),
        "requiere_revision": requiere_revision,
        "pregunta_sugerida": pregunta_sugerida,
        "persona": persona,
        "status": "pendiente",
        "final_category": None,
        "final_subcategory": None,
        "decided_by": None,
        "decided_at": None,
        "notified_at": None,
        "tg_photo_file_id": None,
        "inserted_at": _now(),
    }
    _with_retry(lambda: ref.set(payload))
    return True


def set_movement_photo_file_id(mov_id: str, file_id: str) -> None:
    """Guarda el file_id que retorna Telegram al subir el screenshot del movimiento.
    Permite reusarlo (sendPhoto con file_id) en lugar de re-subir bytes cada vez."""
    _db().collection("movements").document(mov_id).update({"tg_photo_file_id": file_id})


def update_classification(
    mov_id: str,
    *,
    suggested_category: str | None,
    suggested_subcategory: str | None,
    confidence: float | None,
    classifier_source: str | None,
    comercio: str | None = None,
    tipo: str | None = None,
    requiere_revision: bool = False,
    pregunta_sugerida: str | None = None,
) -> None:
    ref = _db().collection("movements").document(mov_id)
    payload = {
        "suggested_category": suggested_category,
        "suggested_subcategory": suggested_subcategory,
        "confidence": confidence,
        "classifier_source": classifier_source,
        "comercio": comercio,
        "tipo": tipo,
        "requiere_revision": requiere_revision,
        "pregunta_sugerida": pregunta_sugerida,
    }
    _with_retry(lambda: ref.update(payload))


def mark_notified(ids: list[str]) -> None:
    if not ids:
        return
    now = _now()
    db = _db()
    batch = db.batch()
    for mid in ids:
        batch.update(db.collection("movements").document(mid), {"notified_at": now})
    batch.commit()


def get_pending() -> list[dict]:
    """Movimientos pendientes aún no notificados (para el cron diario)."""
    docs = _db().collection("movements").where("status", "==", "pendiente").get()
    rows = [d.to_dict() for d in docs if not d.to_dict().get("notified_at")]
    rows.sort(key=lambda x: (x.get("date", ""), x.get("inserted_at", "")), reverse=True)
    return rows


def get_all_pending() -> list[dict]:
    """Todos los movimientos con status=pendiente, hayan sido notificados o no."""
    docs = _db().collection("movements").where("status", "==", "pendiente").get()
    rows = [d.to_dict() for d in docs]
    rows.sort(key=lambda x: (x.get("date", ""), x.get("inserted_at", "")), reverse=True)
    return rows


def get_movements_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    db = _db()
    result: dict[str, dict] = {}
    for mid in ids:
        snap = db.collection("movements").document(mid).get()
        if snap.exists:
            result[mid] = snap.to_dict()
    return [result[i] for i in ids if i in result]


def update_decision(
    mov_id: str,
    *,
    status: str,
    final_category: str | None,
    final_subcategory: str | None,
    decided_by: str,
) -> None:
    _db().collection("movements").document(mov_id).update({
        "status": status,
        "final_category": final_category,
        "final_subcategory": final_subcategory,
        "decided_by": decided_by,
        "decided_at": _now(),
    })


def get_last_movements(limit: int = 10) -> list[dict]:
    limit = max(1, min(limit, 50))
    docs = (
        _db().collection("movements")
        .order_by("date", direction=fstore.Query.DESCENDING)
        .limit(limit)
        .get()
    )
    return [d.to_dict() for d in docs]


def count_pending() -> int:
    return len(_db().collection("movements").where("status", "==", "pendiente").get())


def count_total() -> int:
    return len(list(_db().collection("movements").get()))


# ── telegram_log ───────────────────────────────────────────────────────────

def record_telegram_log(
    *,
    direction: str,
    chat_id: str | None,
    message_id: str | None,
    text: str | None,
    payload: str | None = None,
) -> str:
    db = _db()
    ref = db.collection("telegram_log").document()
    ref.set({
        "direction": direction,
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "payload": payload,
        "created_at": _now(),
    })
    if payload:
        db.collection("config").document("last_batch_payload").set(
            {"value": payload, "updated_at": _now()}
        )
    return ref.id


def get_last_batch_payload() -> str | None:
    return get_config("last_batch_payload")


def get_latest_otp(after: str) -> str | None:
    # Filtra solo por rango en created_at (evita índice compuesto).
    # Filtra direction e inicio de texto en Python.
    docs = (
        _db().collection("telegram_log")
        .where("created_at", ">", after)
        .order_by("created_at", direction=fstore.Query.DESCENDING)
        .limit(50)
        .get()
    )
    for doc in docs:
        d = doc.to_dict()
        if d.get("direction") == "in" and (d.get("text") or "").lower().startswith("otp "):
            return d["text"]
    return None


# ── errors ─────────────────────────────────────────────────────────────────

def record_error(component: str, message: str, traceback: str | None = None) -> None:
    _db().collection("errors").document().set({
        "component": component,
        "message": message,
        "traceback": traceback,
        "created_at": _now(),
    })


def get_last_error() -> dict | None:
    docs = (
        _db().collection("errors")
        .order_by("created_at", direction=fstore.Query.DESCENDING)
        .limit(1)
        .get()
    )
    return docs[0].to_dict() if docs else None


# ── credentials (usadas por secrets_store.py) ──────────────────────────────

def get_credential_blob(bank: str) -> str | None:
    snap = _db().collection("credentials").document(bank.lower()).get()
    return snap.to_dict().get("blob") if snap.exists else None


def set_credential_blob(bank: str, blob_b64: str) -> None:
    _db().collection("credentials").document(bank.lower()).set({
        "bank": bank.lower(),
        "blob": blob_b64,
        "updated_at": _now(),
    })


def delete_credential(bank: str) -> bool:
    ref = _db().collection("credentials").document(bank.lower())
    if not ref.get().exists:
        return False
    ref.delete()
    return True


def list_credentials() -> list[str]:
    return sorted(d.id for d in _db().collection("credentials").get())


# ── wizard_state ───────────────────────────────────────────────────────────

def get_wizard_state(chat_id: str) -> dict[str, Any] | None:
    snap = _db().collection("wizard_state").document(chat_id).get()
    if not snap.exists:
        return None
    d = snap.to_dict()
    payload: dict[str, Any] = {}
    if d.get("payload"):
        try:
            payload = json.loads(d["payload"])
        except json.JSONDecodeError:
            pass
    return {"state": d["state"], "payload": payload}


def set_wizard_state(chat_id: str, state: str, payload: dict[str, Any] | None = None) -> None:
    _db().collection("wizard_state").document(chat_id).set({
        "state": state,
        "payload": json.dumps(payload or {}, ensure_ascii=False),
        "updated_at": _now(),
    })


def clear_wizard_state(chat_id: str) -> None:
    _db().collection("wizard_state").document(chat_id).delete()


# ── rules ──────────────────────────────────────────────────────────────────

def find_rule_for(description: str) -> dict | None:
    from .utils import normalize
    norm_desc = normalize(description)
    if not norm_desc:
        return None
    db = _db()

    # Exact match: dos filtros de igualdad no necesitan índice compuesto
    docs = (
        db.collection("rules")
        .where("match_type", "==", "exact")
        .where("pattern", "==", norm_desc)
        .limit(1)
        .get()
    )
    if docs:
        d = docs[0].to_dict()
        d["id"] = docs[0].id
        return d

    # Contains match: traemos todas y filtramos en Python
    docs = db.collection("rules").where("match_type", "==", "contains").get()
    best: dict | None = None
    for doc in docs:
        rule = doc.to_dict()
        rule["id"] = doc.id
        if rule.get("pattern") and rule["pattern"] in norm_desc:
            if best is None or rule.get("hits", 0) > best.get("hits", 0):
                best = rule
    return best


def bump_rule_hit(rule_id: str) -> None:
    from google.cloud.firestore_v1 import transforms
    _db().collection("rules").document(rule_id).update({
        "hits": transforms.Increment(1),
        "last_used_at": _now(),
    })


def add_rule(*, match_type: str, pattern: str, category: str, subcategory: str | None) -> str | None:
    db = _db()
    existing = (
        db.collection("rules")
        .where("match_type", "==", match_type)
        .where("pattern", "==", pattern)
        .where("category", "==", category)
        .limit(1)
        .get()
    )
    if existing:
        return None
    ref = db.collection("rules").document()
    ref.set({
        "match_type": match_type,
        "pattern": pattern,
        "category": category,
        "subcategory": subcategory,
        "hits": 0,
        "created_at": _now(),
        "last_used_at": None,
    })
    return ref.id


def count_rules() -> int:
    return len(list(_db().collection("rules").get()))


# ── config ─────────────────────────────────────────────────────────────────

def get_batch_ids() -> list[str]:
    val = get_config("last_batch_ids")
    return json.loads(val) if val else []


def set_batch_ids(ids: list[str]) -> None:
    set_config("last_batch_ids", json.dumps(ids))


def get_config(key: str) -> str | None:
    snap = _db().collection("config").document(key).get()
    return snap.to_dict().get("value") if snap.exists else None


def set_config(key: str, value: str) -> None:
    _db().collection("config").document(key).set({"value": value, "updated_at": _now()})


# ── browser_state (cookies de Playwright sincronizadas Mac↔Railway) ──────
# Para BCh: no se puede hacer login fresh desde Railway porque BCh detecta
# headless y rechaza credenciales. La estrategia: Diego logea manual desde
# Mac con HEADLESS=false, el state queda guardado acá; Railway descarga el
# state al inicio del scrape y lo usa sin re-login.

def get_browser_state(bank: str) -> str | None:
    """Devuelve el JSON serializado del storage_state de Playwright para `bank`,
    o None si no hay state guardado."""
    snap = _db().collection("browser_state").document(bank.lower()).get()
    return snap.to_dict().get("state_json") if snap.exists else None


def set_browser_state(bank: str, state_json: str) -> None:
    """Guarda el storage_state JSON de Playwright. Llamar después de un login
    exitoso para que otros procesos (Railway) puedan reusar las cookies."""
    _db().collection("browser_state").document(bank.lower()).set({
        "bank": bank.lower(),
        "state_json": state_json,
        "updated_at": _now(),
    })


# ── pending_corrections (Force Reply) ──────────────────────────────────────
# Cuando el usuario apreta "✏️ Corregir", el bot manda un mensaje con
# force_reply y guarda aquí el mapping message_id → mov_id, para que cuando
# el usuario responda (con reply_to_message_id) el bot sepa qué movimiento
# corregir, sin importar cuántas correcciones tenga simultáneamente.

def save_pending_correction(prompt_message_id: str, mov_id: str, chat_id: str, original_card_message_id: int | None = None) -> None:
    _db().collection("pending_corrections").document(prompt_message_id).set({
        "mov_id": mov_id,
        "chat_id": chat_id,
        "original_card_message_id": original_card_message_id,
        "created_at": _now(),
    })


def get_pending_correction(prompt_message_id: str) -> dict | None:
    snap = _db().collection("pending_corrections").document(prompt_message_id).get()
    return snap.to_dict() if snap.exists else None


def delete_pending_correction(prompt_message_id: str) -> None:
    _db().collection("pending_corrections").document(prompt_message_id).delete()
