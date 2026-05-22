from __future__ import annotations

import os

from flask import Flask, jsonify

from ..utils import get_logger

log = get_logger("api")


def create_app() -> Flask | None:
    """Crea la app Flask con todos los blueprints. Devuelve None si la feature
    está apagada (ENABLE_MOVIMIENTOS_REVIEW != "true") — el caller decide si
    levantar el thread o no."""
    if (os.environ.get("ENABLE_MOVIMIENTOS_REVIEW") or "").lower() != "true":
        return None

    app = Flask(__name__)

    # CORS mínimo: el origen del dashboard se autoriza vía env var. Como el
    # dashboard hace requests server-side desde sus route handlers (Vercel),
    # CORS no es estrictamente necesario, pero se deja para tests manuales
    # con curl/postman desde browser.
    origin = (os.environ.get("DASHBOARD_ORIGIN") or "").strip()

    @app.after_request
    def _cors(resp):
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Headers"] = "Authorization,Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return resp

    @app.get("/api/health")
    def _health():
        from .. import db
        try:
            # Best-effort: contar pendientes (lectura barata) para confirmar
            # conectividad con Firestore. Si falla, lo reportamos sin 500.
            db.count_pending()
            db_ok = True
            db_err = None
        except Exception as e:
            db_ok = False
            db_err = f"{type(e).__name__}: {str(e)[:120]}"
        return jsonify({
            "status": "ok",
            "db": "ok" if db_ok else "error",
            "db_error": db_err,
            "feature": "movimientos_review",
        })

    from .routes.categories import bp as categories_bp
    from .routes.movements import bp as movements_bp
    from .routes.patrimonio import bp as patrimonio_bp
    app.register_blueprint(movements_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(patrimonio_bp)

    return app


def run_app() -> None:
    """Punto de entrada del thread de Flask. Se invoca desde bot.main()
    si la feature está activa. Levanta werkzeug serving en threaded=True
    para soportar requests concurrentes sin bloquear el long-poll."""
    app = create_app()
    if app is None:
        log.info("ENABLE_MOVIMIENTOS_REVIEW=false — API HTTP no iniciada.")
        return
    port = int(os.environ.get("PORT", "8080"))
    log.info(f"API HTTP escuchando en 0.0.0.0:{port}")
    # use_reloader=False es CRÍTICO en thread: el reloader spawnea un proceso
    # hijo que duplicaría el long-poll del bot.
    from werkzeug.serving import run_simple
    run_simple(
        hostname="0.0.0.0",
        port=port,
        application=app,
        use_reloader=False,
        threaded=True,
    )
