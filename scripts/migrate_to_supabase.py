"""
Migra datos desde el SQLite local a Supabase.
Uso:
    DATABASE_URL=postgresql://... python scripts/migrate_to_supabase.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SQLITE_PATH = Path(__file__).resolve().parent.parent / "data" / "banco.db"


def migrate():
    if not SQLITE_PATH.exists():
        print(f"SQLite no encontrado en {SQLITE_PATH}")
        sys.exit(1)

    pg_url = os.environ.get("DATABASE_URL", "").strip()
    if not pg_url:
        print("Falta DATABASE_URL")
        sys.exit(1)

    src = sqlite3.connect(str(SQLITE_PATH))
    src.row_factory = sqlite3.Row

    dst = psycopg2.connect(pg_url, sslmode="require",
                           cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        _migrate_movements(src, dst)
        _migrate_rules(src, dst)
        _migrate_telegram_log(src, dst)
        _migrate_errors(src, dst)
        _migrate_credentials(src, dst)
        _migrate_wizard_state(src, dst)
        dst.commit()
        print("Migración completada.")
    except Exception as e:
        dst.rollback()
        print(f"Error: {e}")
        raise
    finally:
        src.close()
        dst.close()


def _migrate_movements(src, dst):
    rows = src.execute("SELECT * FROM movements").fetchall()
    with dst.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO movements VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                ) ON CONFLICT(id) DO NOTHING
            """, (
                r["id"], r["date"], r["description"], r["amount"],
                r["movement_type"], r["account"], r["bank"], r["raw_blob"],
                r["suggested_category"], r["suggested_subcategory"],
                r["confidence"], r["classifier_source"], r["status"],
                r["final_category"], r["final_subcategory"],
                r["decided_by"], r["decided_at"], r["notified_at"], r["inserted_at"],
            ))
    print(f"  movements: {len(rows)} filas")


def _migrate_rules(src, dst):
    rows = src.execute("SELECT * FROM rules").fetchall()
    with dst.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO rules (match_type, pattern, category, subcategory, hits, created_at, last_used_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
            """, (r["match_type"], r["pattern"], r["category"],
                  r["subcategory"], r["hits"], r["created_at"], r["last_used_at"]))
    print(f"  rules: {len(rows)} filas")


def _migrate_telegram_log(src, dst):
    rows = src.execute("SELECT * FROM telegram_log ORDER BY id LIMIT 1000").fetchall()
    with dst.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO telegram_log (direction, chat_id, message_id, text, payload, created_at)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (r["direction"], r["chat_id"], r["message_id"],
                  r["text"], r["payload"], r["created_at"]))
    print(f"  telegram_log: {len(rows)} filas (últimas 1000)")


def _migrate_errors(src, dst):
    rows = src.execute("SELECT * FROM errors ORDER BY id LIMIT 200").fetchall()
    with dst.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO errors (component, message, traceback, created_at)
                VALUES (%s,%s,%s,%s)
            """, (r["component"], r["message"], r["traceback"], r["created_at"]))
    print(f"  errors: {len(rows)} filas")


def _migrate_credentials(src, dst):
    import base64
    rows = src.execute("SELECT * FROM credentials").fetchall()
    with dst.cursor() as cur:
        for r in rows:
            blob = r["blob"]
            if isinstance(blob, (bytes, memoryview)):
                blob = base64.b64encode(bytes(blob)).decode("ascii")
            cur.execute("""
                INSERT INTO credentials (bank, blob, updated_at)
                VALUES (%s,%s,%s)
                ON CONFLICT(bank) DO UPDATE SET blob=EXCLUDED.blob
            """, (r["bank"], blob, r["updated_at"]))
    print(f"  credentials: {len(rows)} filas")


def _migrate_wizard_state(src, dst):
    rows = src.execute("SELECT * FROM wizard_state").fetchall()
    with dst.cursor() as cur:
        for r in rows:
            cur.execute("""
                INSERT INTO wizard_state (chat_id, state, payload, updated_at)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT(chat_id) DO UPDATE SET state=EXCLUDED.state
            """, (r["chat_id"], r["state"], r["payload"], r["updated_at"]))
    print(f"  wizard_state: {len(rows)} filas")


if __name__ == "__main__":
    migrate()
