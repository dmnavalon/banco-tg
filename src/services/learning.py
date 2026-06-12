"""Aprendizaje de reglas a partir de decisiones del usuario.

Cada vez que Diego aprueba un movimiento (directo o tras corrección), la
categoría final se persiste como regla en Firestore. La próxima aparición del
mismo comercio se clasifica por regla (confidence 1.0, sin llamada al LLM).
Si una regla existente para ese patrón apunta a otra categoría —es decir, la
regla se equivocó y el usuario la corrigió— `upsert_rule` la actualiza en vez
de duplicarla, así el error no se repite.
"""

from __future__ import annotations

from typing import Any

from .. import db
from ..utils import get_logger, normalize

log = get_logger("services.learning")

# Patrones más cortos que esto matchean por accidente ("PAGO", "COMPRA").
_MIN_PATTERN_LEN = 4

# "Gastos por rendir" depende del contexto, no del comercio: la misma compra
# en el mismo local puede ser personal hoy y por rendir mañana. Una regla aquí
# clasificaría mal para siempre, así que no se aprende de estas decisiones.
_NO_LEARN_CATEGORIES = {"Gastos por rendir"}


def learn_from_decision(mov: dict[str, Any]) -> None:
    """Crea/actualiza la regla derivada de la decisión final de un movimiento.

    Best-effort: el caller la envuelve en try/except — un fallo aquí jamás
    debe romper una aprobación.
    """
    desc = mov.get("description") or ""
    cat = mov.get("final_category")
    sub = mov.get("final_subcategory")
    if not desc or not cat or cat in _NO_LEARN_CATEGORIES:
        return

    norm_desc = normalize(desc)
    if len(norm_desc) < _MIN_PATTERN_LEN:
        return

    # Patrón preferido: el comercio (limpio, extraído por el LLM o corregido
    # por el usuario) como regla "contains" — generaliza entre cuotas,
    # sucursales y sufijos volátiles de la descripción. Solo es seguro si el
    # comercio normalizado aparece literalmente dentro de la descripción
    # normalizada (find_rule_for matchea contra la descripción). Si no,
    # fallback a regla exacta sobre la descripción completa.
    comercio = mov.get("comercio_final") or mov.get("comercio") or ""
    norm_com = normalize(comercio)
    if len(norm_com) >= _MIN_PATTERN_LEN and norm_com in norm_desc:
        match_type, pattern = "contains", norm_com
    else:
        match_type, pattern = "exact", norm_desc

    rule_id = db.upsert_rule(
        match_type=match_type, pattern=pattern, category=cat, subcategory=sub,
    )
    log.info(f"regla aprendida [{match_type}] {pattern!r} → {cat}/{sub} (id={rule_id})")
