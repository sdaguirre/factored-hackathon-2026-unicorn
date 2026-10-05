"""Temas que no son de credito: el chat no los atiende, los deriva a un asesor (con confirmacion).

El agente no consulta productos, saldos ni casos: solo anota lo que el cliente cuenta, como DECLARADO (sin verificar), para
que el asesor no tenga que volver a preguntarlo.
"""
from __future__ import annotations

import re

from app.agent.language import norm

# Motivos de derivacion que NO son de credito: al declinar la derivacion no se habla de "otro monto o plazo".
SUPPORT_REASONS = frozenset({"OTHER_TOPIC", "ACCOUNT_DETAIL", "INCIDENT", "CASE_FOLLOWUP", "UNCLEAR"})
MAX_NOTES = 8
NOTE_CHARS = 300

# ---------------------------------------------------------------- familia de producto mencionada (para el asesor)
_FAMILY_RX = [("mortgage", r"hipotec|imobili"), ("loan", r"prestamo|emprestimo"), ("card", r"tarjeta|cartao|cartoes"),
              ("account", r"cuenta|\bcontas?\b|ahorro|poupanca|corriente"), ("investment", r"inversion|investimento"),
              ("insurance", r"\bseguro")]


def mentioned_family(text: str) -> tuple[str | None, str | None]:
    """(familia de producto, tipo exacto si el cliente lo precisa) que menciona el mensaje. Ej.: 'mi tarjeta de débito'."""
    t = norm(text)
    family = next((f for f, rx in _FAMILY_RX if re.search(rx, t)), None)
    hint = None
    if family == "card":
        hint = "Tarjeta Débito" if "debito" in t else "Tarjeta Crédito" if "credit" in t else None
    elif family == "account":
        hint = "Cuenta Ahorro" if re.search(r"ahorro|poupanca", t) else "Cuenta Corriente" if "corrente" in t or "corriente" in t else None
    return family, hint


# ---------------------------------------------------------------- notas del cliente
_LONG_NUMBER = re.compile(r"(?:\d[ -]?){9,19}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact(text: str, limit: int | None = NOTE_CHARS) -> str:
    """Quita numeros largos (cuentas, tarjetas, documentos) y correos antes de guardar lo que el cliente escribio."""
    clean = _EMAIL.sub("[correo omitido]", _LONG_NUMBER.sub("[número omitido]", text)).strip()
    return clean if limit is None else clean[:limit]


def add_note(slots: dict, topic: str, message: str, family: str | None = None) -> dict:
    """Registra el tema y lo que el cliente cuenta, como DECLARADO. Devuelve el caso en curso (para el resumen del asesor)."""
    case = slots.setdefault("case", {"topic": topic, "topics": [], "notes": [], "families": []})
    case["topic"] = topic
    if topic not in case["topics"]:
        case["topics"].append(topic)
    if family and family not in case["families"]:
        case["families"].append(family)
    text = redact(message)
    if len(text) > 3 and len(case["notes"]) < MAX_NOTES:
        # Solo el texto (anonimizado). No se extraen "montos": parse_amounts lee "12 de mayo" como 12 y un numero largo como
        # monto, y un dato equivocado engana mas que ayuda al asesor.
        case["notes"].append({"text": text, "declared_by_customer": True, "verified": False})
    return case
