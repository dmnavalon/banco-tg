from __future__ import annotations

import difflib
import unicodedata

from flask import Blueprint, jsonify, request

from ... import db as db_mod
from ...classifier import (
    EXTENSIBLE_CATEGORIES,
    INCOME_CATEGORIES,
    INTERNAL_CATEGORIES,
    get_taxonomy,
    invalidate_taxonomy_cache,
)
from ..auth import require_token

bp = Blueprint("categories", __name__, url_prefix="/api/categories")

_MAX_LEN = 60


def _norm(s: str) -> str:
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode("ascii").strip()


@bp.get("")
@require_token
def list_categories():
    """Devuelve la taxonomía vigente (base + overrides). El dashboard la usa
    para popular los selectores de categoría/subcategoría en edición y filtros.
    Las categorías extensibles ('Gastos por rendir') aceptan subcategorías nuevas."""
    return jsonify({
        "taxonomy": get_taxonomy(),
        "income_categories": sorted(INCOME_CATEGORIES),
        "extensible_categories": sorted(EXTENSIBLE_CATEGORIES),
    })


@bp.post("")
@require_token
def create_category():
    """Crea (o reutiliza) una combinación cat/sub en la taxonomía persistida.

    - 200 + created=false si la combinación ya existe (idempotente).
    - 201 + created=true si se persistió una nueva.
    - 422 si la entrada es inválida.
    - `similar` siempre puede traer cats/subs parecidas para que el frontend
      muestre advertencia suave (no bloquea).
    """
    payload = request.get_json(silent=True) or {}
    cat = (payload.get("cat") or "").strip()
    sub = (payload.get("sub") or "").strip()

    if not cat or not sub:
        return jsonify({"error": "validation_error", "message": "cat y sub son obligatorios"}), 422
    if len(cat) > _MAX_LEN or len(sub) > _MAX_LEN:
        return jsonify({"error": "validation_error", "message": f"cat y sub deben tener <= {_MAX_LEN} caracteres"}), 422
    if cat in INCOME_CATEGORIES or cat in INTERNAL_CATEGORIES:
        return jsonify({
            "error": "validation_error",
            "message": "No se pueden crear subcategorías nuevas en categorías de ingreso o transferencias internas — toca el código para esos flujos.",
        }), 422

    taxonomy = get_taxonomy()

    # Detección de similares (advertencia, no bloquea).
    cat_keys = list(taxonomy.keys())
    similar_cats = [c for c in difflib.get_close_matches(cat, cat_keys, n=3, cutoff=0.85) if c != cat]
    target_subs = taxonomy.get(cat, [])
    similar_subs = [s for s in difflib.get_close_matches(sub, target_subs, n=3, cutoff=0.85) if s != sub]

    # Match exacto post-normalización (cubre tildes/case).
    n_cat = _norm(cat)
    n_sub = _norm(sub)
    for existing_cat, subs in taxonomy.items():
        if _norm(existing_cat) != n_cat:
            continue
        for existing_sub in subs:
            if _norm(existing_sub) == n_sub:
                return jsonify({
                    "created": False,
                    "cat": existing_cat,
                    "sub": existing_sub,
                    "taxonomy": taxonomy,
                    "similar": {"cats": similar_cats, "subs": similar_subs},
                }), 200

    try:
        db_mod.add_taxonomy_override(cat, sub)
    except Exception as e:
        return jsonify({"error": "internal", "message": str(e)}), 500
    invalidate_taxonomy_cache()

    return jsonify({
        "created": True,
        "cat": cat,
        "sub": sub,
        "taxonomy": get_taxonomy(),
        "similar": {"cats": similar_cats, "subs": similar_subs},
    }), 201
