"""Oferta proactiva de credito: cuando y a quien se ofrece.

Dos caminos distintos que NO deben mezclarse:
- Reactivo: el cliente pide un credito -> se evalua su elegibilidad. NO mira el consentimiento de marketing.
- Proactivo (este modulo): se ofrece por iniciativa del banco. Exige TODAS estas condiciones:
    1. gold lo marca como `offer_mode = proactive`: elegible con datos del banco (no con lo declarado en el chat), acepta
       marketing y no tiene un reclamo critico abierto (politica 0.4, seccion 7);
    2. tiene una opcion disponible; se presenta la destacada (la mas alta): prestamo personal, si no tarjeta, si no hipoteca;
    3. el momento es adecuado: sin sentimiento negativo, sin tema delicado (fraude, disputa, queja) y sin
       rechazo previo en la sesion;
    4. no se ofrecio ya en esta sesion ni el cliente dijo que no;
    5. al cliente no le queda nada pendiente: ni un tema de soporte en esta conversacion (producto, incidente, caso), ni una
       derivacion, ni un caso critico abierto, ni un caso abierto reciente (agent/context.py). Antes se ofrecia credito justo
       despues de derivar un problema; ahora primero se atiende lo que el cliente trajo.
Es una decision en codigo: el LLM solo redacta el mensaje.

LIMITE: el tope de frecuencia es por sesion (en memoria). En produccion debe persistirse (ultima oferta por cliente).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.agent import templates
from app.agent.tools import ToolContext, get_offers, get_profile, to_local
from app.core.fmt import fmt_money, fmt_pct
from app.core.sessions import Session
from app.logging_setup import log
from app.policy import engine as eng

logger = logging.getLogger(__name__)

OK = "OK"
NO_CONSENT = "NO_CONSENT"
NEGATIVE_MOMENT = "NEGATIVE_MOMENT"
ALREADY_OFFERED = "ALREADY_OFFERED"
ALREADY_DECLINED = "ALREADY_DECLINED"
RECENT_DECLINE = "RECENT_DECLINE"
NOT_PREAPPROVED = "NOT_PREAPPROVED"
SUPPORT_TOPIC = "SUPPORT_TOPIC"
OPEN_CASE = "OPEN_CASE"


@dataclass
class OfferDecision:
    make: bool
    reason: str
    fmt: dict[str, str] = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)


def decide(session: Session, ctx: ToolContext) -> OfferDecision:
    slots = session.slots
    if slots.get("offer_declined"):
        return _no(ALREADY_DECLINED)
    if slots.get("offer_made"):
        return _no(ALREADY_OFFERED)
    if slots.get("no_offers"):
        return _no(NEGATIVE_MOMENT)
    if slots.get("case") or session.handoff:
        return _no(SUPPORT_TOPIC)
    last = slots.get("last_evaluation")
    if last and last["outcome"] in (eng.DECLINED, eng.NEEDS_DATA, eng.UNAVAILABLE):
        return _no(RECENT_DECLINE)
    if last and last["outcome"] in (eng.ELIGIBLE, eng.ELIGIBLE_PROVISIONAL):
        return _no("ALREADY_EVALUATED")      # ya tiene una propuesta: se le resume, no se le ofrece otra

    profile = get_profile(ctx)
    if profile.get("offer_mode") != "proactive":
        return _no(NO_CONSENT if profile.get("not_proactive_reason") == "no_marketing_consent" else
                   OPEN_CASE if profile.get("not_proactive_reason") == "open_critical_complaint" else NOT_PREAPPROVED)

    offers = get_offers(ctx, record=False)   # solo datos del banco: nunca lo declarado en el chat
    if not offers.ordered:
        return _no(NOT_PREAPPROVED)
    best = offers.ordered[0]
    product = eng.PRODUCT_NAME[best["product_code"]]
    get_offers(ctx)                           # la consulta que respalda la oferta queda en la evidencia del turno

    lang, ccy = session.language, profile["local_currency"]
    top = to_local(profile, best["offer_max_amount_usd"], floor=True)
    fmt = {"first_name": session.first_name or "", "max_amount": fmt_money(top, ccy, 0),
           "months": str(best["term_months"]), "rate": fmt_pct(best["offer_rate_pct"]),
           "product": templates.product_label(product, best["tier"], lang)}
    evidence = {"product": product, "option_code": best["option_code"], "term_months": best["term_months"],
                "indicative_max_amount": best["offer_max_amount_usd"], "indicative_max_amount_local": top, "ccy": ccy,
                "rate_pct": best["offer_rate_pct"], "band": profile.get("risk_band"),
                "policy_version": profile.get("policy_version"), "basis": "bank_data_only"}
    log(logger, "proactive_decision", reason=OK, make=True)
    return OfferDecision(True, OK, fmt, evidence)


def _no(reason: str) -> OfferDecision:
    log(logger, "proactive_decision", reason=reason, make=False)
    return OfferDecision(False, reason)
