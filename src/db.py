from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

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
            firebase_admin.initialize_app(cred, {
                "storageBucket": "control-gastos-c53b6.firebasestorage.app",
            })
        _client = fstore.client()
    return _client


def init_if_needed() -> None:
    _db()


_TZ_SANTIAGO = ZoneInfo("America/Santiago")


def _now() -> str:
    """Devuelve timestamp en America/Santiago. Mantenerlo consistente entre
    Mac (TZ local CLT) y Railway (TZ UTC) evita que comparaciones cross-host
    queden desfasadas."""
    return datetime.now(tz=_TZ_SANTIAGO).strftime("%Y-%m-%d %H:%M:%S")


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
    cuotas_actual: int | None = None,
    cuotas_total: int | None = None,
    cuota_monto: float | None = None,
    saldo: float | None = None,
) -> bool:
    ref = _db().collection("movements").document(mov_id)
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
        "cuotas_actual": cuotas_actual,
        "cuotas_total": cuotas_total,
        "cuota_monto": cuota_monto,
        "saldo": saldo,
        "status": "pendiente",
        "final_category": None,
        "final_subcategory": None,
        "decided_by": None,
        "decided_at": None,
        "notified_at": None,
        "tg_photo_file_id": None,
        "inserted_at": _now(),
        # Campos del modelo dual de la feature "Movimientos" (revisión masiva en
        # dashboard). El bot legacy sigue usando `status`; los servicios nuevos
        # leen/escriben review_status + sheet_sync_status + version. Mantener
        # ambos sincronizados durante la transición evita migración destructiva.
        "review_status": "pending",
        "sheet_sync_status": "not_ready",
        "version": 1,
        "updated_at": _now(),
        "comercio_final": None,
        "comment": None,
        "ignore_reason": None,
        "last_action_source": "system",
        "corrected_at": None,
        "corrected_by": None,
        "sheet_row_id": None,
        "sync_error_message": None,
    }
    # `create()` es atómico — falla con AlreadyExists si el doc ya existe.
    # Reemplaza el patrón read-then-write que tenía race condition entre
    # procesos concurrentes con el mismo mov_id.
    try:
        _with_retry(lambda: ref.create(payload))
    except gax_exceptions.AlreadyExists:
        return False
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


_PENDING_PAGE_SIZE = 500


def get_pending() -> list[dict]:
    """Movimientos pendientes aún no notificados (para el cron diario).
    Limita a `_PENDING_PAGE_SIZE` para no saturar memoria si se acumulan."""
    docs = (
        _db().collection("movements")
        .where("status", "==", "pendiente")
        .limit(_PENDING_PAGE_SIZE)
        .get()
    )
    rows = [d.to_dict() for d in docs if not d.to_dict().get("notified_at")]
    rows.sort(key=lambda x: (x.get("date", ""), x.get("inserted_at", "")), reverse=True)
    return rows


def get_all_pending() -> list[dict]:
    """Todos los movimientos con status=pendiente, hayan sido notificados o no."""
    docs = (
        _db().collection("movements")
        .where("status", "==", "pendiente")
        .limit(_PENDING_PAGE_SIZE)
        .get()
    )
    rows = [d.to_dict() for d in docs]
    rows.sort(key=lambda x: (x.get("date", ""), x.get("inserted_at", "")), reverse=True)
    return rows


def get_ignored() -> list[dict]:
    """Movimientos con status=ignorado, ordenados por fecha desc.
    Se reenvían al final de la cola en /pending por si Diego se arrepiente."""
    docs = (
        _db().collection("movements")
        .where("status", "==", "ignorado")
        .limit(_PENDING_PAGE_SIZE)
        .get()
    )
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
    ignore_reason: str | None = None,
) -> None:
    payload: dict = {
        "status": status,
        "final_category": final_category,
        "final_subcategory": final_subcategory,
        "decided_by": decided_by,
        "decided_at": _now(),
    }
    # Solo seteamos ignore_reason cuando se ignora; en aprobar/corregir no lo
    # tocamos para no pisar una razón previa (útil si el usuario reactiva un
    # ignorado, queda registro histórico de por qué lo había ignorado antes).
    if status == "ignorado":
        payload["ignore_reason"] = ignore_reason
    _db().collection("movements").document(mov_id).update(payload)


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
    """Usa Firestore aggregate count() — O(1) en costo, no descarga docs."""
    try:
        agg = _db().collection("movements").where("status", "==", "pendiente").count().get()
        # `count().get()` devuelve [[AggregationResult]] — extraer el valor.
        return int(agg[0][0].value)
    except Exception:
        # Fallback al método viejo si el SDK no soporta count() o algo falla.
        return len(_db().collection("movements").where("status", "==", "pendiente").get())


def count_total() -> int:
    """Usa Firestore aggregate count() — O(1) en costo, no descarga docs."""
    try:
        agg = _db().collection("movements").count().get()
        return int(agg[0][0].value)
    except Exception:
        return len(list(_db().collection("movements").get()))


# ── movements (helpers para servicios "Movimientos") ──────────────────────
# Estas funciones soportan la capa central src/services/movements.py. Conviven
# con las legacy (insert_movement, update_decision, get_pending, etc.) sin
# romperlas. Las nuevas versiones manejan version + transacciones.


def get_movement_by_id(mov_id: str) -> dict | None:
    snap = _db().collection("movements").document(mov_id).get()
    return snap.to_dict() if snap.exists else None


def get_movement_ref(mov_id: str):
    """Devuelve un DocumentReference para usar dentro de una transacción.
    Pensado para servicios — no usar directamente fuera de transactional."""
    return _db().collection("movements").document(mov_id)


def run_txn(callback):
    """Ejecuta `callback(transaction)` dentro de una transacción Firestore.
    Centralizar el decorator `@firestore.transactional` acá hace que los
    tests puedan monkey-patchear esta sola función para correr la lógica
    sin transacción real (mockfirestore no provee transactional)."""
    transaction = _db().transaction()

    @fstore.transactional
    def _wrapped(t):
        return callback(t)

    return _wrapped(transaction)


# Filtros válidos para query_movements. Los servidores los validan antes de
# llamar — cualquier filtro fuera de esta lista se ignora silenciosamente.
_QUERY_REVIEW_STATUSES = {
    "pending", "approved", "corrected_pending", "corrected_approved",
    "ignored", "error",
}


def query_movements(
    *,
    review_status: str | list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    bank: str | None = None,
    persona: str | None = None,
    final_category: str | None = None,
    final_subcategory: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    confidence_min: float | None = None,
    description_contains: str | None = None,
    comercio_contains: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query genérico de movimientos para el dashboard. Filtros server-side los
    que Firestore puede hacer barato (where), el resto en Python tras traer.

    Para mantener el costo bajo: aplicar primero el filtro más selectivo
    (review_status si está) y limitar resultados a 500 antes de filtros locales.
    Las queries que combinan múltiples campos pueden requerir índices
    compuestos — se documentan en HANDOFF.md."""
    limit = max(1, min(int(limit or 100), 500))
    q = _db().collection("movements")

    # Normalizar review_status a lista para usar `in` en Firestore (max 30 vals).
    statuses: list[str] | None = None
    if isinstance(review_status, str):
        if review_status in _QUERY_REVIEW_STATUSES:
            statuses = [review_status]
    elif isinstance(review_status, list):
        statuses = [s for s in review_status if s in _QUERY_REVIEW_STATUSES]
        if not statuses:
            statuses = None

    if statuses and len(statuses) == 1:
        q = q.where("review_status", "==", statuses[0])
    elif statuses:
        q = q.where("review_status", "in", statuses)

    # Bank igualdad — barato.
    if bank:
        q = q.where("bank", "==", bank)

    # Range queries (date) — Firestore acepta una sola desigualdad por field,
    # así que `from`+`to` ambas en `date` están OK.
    if date_from:
        q = q.where("date", ">=", date_from)
    if date_to:
        q = q.where("date", "<=", date_to)

    # Limit fetch para no traer toda la collection.
    docs = q.limit(limit).get()
    rows = [d.to_dict() for d in docs]

    # Filtros que se hacen en Python (no requieren índices).
    if persona:
        rows = [r for r in rows if (r.get("persona") or "") == persona]
    if final_category:
        rows = [r for r in rows if (r.get("final_category") or r.get("suggested_category") or "") == final_category]
    if final_subcategory:
        rows = [r for r in rows if (r.get("final_subcategory") or r.get("suggested_subcategory") or "") == final_subcategory]
    if min_amount is not None:
        rows = [r for r in rows if abs(float(r.get("amount") or 0)) >= float(min_amount)]
    if max_amount is not None:
        rows = [r for r in rows if abs(float(r.get("amount") or 0)) <= float(max_amount)]
    if confidence_min is not None:
        rows = [r for r in rows if float(r.get("confidence") or 0) >= float(confidence_min)]
    if description_contains:
        needle = description_contains.lower()
        rows = [r for r in rows if needle in (r.get("description") or "").lower()]
    if comercio_contains:
        needle = comercio_contains.lower()
        rows = [r for r in rows if needle in (r.get("comercio") or r.get("comercio_final") or "").lower()]

    rows.sort(key=lambda x: (x.get("date", ""), x.get("inserted_at", "")), reverse=True)
    return rows


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


def list_credential_docs() -> list[dict[str, Any]]:
    """Lista todos los docs de `credentials` con sus campos (no solo IDs).
    Usado por `secrets_store.list_configured()` para filtrar los bancos
    marcados como inválidos por el banco (campo `invalid_since`)."""
    docs = []
    for d in _db().collection("credentials").get():
        data = d.to_dict() or {}
        data["bank"] = d.id
        docs.append(data)
    return sorted(docs, key=lambda x: x["bank"])


def mark_credential_invalid(bank: str, reason: str) -> None:
    """Marca la credencial como rechazada por el banco. El doc se actualiza
    parcialmente para no tocar el blob cifrado: el siguiente `set_credential_blob`
    (vía /cred) pisa el doc completo y limpia el flag automáticamente."""
    _db().collection("credentials").document(bank.lower()).update({
        "invalid_since": _now(),
        "invalid_reason": (reason or "")[:500],
    })


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


# ── pending_user_actions (Force Reply genérico) ────────────────────────────
# Cuando el usuario apreta "✏️ Corregir" o "🚫 Ignorar", el bot manda un mensaje
# con force_reply y guarda aquí el mapping `prompt_message_id → (mov_id, action)`,
# para que cuando responda (con reply_to_message_id) el bot sepa qué movimiento
# es y qué hacer (corregir vs ignorar con razón).

def save_pending_user_action(
    prompt_message_id: str,
    mov_id: str,
    chat_id: str,
    action: str,                       # "correct" | "ignore"
    original_card_message_id: int | None = None,
) -> None:
    _db().collection("pending_user_actions").document(prompt_message_id).set({
        "mov_id": mov_id,
        "chat_id": chat_id,
        "action": action,
        "original_card_message_id": original_card_message_id,
        "created_at": _now(),
    })


def get_pending_user_action(prompt_message_id: str) -> dict | None:
    """Devuelve el dict con `action` ('correct' o 'ignore'), `mov_id`, etc.
    Para retro-compat, también busca en la collection vieja `pending_corrections`
    y asume `action="correct"` si la encuentra ahí."""
    snap = _db().collection("pending_user_actions").document(prompt_message_id).get()
    if snap.exists:
        return snap.to_dict()
    # Retro-compat con la collection vieja: si todavía hay docs ahí, los
    # tratamos como correct.
    legacy = _db().collection("pending_corrections").document(prompt_message_id).get()
    if legacy.exists:
        d = legacy.to_dict()
        d.setdefault("action", "correct")
        return d
    return None


def delete_pending_user_action(prompt_message_id: str) -> None:
    _db().collection("pending_user_actions").document(prompt_message_id).delete()
    # Best-effort: si quedó en la legacy, limpiar también.
    try:
        _db().collection("pending_corrections").document(prompt_message_id).delete()
    except Exception:
        pass


# Aliases retrocompatibles para call sites que aún usan los nombres viejos.
def save_pending_correction(prompt_message_id: str, mov_id: str, chat_id: str, original_card_message_id: int | None = None) -> None:
    save_pending_user_action(prompt_message_id, mov_id, chat_id, "correct", original_card_message_id)


def get_pending_correction(prompt_message_id: str) -> dict | None:
    return get_pending_user_action(prompt_message_id)


def delete_pending_correction(prompt_message_id: str) -> None:
    delete_pending_user_action(prompt_message_id)


# ── taxonomy_overrides (cats/subs creadas por el usuario en el dashboard) ─
# El catálogo base vive hardcoded en classifier._BASE_TAXONOMY. Cuando Diego
# crea una combinación nueva desde el dashboard, se persiste acá. El classifier
# lee ambos al clasificar (ver classifier.get_taxonomy()).

def add_taxonomy_override(cat: str, sub: str) -> None:
    """Idempotente: dedupea por lectura+escritura. La cantidad de overrides es
    chica (decenas), no vale la pena ArrayUnion + transacción para el caso real
    (un solo usuario creando desde el dashboard). Si hace falta concurrencia
    real, envolver en run_txn."""
    ref = _db().collection("taxonomy_overrides").document(cat)
    snap = ref.get()
    existing: list[str] = []
    if snap.exists:
        data = snap.to_dict() or {}
        raw = data.get("subs") or []
        if isinstance(raw, list):
            existing = [s for s in raw if isinstance(s, str)]
    if sub in existing:
        ref.set({"updated_at": _now()}, merge=True)
        return
    new_subs = list(existing) + [sub]
    ref.set({
        "cat": cat,
        "subs": new_subs,
        "updated_at": _now(),
    }, merge=True)


def list_taxonomy_overrides() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for doc in _db().collection("taxonomy_overrides").stream():
        data = doc.to_dict() or {}
        subs = data.get("subs") or []
        if isinstance(subs, list):
            clean = [s for s in subs if isinstance(s, str) and s.strip()]
            if clean:
                out[doc.id] = clean
    return out


# ── patrimonio_state (cross-host coordination Railway ↔ Mac local) ─────
# El bot Railway no puede correr scrapers de patrimonio (no Mac Keychain ni
# storage Playwright). Cuando el usuario clickea "Actualizar ahora" en el
# dashboard de producción, Railway escribe un request acá. El daemon que
# corre en la Mac de Diego polea cada 30s, ve el request nuevo, ejecuta
# `runner.run_all()` y escribe el resultado. El dashboard polea
# `/api/patrimonio/status` cada 5s para mostrar el resultado real-time.

_PATRIMONIO_DOC = "patrimonio_state"


def request_patrimonio_sync() -> str:
    """Marca un request nuevo de sync. Devuelve el timestamp del request
    (sirve como job_id para que el cliente sepa cuál esperar)."""
    now = _now()
    _db().collection("config").document(_PATRIMONIO_DOC).set({
        "last_request_at": now,
        "updated_at": now,
    }, merge=True)
    return now


def get_patrimonio_state() -> dict:
    """Devuelve el estado completo o un dict vacío si no hay nada."""
    snap = _db().collection("config").document(_PATRIMONIO_DOC).get()
    return snap.to_dict() if snap.exists else {}


def set_patrimonio_running(running: bool, started_at: str | None = None) -> None:
    payload: dict = {"running": running, "updated_at": _now()}
    if started_at is not None:
        payload["started_at"] = started_at
    _db().collection("config").document(_PATRIMONIO_DOC).set(payload, merge=True)


def set_patrimonio_result(
    summary: dict | None,
    error: str | None,
    processed_at: str,
) -> None:
    """El daemon llama esto al terminar (con summary) o al fallar (con error)."""
    _db().collection("config").document(_PATRIMONIO_DOC).set({
        "summary": summary,
        "error": error,
        "last_processed_at": processed_at,
        "running": False,
        "updated_at": _now(),
    }, merge=True)


def patrimonio_daemon_heartbeat() -> None:
    """El daemon llama esto cada loop iteration para que el dashboard pueda
    detectar si la Mac está respondiendo (heartbeat reciente = OK)."""
    _db().collection("config").document(_PATRIMONIO_DOC).set({
        "daemon_heartbeat_at": _now(),
    }, merge=True)
