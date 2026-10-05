"""Contexto del cliente para la conversacion: que le quedo pendiente y que productos tiene.

Se arma una vez al autenticar (sessions.verify) y vive en `session.slots["context"]`. Es DATO verificado del banco, no
texto del cliente. Solo contiene lo que el repositorio deja pasar (lista blanca): el agente confirma que algo existe, no
detalla saldos, montos ni descripciones.

La prioridad de un caso es una decision en codigo, no del LLM. Reglas (de mayor a menor peso):
  prioridad del reclamo (Critical > High > Medium > Low) > escalado > SLA incumplido > es reclamo (no interaccion);
  a igual peso se muestra primero el mas antiguo (lleva mas tiempo esperando).
"""
from __future__ import annotations

PRIORITY_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
HIGH_PRIORITY = ("High", "Critical")
NEGATIVE_SENTIMENT = ("Negativo", "Muy Negativo")


def case_weight(case: dict) -> int:
    return (PRIORITY_RANK.get(case.get("priority"), 0) * 10 + (5 if case.get("is_escalated") else 0)
            + (3 if case.get("sla_breached") else 0) + (2 if case.get("case_source") == "complaint" else 0))


def rank_cases(cases: list[dict]) -> list[dict]:
    """Mas relevante primero; a igual peso, el mas antiguo."""
    return sorted(cases, key=lambda c: (-case_weight(c), -(c.get("days_open") or 0)))


def build_context(cases: list[dict], products: list[dict]) -> dict:
    ranked = rank_cases(cases)
    complaints = [c for c in ranked if c["case_source"] == "complaint"]
    return {
        "open_cases": ranked,
        "most_relevant": ranked[0] if ranked else None,
        "counts": {
            "open": len(ranked),
            "complaints": len(complaints),
            "interactions": len(ranked) - len(complaints),
            "high_priority": sum(1 for c in ranked if c.get("priority") in HIGH_PRIORITY),
        },
        "flags": {
            "has_open_case": bool(ranked),
            "has_critical_open": any(c.get("priority") == "Critical" for c in ranked),
            "has_sla_breach": any(c.get("sla_breached") for c in ranked),
            "repeat_complainer": any(c.get("is_repeat_complainer") for c in ranked),
            "recent_negative_sentiment": any(c.get("sentiment") in NEGATIVE_SENTIMENT for c in ranked),
        },
        "products": [{"type": p["product_type"], "last4": p["last4"], "status": p["product_status"]} for p in products],
    }


EMPTY: dict = build_context([], [])
