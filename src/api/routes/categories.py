from __future__ import annotations

from flask import Blueprint, jsonify

from ...classifier import EXTENSIBLE_CATEGORIES, INCOME_CATEGORIES, TAXONOMY
from ..auth import require_token

bp = Blueprint("categories", __name__, url_prefix="/api/categories")


@bp.get("")
@require_token
def list_categories():
    """Devuelve la taxonomía oficial. El dashboard la usa para popular los
    selectores de categoría/subcategoría en edición y filtros. Las categorías
    extensibles ('Gastos por rendir') aceptan subcategorías nuevas."""
    return jsonify({
        "taxonomy": TAXONOMY,
        "income_categories": sorted(INCOME_CATEGORIES),
        "extensible_categories": sorted(EXTENSIBLE_CATEGORIES),
    })
