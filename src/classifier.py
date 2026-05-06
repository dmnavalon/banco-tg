from __future__ import annotations

import os
from typing import NamedTuple

from . import db
from .utils import get_logger

log = get_logger("classifier")

# ── Taxonomía autorizada ──────────────────────────────────────────────────────

TAXONOMY: dict[str, list[str]] = {
    # INGRESOS
    "Sueldo":                       ["Sueldo principal", "Sueldo secundario"],
    "Honorarios":                   ["Boletas de honorarios"],
    "Dividendos y utilidades":      ["Dividendos empresas", "Retiros de empresa"],
    "Inversiones":                  ["Intereses", "Dividendos financieros", "Venta de activos"],
    "Arriendos":                    ["Ingreso por arriendo"],
    "Reembolsos":                   ["Reembolso empresa", "Devolución comercio", "Seguro reembolsado"],
    "Otros ingresos":               ["Regalos recibidos", "Ingresos extraordinarios"],
    # EGRESOS
    "Hogar y alimentación":         ["Supermercado", "Alimentos", "Panadería", "Carnicería", "Verduras y frutas", "Delivery", "Restaurantes", "Cafeterías"],
    "Vivienda":                     ["Arriendo o dividendo", "Contribuciones", "Gastos comunes", "Mantención casa", "Jardín y piscina", "Muebles y decoración"],
    "Servicios básicos":            ["Luz", "Agua", "Gas", "Internet", "Telefonía móvil", "Streaming", "Alarmas y seguridad"],
    "Educación":                    ["Colegio", "Jardín infantil", "Matrícula", "Útiles escolares", "Uniformes", "Transporte escolar", "Actividades escolares"],
    "Niños":                        ["Actividades extracurriculares", "Juguetes", "Cumpleaños", "Ropa niños", "Salud niños", "Deportes niños"],
    "Salud y seguros":              ["Farmacia", "Consultas médicas", "Dentista", "Exámenes médicos", "Seguro de salud", "Seguro de vida", "Terapias"],
    "Transporte":                   ["Combustible", "Tag y peajes", "Estacionamientos", "Mantención auto", "Seguro auto", "Permiso de circulación", "Uber o taxi", "Transporte público"],
    "Deporte y bienestar":          ["Gimnasio", "Pádel", "Club deportivo", "Ropa deportiva", "Implementos deportivos", "Masajes"],
    "Vestuario y cuidado personal": ["Ropa adultos", "Zapatos", "Peluquería", "Estética", "Perfumería y cuidado personal"],
    "Entretención y vida social":   ["Salidas familiares", "Cine y espectáculos", "Regalos", "Cumpleaños y eventos", "Vacaciones"],
    "Tecnología":                   ["Software y suscripciones", "Hardware", "Celulares", "Apps", "Soporte técnico"],
    "Servicios domésticos":         ["Nana", "Imposiciones nana", "Aseo", "Reparaciones menores"],
    "Mascotas":                     ["Alimento mascotas", "Veterinario", "Accesorios mascotas"],
    "Finanzas e impuestos":         ["Pago tarjeta de crédito", "Intereses y comisiones", "Impuestos", "Contador", "Seguros financieros"],
    "Ahorro e inversión":           ["Ahorro mensual", "Inversión financiera", "Fondo emergencia", "APV"],
    "Transferencias internas":      ["Movimiento entre cuentas", "Pago tarjeta mismo titular", "Traspaso a inversión"],
    "Otros":                        ["Varios", "Gastos no clasificados", "Ajustes manuales"],
}

INCOME_CATEGORIES = {"Sueldo", "Honorarios", "Dividendos y utilidades", "Inversiones", "Arriendos", "Reembolsos", "Otros ingresos"}
INTERNAL_CATEGORIES = {"Transferencias internas"}

AGENT_MODEL = "claude-haiku-4-5-20251001"
AGENT_MAX_TOKENS = 1024

SYSTEM_PROMPT = """Eres un agente experto en clasificación de movimientos financieros personales y familiares.

Tu tarea es leer movimientos de ingresos y egresos de una familia compuesta por 2 adultos y 3 niños, y clasificarlos correctamente usando la taxonomía entregada.

Reglas principales:
1. Usa únicamente las categorías y subcategorías de la taxonomía.
2. Si el movimiento es ambiguo, usa la categoría más probable con confianza baja.
3. Si no hay información suficiente, marca requiere_revision: true.
4. Si parece ser movimiento entre cuentas propias, clasifica como Transferencias internas.
5. Si es pago de tarjeta de crédito, clasifícalo como Transferencias internas / Pago tarjeta mismo titular.
6. Si es devolución de comercio, clasifícalo como Reembolsos / Devolución comercio.
7. Si es sueldo, honorarios, dividendos, intereses o arriendos recibidos, clasifícalo como Ingreso.
8. Si confianza < 0.75, entonces requiere_revision debe ser true.
9. No uses "Otros" si existe una categoría más específica.
10. No inventes categorías. No inventes subcategorías. No asumas información no disponible."""


class Classification(NamedTuple):
    category: str
    subcategory: str | None
    confidence: float
    source: str
    comercio: str | None = None
    tipo: str = "Egreso"
    requiere_revision: bool = False
    pregunta_sugerida: str | None = None


def _fallback(amount: float) -> Classification:
    cat = "Otros ingresos" if amount > 0 else "Otros"
    sub = "Ingresos extraordinarios" if amount > 0 else "Gastos no clasificados"
    tipo = "Ingreso" if amount > 0 else "Egreso"
    return Classification(cat, sub, 0.0, "fallback", tipo=tipo, requiere_revision=True)


def classify(description: str, amount: float) -> Classification:
    rule = db.find_rule_for(description)
    if rule:
        db.bump_rule_hit(rule["id"])
        tipo = "Ingreso" if amount > 0 else "Egreso"
        cat = rule["category"]
        if cat in INTERNAL_CATEGORIES:
            tipo = "Transferencia interna"
        return Classification(
            category=cat,
            subcategory=rule["subcategory"],
            confidence=1.0,
            source="rule",
            tipo=tipo,
        )
    return _classify_with_agent(description, amount)


def _classify_with_agent(description: str, amount: float) -> Classification:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY no está seteada. Cayendo a fallback.")
        return _fallback(amount)

    try:
        import anthropic
    except ImportError:
        log.error("Paquete anthropic no instalado.")
        return _fallback(amount)

    client = anthropic.Anthropic(api_key=api_key)

    taxonomy_lines = "\n".join(f"- {cat}: {', '.join(subs)}" for cat, subs in TAXONOMY.items())
    flow = "positivo (ingreso)" if amount > 0 else "negativo (gasto/egreso)"

    tool_def = {
        "name": "clasificar_movimiento",
        "description": "Clasifica un movimiento bancario en tipo, categoría, subcategoría y metadata de revisión.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["tipo", "categoria", "subcategoria", "confianza", "requiere_revision",
                         "explicacion_breve", "pregunta_sugerida", "regla_aplicada", "comercio"],
            "properties": {
                "tipo":             {"type": "string", "enum": ["Ingreso", "Egreso", "Transferencia interna"]},
                "categoria":        {"type": "string"},
                "subcategoria":     {"type": "string"},
                "confianza":        {"type": "number", "minimum": 0, "maximum": 1},
                "requiere_revision":{"type": "boolean"},
                "explicacion_breve":{"type": "string", "maxLength": 160},
                "pregunta_sugerida":{"type": ["string", "null"]},
                "regla_aplicada":   {"type": "string", "maxLength": 120},
                "comercio":         {"type": ["string", "null"],
                                     "description": "Nombre legible del comercio. Null si es transferencia, servicio genérico o no identificable."},
            },
        },
    }

    user_message = (
        f"Clasifica este movimiento bancario chileno.\n"
        f"Descripción: {description}\n"
        f"Monto CLP: {amount} ({flow})\n\n"
        f"Taxonomía autorizada:\n{taxonomy_lines}"
    )

    try:
        msg = client.messages.create(
            model=AGENT_MODEL,
            max_tokens=AGENT_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[tool_def],
            tool_choice={"type": "tool", "name": "clasificar_movimiento"},
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as e:
        log.error(f"Agente clasificador falló: {type(e).__name__}: {e}")
        return _fallback(amount)

    parsed = None
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "clasificar_movimiento":
            parsed = block.input
            break

    if parsed is None:
        log.warning("Agente no devolvió tool_use. Fallback.")
        return _fallback(amount)

    cat = parsed.get("categoria") or ""
    sub = parsed.get("subcategoria") or ""
    tipo = parsed.get("tipo") or ("Ingreso" if amount > 0 else "Egreso")

    if cat not in TAXONOMY:
        cat = "Otros ingresos" if amount > 0 else "Otros"
        sub = "Ingresos extraordinarios" if amount > 0 else "Gastos no clasificados"
    elif sub not in TAXONOMY[cat]:
        sub = TAXONOMY[cat][0]

    conf = max(0.0, min(1.0, float(parsed.get("confianza") or 0.0)))
    requiere_revision = bool(parsed.get("requiere_revision", conf < 0.75))
    if conf < 0.75:
        requiere_revision = True

    com_raw = parsed.get("comercio")
    comercio = com_raw if (com_raw and str(com_raw).lower() not in {"null", "none", ""}) else None

    pregunta = parsed.get("pregunta_sugerida")
    if pregunta and str(pregunta).lower() in {"null", "none", ""}:
        pregunta = None

    return Classification(
        category=cat,
        subcategory=sub,
        confidence=conf,
        source="agent",
        comercio=comercio,
        tipo=tipo,
        requiere_revision=requiere_revision,
        pregunta_sugerida=pregunta,
    )
