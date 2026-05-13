from __future__ import annotations

from firebase_admin import storage

from . import db
from .utils import get_logger

log = get_logger("screenshot_storage")


def _blob(mov_id: str):
    db.init_if_needed()
    return storage.bucket().blob(f"screenshots/{mov_id}.png")


def upload(mov_id: str, png_bytes: bytes) -> None:
    try:
        _blob(mov_id).upload_from_string(png_bytes, content_type="image/png")
    except Exception as e:
        log.warning(f"upload screenshot {mov_id} falló: {type(e).__name__}: {e}")


def download(mov_id: str) -> bytes | None:
    try:
        return _blob(mov_id).download_as_bytes()
    except Exception as e:
        log.info(f"screenshot {mov_id} no disponible: {type(e).__name__}: {e}")
        return None


def delete(mov_id: str) -> None:
    try:
        _blob(mov_id).delete()
    except Exception as e:
        log.info(f"delete screenshot {mov_id}: {type(e).__name__}: {e}")
