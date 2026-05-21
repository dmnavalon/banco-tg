"""Screenshot storage — DESACTIVADO desde 2026-05-15.

El proyecto Firebase está en plan Spark (sin tarjeta), así que no podemos
habilitar Cloud Storage. Las fotos se persisten vía `tg_photo_file_id` que se
guarda automáticamente en Firestore cuando `telegram_notify.send_card` envía
la foto a TG. Los `file_id` de Telegram no expiran y permiten reenviar la foto
sin re-uploading.

Este módulo queda como no-op por compatibilidad con callers existentes
(`services/movements.py` lo usa para limpiar fotos al borrar un mov).

Si en el futuro se habilita Storage (Blaze o equivalente), restaurar las
implementaciones reales y volver a llamarlo desde `run_daily.py` y
`telegram_notify.py`.
"""
from __future__ import annotations

from .utils import get_logger

log = get_logger("screenshot_storage")


def upload(mov_id: str, png_bytes: bytes) -> None:
    """No-op: no hay Storage. La foto se persiste implícitamente via TG file_id."""
    return None


def download(mov_id: str) -> bytes | None:
    """No-op: no hay Storage. Devuelve None — el caller debe usar `tg_photo_file_id`."""
    return None


def delete(mov_id: str) -> None:
    """No-op: no hay Storage. El cleanup de `tg_photo_file_id` lo hace Firestore al
    borrar el doc del mov (campo se va con el documento)."""
    return None
