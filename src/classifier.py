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
    "Educación":                    ["Colegio", "Jardín infantil", "Educación Hijos", "Matrícula", "Útiles escolares", "Uniformes", "Transporte escolar", "Actividades escolares"],
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
    # Gastos hechos con tarjeta personal pero que se rinden a un tercero (empresa o familiar).
    # Las subcategorías son los nombres de los terceros conocidos. Si no encaja con ninguno,
    # el LLM puede inventar una nueva subcategoría — se acepta tal cual (no se fuerza a la lista).
    "Gastos por rendir":            ["Bodemall", "Faind", "Amplia", "Papá", "Mamá", "Hermano", "Hermana"],
    "Otros":                        ["Varios", "Gastos no clasificados", "Ajustes manuales"],
}

INCOME_CATEGORIES = {"Sueldo", "Honorarios", "Dividendos y utilidades", "Inversiones", "Arriendos", "Reembolsos", "Otros ingresos"}
INTERNAL_CATEGORIES = {"Transferencias internas"}
# Categorías cuyas subcategorías son extensibles: el LLM puede proponer una nueva
# y se acepta tal cual (en vez de forzarla a la primera de TAXONOMY[cat]).
EXTENSIBLE_CATEGORIES = {"Gastos por rendir"}

AGENT_MODEL = "claude-haiku-4-5-20251001"
AGENT_MAX_TOKENS = 1024

SYSTEM_PROMPT = """Eres un agente experto en clasificación de movimientos financieros personales y familiares.

Tu tarea es leer movimientos de ingresos y egresos de una familia compuesta por 2 adultos y 3 niños, y clasificarlos correctamente usando la taxonomía entregada.

Reglas principales:
1. Usa únicamente las categorías y subcategorías de la taxonomía cuando NO hay pista del usuario.
2. Si el movimiento es ambiguo, usa la categoría más probable con confianza baja.
3. Si no hay información suficiente, marca requiere_revision: true.
4. Si parece ser movimiento entre cuentas propias, clasifica como Transferencias internas.
5. Si es pago de tarjeta de crédito, clasifícalo como Transferencias internas / Pago tarjeta mismo titular.
6. Si es devolución de comercio, clasifícalo como Reembolsos / Devolución comercio.
7. Si es sueldo, honorarios, dividendos, intereses o arriendos recibidos, clasifícalo como Ingreso.
8. Si confianza < 0.75, entonces requiere_revision debe ser true.
9. No uses "Otros" si existe una categoría más específica.
10. Jardines infantiles y colegios privados de hijos clasifican como Educación / Educación Hijos. Por ejemplo "LEONCITO ESPAÑOL" es un jardín infantil de los hijos de Diego.

REGLAS CUANDO HAY PISTA DEL USUARIO (hint):
11. Si el usuario propone una categoría o subcategoría EXPLÍCITAMENTE (ej. «esto va a Bodemall», «ponlo como Pádel competencia»), úsala TAL CUAL la dijo, aunque NO exista en la taxonomía. NO la mapees automáticamente a una "parecida" — eso oculta la intención del usuario.
12. Si el usuario propone algo NUEVO que no está en la taxonomía:
    - devuelve `categoria` y `subcategoria` con los nombres EXACTOS propuestos por el usuario,
    - marca `requiere_revision: true`,
    - en `pregunta_sugerida` plantea explícitamente: «Sub-categoría nueva propuesta: "X" en "Y". ¿La uso así o prefieres mapear a [nombre cercano de la taxonomía]?» — para que el usuario pueda confirmar o redirigir.
13. Si el usuario es ambiguo (ej. «esto es del trabajo» sin más), elige la mejor opción de la taxonomía y marca requiere_revision con una pregunta clarificatoria.
14. EXCEPCIÓN PARA "Gastos por rendir": gastos hechos con tarjeta personal que se rinden a un tercero (empresa o familiar). La subcategoría es el NOMBRE del tercero. Lista predefinida: Bodemall, Faind, Amplia, Papá, Mamá, Hermano, Hermana. Si el usuario menciona otro nombre, úsalo tal cual. NO uses esta categoría sin un hint explícito del usuario."""


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


def classify_with_hint(description: str, amount: float, hint: str) -> Classification:
    """Re-clasifica un movimiento incorporando una pista en lenguaje natural del usuario.

    Útil para correcciones: el usuario explica qué tipo de gasto es ("es del super",
    "esto va a Bodemall", etc.) y el LLM lo interpreta usando la taxonomía.
    Salta la búsqueda de reglas y va directo al agente con el hint inyectado.
    """
    return _classify_with_agent(description, amount, hint=hint.strip() or None)


def _classify_with_agent(description: str, amount: float, hint: str | None = None) -> Classification:
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

    hint_block = f"\n\nPista del usuario (importante, úsala para decidir): {hint}" if hint else ""
    user_message = (
        f"Clasifica este movimiento bancario chileno.\n"
        f"Descripción: {description}\n"
        f"Monto CLP: {amount} ({flow}){hint_block}\n\n"
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

    conf = max(0.0, min(1.0, float(parsed.get("confianza") or 0.0)))
    requiere_revision = bool(parsed.get("requiere_revision", conf < 0.75))
    if conf < 0.75:
        requiere_revision = True

    pregunta = parsed.get("pregunta_sugerida")
    if pregunta and str(pregunta).lower() in {"null", "none", ""}:
        pregunta = None

    # Validación de cat/sub contra la taxonomía.
    # Si el usuario dio un hint, respetar lo que el LLM devolvió aunque sea una
    # cat/sub nueva (es probablemente intencional). Si no hubo hint, forzar a la
    # taxonomía conocida para no inventar.
    if cat not in TAXONOMY:
        if hint and cat:
            log.info(f"Categoría nueva propuesta vía hint: {cat!r}. Aceptando tal cual.")
            requiere_revision = True
            if not pregunta:
                pregunta = (
                    f"Categoría NUEVA propuesta: «{cat}». No está en la taxonomía actual. "
                    f"¿La quieres usar así o prefieres mapearla a una existente?"
                )
        else:
            cat = "Otros ingresos" if amount > 0 else "Otros"
            sub = "Ingresos extraordinarios" if amount > 0 else "Gastos no clasificados"
    elif sub not in TAXONOMY[cat]:
        if cat in EXTENSIBLE_CATEGORIES and sub:
            # categorías extensibles (ej. Gastos por rendir) aceptan subcat libre.
            log.info(f"Subcategoría libre aceptada para {cat}: {sub!r}")
        elif hint and sub:
            # El usuario dio una pista que indujo una subcat nueva. Aceptamos
            # tal cual y pedimos confirmación al usuario (sin mapear a 'parecida').
            log.info(f"Subcategoría NUEVA inducida por hint en {cat}: {sub!r}")
            requiere_revision = True
            if not pregunta:
                similares = ", ".join(TAXONOMY[cat][:5])
                pregunta = (
                    f"Sub-categoría NUEVA propuesta: «{sub}» dentro de «{cat}». "
                    f"¿La uso así o prefieres mapear a una existente como: {similares}?"
                )
        else:
            sub = TAXONOMY[cat][0]

    com_raw = parsed.get("comercio")
    comercio = com_raw if (com_raw and str(com_raw).lower() not in {"null", "none", ""}) else None

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
