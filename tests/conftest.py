from __future__ import annotations

import os
import sys
from pathlib import Path

# Permitir `from src.services.movements import ...` desde la raíz del proyecto.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Evitar que db.py intente cargar credenciales reales en tests.
os.environ.setdefault("FIREBASE_KEY_JSON", "")

import pytest
from mockfirestore import MockFirestore

from src import db


@pytest.fixture
def fs(monkeypatch):
    """Fixture que reemplaza el cliente Firestore real con un MockFirestore en
    memoria. También parchea `db.run_txn` para que invoque el callback con la
    transacción del mock (mock-firestore tiene Transaction pero la API es
    ligeramente distinta)."""
    mock = MockFirestore()

    monkeypatch.setattr(db, "_client", mock)
    monkeypatch.setattr(db, "_db", lambda: mock)

    # mock-firestore expone una Transaction pero requiere un transaction-id
    # para commitear, lo que el SDK real maneja transparente. En tests no
    # necesitamos atomicidad real — basta con un wrapper que mapea las
    # operaciones a las del DocumentReference subyacente. La atomicidad real
    # se valida en producción contra Firestore.
    class _FakeTxn:
        def get(self, ref_or_query):
            # En el SDK real Transaction.get devuelve un Iterable[DocumentSnapshot].
            return iter([ref_or_query.get()])

        def update(self, ref, data):
            ref.update(data)

        def set(self, ref, data, *args, **kwargs):
            ref.set(data)

        def delete(self, ref):
            ref.delete()

    def _run_txn_mock(callback):
        return callback(_FakeTxn())

    monkeypatch.setattr(db, "run_txn", _run_txn_mock)
    return mock


@pytest.fixture
def insert_pending(fs):
    """Crea un movimiento en estado pending con todos los campos requeridos.
    Devuelve una factory que el test usa para crear N movimientos distintos."""
    def _factory(mov_id: str = "mov-1", **overrides):
        payload = {
            "id": mov_id,
            "date": "2026-05-08",
            "description": "COMPRA UBER",
            "amount": -5000.0,
            "movement_type": "cargo",
            "account": "falabella",
            "bank": "falabella",
            "raw_blob": "{}",
            "suggested_category": "Transporte",
            "suggested_subcategory": "Uber o taxi",
            "confidence": 0.92,
            "classifier_source": "agent",
            "comercio": "Uber",
            "tipo": "Egreso",
            "requiere_revision": False,
            "pregunta_sugerida": None,
            "persona": "Titular",
            "cuotas_actual": None,
            "cuotas_total": None,
            "cuota_monto": None,
            "saldo": None,
            "status": "pendiente",
            "review_status": "pending",
            "sheet_sync_status": "not_ready",
            "version": 1,
            "final_category": None,
            "final_subcategory": None,
            "comercio_final": None,
            "comment": None,
            "ignore_reason": None,
            "decided_by": None,
            "decided_at": None,
            "corrected_by": None,
            "corrected_at": None,
            "last_action_source": "system",
            "sheet_row_id": None,
            "sync_error_message": None,
            "notified_at": None,
            "tg_photo_file_id": None,
            "inserted_at": "2026-05-08 09:00:00",
            "updated_at": "2026-05-08 09:00:00",
        }
        payload.update(overrides)
        fs.collection("movements").document(mov_id).set(payload)
        return payload

    return _factory
