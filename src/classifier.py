from __future__ import annotations

import json
import os
from typing import NamedTuple

from . import db
from .utils import get_logger

log = get_logger("classifier")

# Taxonomía completa: categoría → subcategorías válidas
TAXONOMY: dict[str, list[str]] = {
    # ── EGRESOS ──────────────────────────────────────────────────────────────
    "Supermercado":           ["Alimento", "Bebida", "Limpieza", "Higiene", "Mascotas", "General"],
    "Restaurante":            ["Almuerzo", "Cena", "Desayuno", "Café", "Delivery", "General"],
    "Transporte":             ["Combustible", "Taxi/Uber", "Estacionamiento", "Público", "Peaje", "General"],
    "Salud":                  ["Farmacia", "Médico", "Dental", "Examen", "Seguro", "General"],
    "Entretenimiento":        ["Streaming", "Cine/Teatro", "Deporte", "Viaje", "General"],
    "Educación":              ["Colegio", "Universidad", "Curso", "Material", "General"],
    "Vivienda":               ["Arriendo", "Dividendo", "Agua", "Luz", "Gas", "Internet", "Condominio", "General"],
    "Ropa":                   ["Ropa", "Calzado", "Accesorios"],
    "Tecnología":             ["Equipo", "Software", "Suscripción"],
    "Finanzas":               ["Cuota crédito", "Seguro", "Comisión banco", "Retiro/Ahorro"],
    "Empresa":                ["Proveedor", "Gasto operacional", "General"],
    "Transferencia enviada":  ["Personal", "Empresa", "General"],
    "Otro gasto":             ["General"],
    # ── INGRESOS ─────────────────────────────────────────────────────────────
    "Sueldo":                 ["Líquido", "Bono", "Aguinaldo"],
    "Arriendo cobrado":       ["General"],
    "Transferencia recibida": ["Personal", "Empresa", "General"],
    "Devolución":             ["Compra", "Impuesto", "General"],
    "Dividendo recibido":     ["General"],
    "Otro ingreso":           ["General"],
}

INCOME_CATEGORIES = {"Sueldo", "Arriendo cobrado", "Transferencia recibida", "Devolución", "Dividendo recibido", "Otro ingreso"}

HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_MAX_TOKENS = 300


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
        return Classification("Otro gasto", None, 0.0, "fallback")

    try:
        import anthropic
    except ImportError:
        log.error("Paquete anthropic no instalado.")
        return Classification("Otro gasto", None, 0.0, "fallback")

    client = anthropic.Anthropic(api_key=api_key)

    taxonomy_lines = "\n".join(
        f"  {cat}: {', '.join(subs)}" for cat, subs in TAXONOMY.items()
    )
    flow = "INGRESO" if amount > 0 else "GASTO/EGRESO"

    prompt = (
        "Clasifica este movimiento bancario chileno.\n"
        f"Descripción: {description}\n"
        f"Monto CLP: {amount} → tipo: {flow}\n\n"
        "Taxonomía (elige category y subcategory de esta lista):\n"
        f"{taxonomy_lines}\n\n"
        "Reglas:\n"
        "- Si el monto es negativo (gasto) elige una categoría de EGRESOS.\n"
        "- Si el monto es positivo (ingreso) elige una categoría de INGRESOS.\n"
        "- subcategory debe ser exactamente una de las listadas para esa categoría.\n"
        "- comercio: nombre legible del comercio (ej: 'Lider', 'Uber Eats'). "
        "  Null si es transferencia, pago de servicio genérico o no identificable.\n\n"
        "Responde SOLO con JSON sin markdown:\n"
        '{"category":"...","subcategory":"...","confidence":<0.0-1.0>,"comercio":"..."}'
    )

    try:
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=HAIKU_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        log.error(f"Haiku falló: {type(e).__name__}: {e}")
        return Classification("Otro gasto", None, 0.0, "fallback")

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
        return Classification("Otro gasto", None, 0.0, "fallback")

    cat = parsed.get("category") or ""
    if cat not in TAXONOMY:
        cat = "Otro ingreso" if amount > 0 else "Otro gasto"

    valid_subs = TAXONOMY[cat]
    sub_raw = parsed.get("subcategory") or ""
    sub = sub_raw if sub_raw in valid_subs else valid_subs[0]

    conf_raw = parsed.get("confidence", 0.0)
    try:
        conf = float(conf_raw)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    com_raw = parsed.get("comercio")
    comercio = com_raw if (com_raw and str(com_raw).lower() not in {"null", "none", ""}) else None

    return Classification(cat, sub, conf, "haiku", comercio)
