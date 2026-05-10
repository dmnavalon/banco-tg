from __future__ import annotations

import os
from functools import wraps

from flask import jsonify, request


def _expected_token() -> str | None:
    return (os.environ.get("DASHBOARD_API_TOKEN") or "").strip() or None


def require_token(fn):
    """Decorador para endpoints que requieren `Authorization: Bearer <token>`.
    Si la env var DASHBOARD_API_TOKEN no está configurada, rechaza todas las
    requests (fail-closed: nunca abierto sin auth)."""
    @wraps(fn)
    def _wrap(*args, **kwargs):
        expected = _expected_token()
        if not expected:
            return jsonify({"error": "api_disabled", "message": "DASHBOARD_API_TOKEN no configurado"}), 503
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": "unauthorized"}), 401
        provided = header[len("Bearer "):].strip()
        # Comparación constante en tiempo para evitar timing attacks.
        import hmac
        if not hmac.compare_digest(provided, expected):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return _wrap
