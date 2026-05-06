from __future__ import annotations

import json
import os
from typing import NamedTuple

from . import db
from .utils import get_logger

log = get_logger("classifier")

VALID_CATEGORIES = [
    "Alimentación",
    "Transporte",
    "Vivienda",
    "Salud",
    "Entretenimiento",
    "Servicios",
    "Educación",
    "Empresa",
    "Transferencia",
    "Ingreso",
    "Otro",
]

HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_MAX_TOKENS = 200


class Classification(NamedTuple):
    category: str
    subcategory: str | None
    confidence: float
    source: str
    comercio: str | None = None


def classify(description: str, amount: float) -> Classification:
    rule = db.find_rule_for(description)
    if rule:
        db.bump_rule_hit(rule["id"])
        return Classification(
            category=rule["category"],
            subcategory=rule["subcategory"],
            confidence=1.0,
            source="rule",
        )

    return _classify_with_haiku(description, amount)


def _classify_with_haiku(description: str, amount: float) -> Classification:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY no está seteada. Cayendo a fallback.")
        return Classification("Otro", None, 0.0, "fallback")

    try:
        import anthropic
    except ImportError:
        log.error("Paquete anthropic no instalado.")
        return Classification("Otro", None, 0.0, "fallback")

    client = anthropic.Anthropic(api_key=api_key)
    categorias = ", ".join(VALID_CATEGORIES)
    prompt = (
        "Clasifica este movimiento bancario chileno.\n"
        f"Descripción: {description}\n"
        f"Monto (CLP, negativo = gasto): {amount}\n"
        f"Categorías válidas: {categorias}.\n"
        "Responde SOLO con JSON sin markdown:\n"
        '{"category":"<una de las válidas>",'
        '"subcategory":"<libre o null>",'
        '"confidence":<0.0-1.0>,'
        '"comercio":"<nombre legible del comercio, o null si es transferencia/genérico>"}'
    )

    try:
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=HAIKU_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        log.error(f"Haiku falló: {type(e).__name__}: {e}")
        return Classification("Otro", None, 0.0, "fallback")

    raw = "".join(getattr(b, "text", "") for b in msg.content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        log.warning(f"Haiku devolvió JSON inválido: {raw[:200]}")
        return Classification("Otro", None, 0.0, "fallback")

    cat = parsed.get("category") or "Otro"
    if cat not in VALID_CATEGORIES:
        cat = "Otro"
    sub_raw = parsed.get("subcategory")
    sub = sub_raw if (sub_raw and str(sub_raw).lower() != "null") else None
    conf_raw = parsed.get("confidence", 0.0)
    try:
        conf = float(conf_raw)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    com_raw = parsed.get("comercio")
    comercio = com_raw if (com_raw and str(com_raw).lower() != "null") else None

    return Classification(cat, sub, conf, "haiku", comercio)
