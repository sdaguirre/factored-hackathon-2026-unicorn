"""Lo que el agente sabe del cliente fuera de la oferta: SOLO la fila de gold customer_credit_profile.

El chat atiende ofertas de credito; cualquier otro tema (productos, saldos, reclamos, casos) lo ve un asesor. Por eso el agente
no lee casos, productos ni movimientos crudos: del perfil de gold toma los conteos de reclamos abiertos (que ya deciden la
oferta proactiva en offer_mode y la marca F04) y los conteos de productos de credito, para el resumen del asesor.
Se arma una vez al autenticar (sessions.verify) y vive en `session.slots["context"]`.
"""
from __future__ import annotations


def _count(profile: dict, key: str) -> int:
    value = profile.get(key)
    return int(value) if value is not None else 0


def gold_context(profile: dict) -> dict:
    open_complaints = _count(profile, "open_complaints")
    priority = _count(profile, "open_priority_complaints")
    critical = _count(profile, "open_critical_complaints")
    return {
        "source": "gold.customer_credit_profile",
        "open_cases": [],                       # sin detalle de casos: lo revisa el asesor
        "counts": {"open": open_complaints, "complaints": open_complaints, "interactions": 0, "high_priority": priority},
        "flags": {"has_open_case": open_complaints > 0, "has_critical_open": critical > 0},
        "credit": {
            "offer_mode": profile.get("offer_mode"),
            "requires_advisor_review": bool(profile.get("requires_advisor_review")),
            "reason_codes": list(profile.get("reason_codes") or []),
            "open_complaints": open_complaints, "open_priority_complaints": priority, "open_critical_complaints": critical,
            "active_credit_cards": _count(profile, "active_credit_cards"),
            "active_personal_loans": _count(profile, "active_personal_loans"),
            "active_mortgages": _count(profile, "active_mortgages"),
        },
        "products": [],
    }
