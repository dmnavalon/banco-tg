"""
Migra datos desde el SQLite local a Firebase Firestore.
Uso:
    FIREBASE_KEY_JSON='{"type":"service_account",...}' python scripts/migrate_to_firebase.py
O con archivo local:
    python scripts/migrate_to_firebase.py   (lee data/firebase_service_account.json)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SQLITE_PATH = Path(__file__).resolve().parent.parent / "data" / "banco.db"


def _init_firebase():
    import firebase_admin
    from firebase_admin import credentials, firestore
    import os

    key_json = os.environ.get("FIREBASE_KEY_JSON", "").strip()
    if key_json:
        cred = credentials.Certificate(json.loads(key_json))
    else:
        key_path = Path(__file__).resolve().parent.parent / "data" / "firebase_service_account.json"
        if not key_path.exists():
            print(f"No se encontró {key_path}. Configura FIREBASE_KEY_JSON o el archivo.")
            sys.exit(1)
        cred = credentials.Certificate(str(key_path))

    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    return firestore.client()


def migrate():
    if not SQLITE_PATH.exists():
        print(f"SQLite no encontrado en {SQLITE_PATH}")
        sys.exit(1)

    db = _init_firebase()
    src = sqlite3.connect(str(SQLITE_PATH))
    src.row_factory = sqlite3.Row

    try:
        _migrate_movements(src, db)
        _migrate_rules(src, db)
        _migrate_credentials(src, db)
        _migrate_wizard_state(src, db)
        print("Migración completada.")
    except Exception as e:
        print(f"Error: {e}")
        raise
    finally:
        src.close()


def _migrate_movements(src, db):
    rows = src.execute("SELECT * FROM movements").fetchall()
    batch = db.batch()
    count = 0
    for r in rows:
        ref = db.collection("movements").document(r["id"])
        if ref.get().exists:
            continue
        batch.set(ref, {
            "id": r["id"],
            "date": r["date"],
            "description": r["description"],
            "amount": r["amount"],
            "movement_type": r["movement_type"],
            "account": r["account"],
            "bank": r["bank"],
            "raw_blob": r["raw_blob"],
            "suggested_category": r["suggested_category"],
            "suggested_subcategory": r["suggested_subcategory"],
            "confidence": r["confidence"],
            "classifier_source": r["classifier_source"],
            "status": r["status"] or "pendiente",
            "final_category": r["final_category"],
            "final_subcategory": r["final_subcategory"],
            "decided_by": r["decided_by"],
            "decided_at": r["decided_at"],
            "notified_at": r["notified_at"],
            "inserted_at": r["inserted_at"],
        })
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
    batch.commit()
    print(f"  movements: {count} filas migradas (de {len(rows)} totales)")


def _migrate_rules(src, db):
    rows = src.execute("SELECT * FROM rules").fetchall()
    count = 0
    for r in rows:
        ref = db.collection("rules").document()
        ref.set({
            "match_type": r["match_type"],
            "pattern": r["pattern"],
            "category": r["category"],
            "subcategory": r["subcategory"],
            "hits": r["hits"] or 0,
            "created_at": r["created_at"],
            "last_used_at": r["last_used_at"],
        })
        count += 1
    print(f"  rules: {count} filas")


def _migrate_credentials(src, db):
    import base64
    rows = src.execute("SELECT * FROM credentials").fetchall()
    for r in rows:
        blob = r["blob"]
        if isinstance(blob, (bytes, memoryview)):
            blob = base64.b64encode(bytes(blob)).decode("ascii")
        db.collection("credentials").document(r["bank"].lower()).set({
            "bank": r["bank"].lower(),
            "blob": blob,
            "updated_at": r["updated_at"],
        })
    print(f"  credentials: {len(rows)} filas")


def _migrate_wizard_state(src, db):
    rows = src.execute("SELECT * FROM wizard_state").fetchall()
    for r in rows:
        db.collection("wizard_state").document(r["chat_id"]).set({
            "state": r["state"],
            "payload": r["payload"],
            "updated_at": r["updated_at"],
        })
    print(f"  wizard_state: {len(rows)} filas")


if __name__ == "__main__":
    migrate()
