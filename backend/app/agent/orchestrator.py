"""Orquestador: maquina de estados del chat de credito.

Flujo por turno:  mensaje -> NLU (LLM o reglas) -> validacion en codigo -> accion/herramienta/politica
                  -> hechos verificados -> texto (plantilla es/pt, opcionalmente reescrito por el LLM).
El LLM nunca decide: la elegibilidad sale de la politica 0.4 (app.policy.engine, la misma de gold) y las acciones, de
tools.py. Montos: la politica trabaja en USD; el cliente ve y escribe montos en su moneda local.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field

from app.agent import documents, money, proactive, support, templates
from app.agent.evidence import build_evidence
from app.agent.handoff import build_summary
from app.agent.language import norm, parse_amounts, parse_months
from app.agent.llm import LLM, MockLLM
from app.agent.nlu import HUMAN_REQUEST, NLUResult
from app.agent.tools import (HandoffQueue, ToolContext, accept_offer, get_offers, get_profile,
                             new_ticket_id, recalculate_offer, to_local, utc_iso)
from app.core.fmt import fmt_money, fmt_pct
from app.core.offers import OfferStore
from app.core.pdf import render_summary_pdf
from app.core.sessions import Session
from app.logging_setup import log
from app.policy import engine as eng

logger = logging.getLogger(__name__)
MAX_REPLY_CHARS = 1200
# Solo estos mensajes pueden ser reescritos por el LLM. Decisiones de credito, ofertas, derivaciones y avisos legales
# salen siempre de la plantilla revisada: el guardia de numeros no detecta frases nuevas que cambien el compromiso.
DEFAULT_REWRITE_KINDS = frozenset({"thanks", "closing", "goodbye", "unknown", "ask_amount", "ask_income",
                                   "handoff_declined", "offer_declined", "offer_accepted",
                                   # soporte: tono, sin decisiones ni datos de la cuenta (los mensajes de productos y de casos
                                   # llevan hechos del banco y salen siempre de la plantilla)
                                   "other_topic", "incident", "detail_noted", "handoff_declined_support", "case_skip"})
# Casos de soporte en curso: el cliente esta contando algo y la derivacion espera su confirmacion
EMPATHY_EVERY_N_TURNS = 3
CONCLUSIVE_OK = (eng.ELIGIBLE, eng.ELIGIBLE_PROVISIONAL)
# "no paga ninguna" a la pregunta de las cuotas de la persona del hogar = 0
_INSTALLMENTS = re.compile(r"\b(cuotas?|paga|pago|debe|deudas?|parcelas?|deve|dividas?)\b")
_NO_DEBT = re.compile(r"\b(ningun[ao]?|nada|nenhum[a]?|zero|cero|no paga|nao paga|sin cuotas|sem parcelas|"
                      r"no tiene cuotas|nao tem parcelas)\b")
# Otra persona del hogar en la respuesta ("mi esposa gana..."). Sin ella, "gano X" es el ingreso del propio cliente.
_THIRD_PARTY = re.compile(r"\b(espos[oa]|marido|mujer|pareja|novi[oa]|companheir[oa]|conyuge|hij[oa]|filh[oa]|herman[oa]|"
                          r"irma[o]?|madre|padre|mae|pai|mama|papa|suegr[oa]|sogr[oa]|ella|ele|essa pessoa|esa persona|"
                          r"gana|ganha)\b")


@dataclass
class ChatReply:
    reply: str
    language: str
    intent: str
    outcome: str | None = None
    handoff_ticket: str | None = None
    suggested_replies: list[str] = field(default_factory=list)
    awaiting: str | None = None
    llm_rewritten: bool = False
    proactive_offer: bool = False
    summary_ready: bool = False          # la respuesta incluye el resumen final de la propuesta
    email: dict | None = None            # correo con el PDF (SIMULADO en el prototipo): {to, status, id}
    evidence: dict | None = None         # lo que el agente hizo de verdad en el turno (agent/evidence.py); None si no uso herramientas
    disclaimer: str | None = None        # "simulation" (la respuesta presenta una oferta) | "final" (resumen de la oferta final)


def _conv_dict(c: money.Conversion) -> dict:
    from datetime import date as _date

    y, m, d = (c.quote.as_of or "1970-01-01").split("-")
    return {"amount_src": c.amount_src, "src": c.src, "amount_dst": c.amount_dst, "dst": c.dst, "rate": c.quote.rate,
            "as_of": c.quote.as_of, "as_of_fmt": f"{d}/{m}/{y}", "rate_text": c.rate_text}


def _digit_runs(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


# El asistente nunca se hace pasar por una persona: un texto que lo afirme se descarta siempre.
_CLAIMS_HUMAN = re.compile(
    r"\b(soy|sou)\s+(una?\s+|um\s+|uma\s+)?(persona|pessoa|humano|humana|agente humano|ejecutiv[oa]|asesor[a]?|consultor[a]?)\b"
    r"|\bno soy (un |una )?(robot|bot|ia|inteligencia artificial)\b|\bn[aã]o sou (um |uma )?(rob[oô]|bot|ia|intelig[eê]ncia artificial)\b",
    re.IGNORECASE)


# Promesas que el asistente no puede cumplir (resultado, plazo, reembolso): un texto que las contenga se descarta siempre.
_PROMISES = re.compile(
    r"\b(garantiz\w*|garant[oi]\w*|prometo|prometemos|le aseguro|aseguro que|reembols\w*|devolver[ea]mos|devolveremos|"
    r"devolvemos|compensar[ea]mos|resolver[ea]mos|solucionar[ea]mos|resolveremos|vamos a resolver|vamos a solucionar|"
    r"vamos resolver|sera (resuelto|solucionado|resolvido)|se resolvera|se solucionara|se devolvera|lo antes posible|"
    r"cuanto antes lo resuel\w*)\b")


def safe_text(candidate: str, facts: dict) -> bool:
    """Acepta el texto del LLM solo si no introduce numeros ajenos a los hechos, no se hace pasar por una persona, no hace
    promesas y tiene tamano razonable. Si el mensaje espera una respuesta (p. ej. '¿lo conecto?'), debe seguir preguntandolo."""
    allowed = _digit_runs(" ".join(facts.get("fmt", {}).values()))
    return (0 < len(candidate) <= MAX_REPLY_CHARS and _digit_runs(candidate) <= allowed
            and not _CLAIMS_HUMAN.search(candidate) and not _PROMISES.search(norm(candidate))
            and (not facts.get("awaiting") or "?" in candidate))


def validate_nlu(nlu: NLUResult, message: str) -> NLUResult:
    """Contrasta en codigo lo que dijo el LLM con el texto original: montos y plazos deben aparecer en el mensaje."""
    amounts, months = parse_amounts(message), parse_months(message)
    upd: dict = {}
    for key in ("amount", "declared_income"):
        v = getattr(nlu, key)
        if v is not None and not any(abs(v - a) <= 1e-6 * max(a, 1) for a in amounts):
            upd[key] = None
    if nlu.months is not None and nlu.months != months:
        upd["months"] = None
    # Una derivacion a humano es una accion: solo se acepta si el cliente la pidio de forma explicita en su texto.
    if nlu.intent == "request_human" and not HUMAN_REQUEST.search(norm(message)):
        upd["intent"] = "other_topic" if nlu.sensitive_topic else "unknown"
    return nlu.model_copy(update=upd) if upd else nlu



# Respuestas que presentan cifras de una oferta: la interfaz las encabeza con el aviso de simulacion.
OFFER_KINDS = frozenset({"offers", "offers_declared", "offer_featured", "offer_featured_card", "offer_proactive", "offer_proactive_card",
                         "eligible", "eligible_card", "eligible_provisional", "eligible_card_provisional"})


def disclaimer_for(facts: dict) -> str | None:
    if facts.get("summary"):
        return "final"
    if facts.get("proactive_offer") or facts["kind"] in OFFER_KINDS or facts.get("kind2") in OFFER_KINDS:
        return "simulation"
    return None


def fmt_dti(share: float) -> str:
    """Debt-to-income truncated to one decimal: an offer at the limit (19.99%) must not read as 20.0%."""
    import math

    return fmt_pct(math.floor(share * 1000) / 10)

class Orchestrator:
    def __init__(self, repo, policy: eng.Policy, rules: dict, llm: LLM, queue: HandoffQueue,
                 rewrite_kinds: frozenset[str] = DEFAULT_REWRITE_KINDS, outbox=None, offers: OfferStore | None = None):
        self.repo, self.policy, self.rules, self.llm, self.queue, self.outbox = repo, policy, rules, llm, queue, outbox
        self.offers = offers
        self.rewrite_kinds = rewrite_kinds
        self._fallback = MockLLM()

    # ------------------------------------------------------------------ entrada
    def handle(self, session: Session, message: str) -> ChatReply:
        nlu = self._understand(session, message)
        if nlu.language:
            session.language = nlu.language
        lang = session.language
        session.history.append({"role": "user", "text": message})
        ctx = ToolContext(session.customer_id, self.repo, self.policy, self.offers)
        if nlu.sentiment == "negative" or nlu.sensitive_topic:
            session.slots["no_offers"] = True  # en toda la sesion: ninguna oferta comercial
        session.slots.setdefault("sentiments", []).append(nlu.sentiment or "neutral")   # curva de animo para el resumen del asesor

        facts = self._dispatch(session, ctx, nlu, message)
        facts["variant"] = len(session.history) // 2        # rota las formulaciones de los mensajes de bajo riesgo
        self._empathy(session, nlu, facts)
        reply = self._render(facts, lang)
        reply.evidence = build_evidence(ctx.trace, facts)
        session.history.append({"role": "assistant", "text": reply.reply})
        log(logger, "turn", intent=nlu.intent, kind=facts["kind"], outcome=facts.get("outcome"),
            awaiting=facts.get("awaiting"), proactive=reply.proactive_offer, llm=self.llm.name,
            llm_rewritten=reply.llm_rewritten, lang=lang)
        return reply

    @staticmethod
    def _empathy(session: Session, nlu: NLUResult, facts: dict) -> None:
        """Si el cliente se muestra molesto, el mensaje abre reconociendolo (una frase fija revisada). No se repite en cada
        turno: seria artificial. Los incidentes ya traen su propio reconocimiento."""
        turn = len(session.history) // 2
        if (nlu.sentiment == "negative" and facts["kind"] not in ("incident", "handoff_created", "application_ready")
                and turn - session.slots.get("empathy_turn", -EMPATHY_EVERY_N_TURNS) >= EMPATHY_EVERY_N_TURNS):
            facts["pre"] = ["empathy_negative"]
            session.slots["empathy_turn"] = turn

    def _understand(self, session: Session, message: str) -> NLUResult:
        pending = session.slots.get("awaiting") in ("confirm_handoff", "offer_interest", "household")  # pregunta de si/no
        try:
            nlu = self.llm.extract(message, session.language, pending)
        except Exception as exc:  # fallo de red, JSON invalido, etc.: respaldo por reglas
            log(logger, "nlu_fallback", error=type(exc).__name__)
            nlu = self._fallback.extract(message, session.language, pending)
        return validate_nlu(nlu, message)

    # ------------------------------------------------------------------ despacho
    def _dispatch(self, session: Session, ctx: ToolContext, nlu: NLUResult, message: str) -> dict:
        awaiting = session.slots.pop("awaiting", None)
        amounts = parse_amounts(message)
        intent = nlu.intent
        mention = money.detect_currency(message)
        if mention.unsupported and amounts and (awaiting in ("amount", "income") or intent in (
                "credit_eligibility", "update_income", "unknown")):
            if awaiting:
                session.slots["awaiting"] = awaiting  # sigue esperando el dato, ahora en una moneda soportada
            return self._facts("currency_unsupported", intent, awaiting=awaiting, ccy=mention.unsupported,
                               supported=", ".join(money.SUPPORTED))

        if awaiting == "confirm_handoff":
            # Ya no se encadena una oferta de credito tras atender otro tema: el cliente vino por otra cosa.
            if intent == "confirm_yes":
                return self._handoff(session, session.slots.pop("handoff_reason", "USER_REQUEST"))
            if intent == "confirm_no":
                reason = session.slots.pop("handoff_reason", "USER_REQUEST")
                return self._facts("handoff_declined_support" if reason in support.SUPPORT_REASONS else "handoff_declined", intent)
            # Cuenta mas detalles en vez de decir si/no: se anotan (un tema generico o ininteligible no cambia el motivo).
            if session.slots.get("case") and (intent == "unknown" or (intent == "other_topic" and not nlu.sensitive_topic)):
                return self._collect_detail(session, message)
        if awaiting == "offer_interest":
            if intent == "confirm_yes":
                return self._proactive_accepted(session, ctx, intent)
            if intent == "confirm_no":
                session.slots["offer_declined"] = True
                return self._facts("offer_declined", intent)
        if awaiting in ("household", "household_income", "household_debt"):
            reply = self._household_answer(session, ctx, awaiting, intent, message, amounts, mention)
            if reply is not None:
                return reply
        if awaiting == "term":
            months = parse_months(message) or nlu.months
            if months is None and amounts and amounts[0] == int(amounts[0]) and amounts[0] <= 360:
                months = int(amounts[0])                         # "48" a secas, en respuesta a "¿qué plazo prefiere?"
            if months is not None and intent in ("unknown", "credit_eligibility", "confirm_yes"):
                session.slots["pending_request"]["months"] = months
                return self._evaluate(session, ctx, "credit_eligibility")
        if awaiting == "proceed":
            if intent == "confirm_yes":
                return self._start_application(session, ctx)
            if intent == "confirm_no":
                session.slots["proceed_declined"] = True
                return self._facts("proceed_declined", intent)
        if awaiting in ("docs_all", "doc_item"):
            reply = self._docs_answer(session, ctx, awaiting, intent)
            if reply is not None:
                return reply
        if awaiting == "amount" and amounts and intent in ("unknown", "credit_eligibility", "confirm_yes", "credit_offers"):
            nlu = nlu.model_copy(update={"intent": "credit_eligibility", "amount": amounts[0]})
            intent = "credit_eligibility"
        elif awaiting == "amount" and intent == "confirm_yes" and session.slots.get("pending_request", {}).get("featured_amount"):
            req = session.slots["pending_request"]                # "si" al maximo propuesto
            req.update(amount=req["featured_amount"], conv=None)
            return self._evaluate(session, ctx, "credit_eligibility")
        if awaiting == "income" and amounts and intent in ("unknown", "update_income"):
            nlu = nlu.model_copy(update={"intent": "update_income", "declared_income": amounts[0]})
            intent = "update_income"
        elif awaiting == "income" and intent in ("unknown", "confirm_yes") and session.slots.get("reasked") != "income":
            session.slots.update(reasked="income", awaiting="income")      # una sola vez: despues sigue el flujo general
            facts = self._facts("ask_income", intent, awaiting="income", ccy=get_profile(ctx)["local_currency"])
            facts["pre"] = ["reask_number"]
            return facts

        pending = session.slots.get("pending_request") or {}
        months_only = parse_months(message) or nlu.months
        if intent == "unknown" and months_only and pending.get("product"):
            pending.update(months=months_only, amount=None, conv=None)    # otro plazo: la oferta mas alta a ese plazo
            session.slots["pending_request"] = pending
            return self._evaluate(session, ctx, "credit_eligibility")
        # 2. a "no" with no question pending (after seeing offers, for example): acknowledge, do not say "I did not understand"
        if intent == "confirm_no" and not awaiting:
            return self._facts("no_thanks", intent, suggest="start")

        session.unknown_streak = session.unknown_streak + 1 if intent == "unknown" else 0

        if intent == "ask_identity":
            return self._facts("identity", intent, suggest="start")      # respuesta honesta: es un asistente virtual, no una persona
        if intent == "request_human":
            return self._handoff(session, "USER_REQUEST")
        if intent == "greeting":
            return self._facts("greeting", intent, first_name=session.first_name or "", suggest="start")
        if intent in ("thanks", "closing"):
            return self._close_turn(session, ctx, intent)
        if intent == "case_status":
            return self._support_case_status(session, ctx, intent, message)
        if nlu.sensitive_topic and intent in ("other_topic", "account_inquiry"):
            return self._support_incident(session, intent, message)
        if intent == "account_inquiry":
            return self._support_account(session, ctx, intent, message)
        if intent == "other_topic":
            return self._support_other(session, intent, message)
        if intent == "credit_offers":
            return self._offers(session, ctx)
        if intent == "credit_eligibility":
            return self._eligibility(session, ctx, nlu, mention)
        if intent == "update_income":
            return self._income(session, ctx, nlu, mention)
        # confirm_yes/no sin nada pendiente, o unknown
        if session.unknown_streak >= 2:
            return self._offer_handoff(session, "UNCLEAR", intent)
        return self._facts("unknown", intent, suggest="start")

    # ------------------------------------------------------------------ oferta proactiva
    def _with_offer(self, session: Session, ctx: ToolContext, facts: dict) -> dict:
        """Encadena la oferta proactiva al mensaje si la politica lo permite (consentimiento, preaprobacion, momento)."""
        d = proactive.decide(session, ctx)
        if not d.make:
            return facts
        session.slots["offer_made"] = True
        session.slots["awaiting"] = "offer_interest"
        session.actions.append({"type": "proactive_offer_made", "verified": True, **d.evidence})
        session.slots.setdefault("verified_facts", []).append({"type": "proactive_offer", **d.evidence})
        session.slots["offer_product"] = d.evidence["product"]
        facts["kind2"] = "offer_proactive_card" if d.evidence["product"] == "credit_card" else "offer_proactive"
        facts["fmt"].update(d.fmt)
        facts.update(awaiting="offer_interest", suggest="yes_no", proactive_offer=True)
        return facts

    # ------------------------------------------------------------------ soporte (productos, casos, incidentes, otros temas)
    # Regla comun: el agente responde lo que puede con datos verificados del banco (existencia de productos, casos abiertos),
    # anota lo que el cliente cuenta como DECLARADO y solo ofrece conectar con un asesor; la derivacion se ejecuta con un "si".
    def _support(self, session: Session, topic: str, reason: str, kind: str, intent: str, message: str,
                 family: str | None = None, **fmt: str) -> dict:
        support.add_note(session.slots, topic, message, family)
        session.slots["awaiting"] = "confirm_handoff"
        session.slots["handoff_reason"] = reason
        return self._facts(kind, intent, awaiting="confirm_handoff", suggest="yes_no", **fmt)

    def _support_account(self, session: Session, ctx: ToolContext, intent: str, message: str) -> dict:
        """Productos, saldos, movimientos: no es credito. Se anota lo que pide y se ofrece un asesor (sin consultar datos)."""
        family, _ = support.mentioned_family(message)
        return self._support(session, "account_inquiry", "ACCOUNT_DETAIL", "non_credit", intent, message, family)

    def _support_case_status(self, session: Session, ctx: ToolContext, intent: str, message: str) -> dict:
        """Estado de un reclamo o caso: lo ve un asesor. Se anota lo que pregunta y se ofrece conectarlo."""
        return self._support(session, "case_status", "CASE_FOLLOWUP", "non_credit", intent, message)

    def _support_incident(self, session: Session, intent: str, message: str) -> dict:
        """Fraude, cargo no reconocido, robo, reclamo nuevo: se reconoce, se pide lo basico para el asesor y se ofrece conectar."""
        family, _ = support.mentioned_family(message)
        return self._support(session, "incident", "INCIDENT", "incident", intent, message, family)

    def _support_other(self, session: Session, intent: str, message: str) -> dict:
        """Tema que el asistente no resuelve (horarios, claves, app...): se anota lo que cuenta y se ofrece conectar."""
        return self._support(session, "other", "OTHER_TOPIC", "other_topic", intent, message)

    def _collect_detail(self, session: Session, message: str) -> dict:
        """El cliente conto mas detalles en lugar de responder si/no: se anotan y se vuelve a preguntar por la derivacion."""
        session.unknown_streak = 0
        case = support.add_note(session.slots, session.slots["case"]["topic"], message)
        session.slots["awaiting"] = "confirm_handoff"        # la razon de la derivacion sigue en slots["handoff_reason"]
        session.actions.append({"type": "customer_detail_noted", "notes": len(case["notes"]), "verified": False})
        return self._facts("detail_noted", "unknown", awaiting="confirm_handoff", suggest="yes_no")

    # ------------------------------------------------------------------ credito (politica 0.4)
    def _declared(self, session: Session) -> dict:
        return session.slots.setdefault("declared", {})

    def _eligibility(self, session: Session, ctx: ToolContext, nlu: NLUResult, mention: money.CurrencyMention) -> dict:
        pending = session.slots.get("pending_request") or {}
        product = nlu.product or pending.get("product") or "personal_loan"
        same = pending.get("product") == product
        months = None if product == "credit_card" else (nlu.months or (pending.get("months") if same else None))
        conv = pending.get("conv") if same else None
        if nlu.amount is not None:
            amount, conv, err = self._to_local(session, ctx, nlu.amount, mention)
            if err:
                session.slots["pending_request"] = {"product": product, "amount": None, "months": months}
                session.slots["awaiting"] = "amount"
                return self._facts("fx_unavailable", "credit_eligibility", awaiting="amount", ccy=err)
        else:
            amount = pending.get("amount") if same and not nlu.product else None
        session.slots["pending_request"] = {"product": product, "amount": amount, "months": months, "conv": conv}
        return self._evaluate(session, ctx, "credit_eligibility")

    def _income(self, session: Session, ctx: ToolContext, nlu: NLUResult, mention: money.CurrencyMention) -> dict:
        """El cliente dice cuanto gana: reemplaza el ingreso usado y la oferta queda condicional (F03). Ya no hay un tope de
        aumento que mande a revision: el asesor verifica los datos declarados de toda oferta condicional."""
        profile = get_profile(ctx)
        if nlu.declared_income is None:
            session.slots["awaiting"] = "income"
            return self._facts("ask_income", "update_income", awaiting="income", ccy=profile["local_currency"])
        inc, conv, err = self._to_local(session, ctx, nlu.declared_income, mention)
        if err:
            return self._facts("fx_unavailable", "update_income", awaiting="income", ccy=err)
        self._declared(session)["income"] = inc
        session.slots["declared_income_conv"] = conv
        session.slots.pop("reasked", None)                       # llego la cifra: se puede volver a repreguntar
        if session.slots.get("pending_request", {}).get("product"):
            # recalculo inmediato con el dato nuevo; sin monto, la opcion mas alta del producto que ya pidio
            return self._evaluate(session, ctx, "update_income")
        if session.slots.get("offers_shown"):                    # ya vio el listado: se lo muestro recalculado
            return self._offers(session, ctx)
        return self._facts("income_saved", "update_income", income=self._m(session, inc, profile["local_currency"]),
                           fx=self._fx_note(session))

    def _offers(self, session: Session, ctx: ToolContext) -> dict:
        """'¿Que ofertas tengo?': la opcion destacada de cada producto (lo mas alto), con los datos del banco y lo declarado."""
        offers = get_offers(ctx, self._declared(session))
        profile, lang = offers.profile, session.language
        if not profile.get("is_eligible"):
            session.slots["pending_request"] = {"product": "personal_loan", "amount": None, "months": None, "conv": None}
            return self._evaluate(session, ctx, "credit_offers")
        if not offers.ordered:
            return self._facts("offers_none", "credit_offers", outcome="offers")
        ccy, lines = profile["local_currency"], []
        for o in offers.ordered:
            product = eng.PRODUCT_NAME[o["product_code"]]
            lines.append(templates.render("offer_line_card" if product == "credit_card" else "offer_line", lang, {
                "product": templates.product_label(product, o["tier"], lang), "months": str(o["term_months"]),
                "max_amount": self._m(session, to_local(profile, o["offer_max_amount_usd"], floor=True), ccy),
                "rate": fmt_pct(o["offer_rate_pct"])}))
        session.slots.setdefault("verified_facts", []).append(
            {"type": "featured_offers", "policy_version": self.policy.version,
             "options": [{k: o[k] for k in ("option_code", "term_months", "offer_rate_pct", "offer_max_amount_usd")}
                         for o in offers.ordered]})
        session.slots["offers_shown"] = True
        kind = "offers_declared" if self._declared(session).get("income") is not None else "offers"
        return self._facts(kind, "credit_offers", outcome="offers", lines=templates.join_list(lines, lang))

    def _proactive_accepted(self, session: Session, ctx: ToolContext, intent: str) -> dict:
        """'Si' a la oferta proactiva: se pide el monto, con lo mas alto como referencia (y como respuesta a un 'si')."""
        product = session.slots.get("offer_product") or "personal_loan"
        q = recalculate_offer(ctx, product, None, None, self._declared(session), record=False)
        if q.outcome not in CONCLUSIVE_OK:
            session.slots["pending_request"] = {"product": product, "amount": None, "months": None, "conv": None}
            return self._evaluate(session, ctx, intent)
        ccy = q.profile["local_currency"]
        top = to_local(q.profile, q.max_amount_usd, floor=True)
        session.slots["pending_request"] = {"product": product, "amount": None, "months": q.term_months if product != "credit_card" else None,
                                            "conv": None, "featured_amount": top}
        session.slots["awaiting"] = "amount"
        kind = "offer_accepted_card" if product == "credit_card" else "offer_accepted"
        return self._facts(kind, intent, awaiting="amount", months=str(q.term_months), max_amount=self._m(session, top, ccy))

    def _evaluate(self, session: Session, ctx: ToolContext, intent: str) -> dict:
        req = session.slots["pending_request"]
        q = recalculate_offer(ctx, req["product"], req.get("amount"), req.get("months"), self._declared(session))
        return self._quote_facts(session, intent, q, req)

    def _record_evaluation(self, session: Session, q: eng.Quote, req: dict) -> dict:
        p = q.profile
        ev = {"request": {"product": req["product"], "amount": req.get("amount"), "months": q.term_months},
              "outcome": q.outcome, "reasons": list(q.reasons), "band": p.get("risk_band"), "rate_pct": q.rate_pct,
              "payment": to_local(p, q.installment_usd), "dti_after": q.dti_after,
              "max_amount": to_local(p, q.max_amount_usd, floor=True), "policy_version": q.policy_version,
              "income_declared_unverified": q.conditional, "flags": list(q.flags),
              "option_code": q.option["option_code"] if q.option else None, "tier": q.option["tier"] if q.option else None,
              "amount_usd": q.amount_usd, "installment_usd": q.installment_usd, "ccy": p.get("local_currency")}
        session.slots["last_evaluation"] = ev
        session.actions.append({"type": "credit_evaluation", "outcome": q.outcome, "verified": True,
                                "policy_version": q.policy_version})
        session.slots.setdefault("verified_facts", []).append({"type": "credit_evaluation", **ev})
        return ev

    def _ask_household(self, session: Session, facts: dict) -> dict:
        """Politica 0.4: a todos los clientes se les pregunta igual, una vez, si alguien mas del hogar aporta ingresos."""
        session.slots["household_asked"] = True
        session.slots["awaiting"] = "household"
        facts["kind2"] = "ask_household"
        facts.update(awaiting="household", suggest="yes_no")
        return facts

    def _quote_facts(self, session: Session, intent: str, q: eng.Quote, req: dict) -> dict:
        lang, p = session.language, q.profile
        ccy = p["local_currency"]
        product = req["product"]
        tier = q.option["tier"] if q.option else None
        label = templates.product_label(product, tier, lang)
        L = lambda usd, floor=False: self._m(session, to_local(p, usd, floor=floor) or 0, ccy)   # noqa: E731
        max_dti = fmt_pct(self.policy.max_dti * 100)
        terms = templates.join_list([str(t) for t in q.allowed_terms], lang)
        can_ask = not session.slots.get("household_asked")

        if q.outcome in CONCLUSIVE_OK and req.get("amount") is None:       # sin monto: lo mas alto primero
            top = to_local(p, q.max_amount_usd, floor=True)
            req.update(featured_amount=top, months=q.term_months if product != "credit_card" else None)
            session.slots["awaiting"] = "amount"
            kind = "offer_featured_card" if product == "credit_card" else "offer_featured"
            return self._facts(kind, intent, outcome="offer", awaiting="amount", product=label, months=str(q.term_months),
                               max_amount=self._m(session, top, ccy), rate=fmt_pct(q.rate_pct),
                               payment=L(q.installment_usd) if q.installment_usd else "", fx=self._fx_note(session))
        if req.get("amount") is not None or q.outcome != eng.UNAVAILABLE:
            self._record_evaluation(session, q, req)

        if q.outcome in CONCLUSIVE_OK:
            card = "_card" if product == "credit_card" else ""
            kind = ("eligible" + card) if q.outcome == eng.ELIGIBLE else ("eligible_card_provisional" if card else "eligible_provisional")
            facts = self._facts(kind, intent, outcome=q.outcome, product=label,
                                amount=self._m(session, req["amount"], ccy), months=str(q.term_months),
                                payment=L(q.installment_usd), rate=fmt_pct(q.rate_pct), dti=fmt_dti(q.dti_after),
                                max_dti=max_dti, fx=self._fx_note(session))
            if can_ask:
                return self._ask_household(session, facts)
            facts["kind2"] = "ask_proceed"                      # "¿Le gustaria que avancemos con la solicitud?"
            facts.update(awaiting="proceed", suggest="yes_no")
            session.slots["awaiting"] = "proceed"
            return facts

        reason = q.reasons[0] if q.reasons else ""
        if q.outcome == eng.NEEDS_DATA:
            session.slots["awaiting"] = "income"
            return self._facts("ask_income", intent, outcome=q.outcome, awaiting="income", ccy=ccy)
        if q.outcome == eng.DECLINED and reason == "DTI_EXCEEDED":
            facts = self._facts("declined_dti", intent, outcome=q.outcome, dti=fmt_pct((q.dti_after or 0) * 100),
                                max_dti=max_dti, months=str(q.term_months), max_amount=L(q.max_amount_usd, True),
                                fx=self._fx_note(session))
            return self._ask_household(session, facts) if can_ask else facts
        if q.outcome == eng.DECLINED and q.reasons == ["R08_NO_CAPACITY"]:
            facts = self._facts("declined_no_capacity", intent, outcome=q.outcome,
                                dti=fmt_pct((p.get("current_debt_to_income") or 0) * 100), max_dti=max_dti,
                                fx=self._fx_note(session))
            return self._ask_household(session, facts) if can_ask else facts
        if q.outcome == eng.DECLINED and q.reasons and all(r.startswith(("R05", "R08")) for r in q.reasons):
            session.slots["awaiting"] = "income"                 # ingreso bajo el minimo: declarar otro puede cambiarlo
            return self._facts("income_below_min", intent, outcome=q.outcome, awaiting="income", ccy=ccy)
        if q.outcome == eng.DECLINED and "R06_SCORE_MISSING" in q.reasons and len(q.reasons) == 1:
            return self._offer_handoff(session, "MISSING_DATA", intent, kind="needs_data_score", outcome=q.outcome)
        if q.outcome == eng.DECLINED:
            why = templates.decline_why(q.reasons, lang, str(self.policy.params.get("min_tenure_months", "")))
            return self._offer_handoff(session, "POLICY_DECLINED", intent, kind="declined_reason" if why else "declined_generic",
                                       outcome=q.outcome, why=why)

        # Elegible, pero no para ese plazo o monto
        max_age = str(self.policy.params.get("max_age_at_maturity_years", ""))
        if reason == "PRODUCT_ABOVE_AGE_AT_MATURITY":
            return self._facts("product_age", intent, outcome=q.outcome, suggest="start", product=label, max_age=max_age,
                               fx=self._fx_note(session))
        if reason == "ABOVE_MAX_AMOUNT":
            session.slots["awaiting"] = "amount"
            req.update(amount=None, featured_amount=to_local(p, q.max_amount_usd, floor=True))
            return self._facts("above_max", intent, outcome=q.outcome, awaiting="amount", product=label,
                               months=str(q.term_months), max_amount=L(q.max_amount_usd, True), fx=self._fx_note(session))
        if reason == "BELOW_MIN_AMOUNT":
            session.slots["awaiting"] = "amount"
            req["amount"] = None
            return self._facts("below_min", intent, outcome=q.outcome, awaiting="amount", product=label,
                               min_amount=self._m(session, math.ceil(to_local(p, q.min_amount_usd)), ccy),
                               fx=self._fx_note(session))
        if reason in ("TERM_NOT_IN_GRID", "TERM_ABOVE_BAND_MAXIMUM", "TERM_ABOVE_AGE_AT_MATURITY") or (
                reason == "NO_CAPACITY_FOR_OPTION" and q.allowed_terms):
            kind = {"TERM_NOT_IN_GRID": "term_not_offered", "TERM_ABOVE_BAND_MAXIMUM": "term_band",
                    "TERM_ABOVE_AGE_AT_MATURITY": "term_age"}.get(reason, "term_no_capacity")
            session.slots["awaiting"] = "term"
            return self._facts(kind, intent, outcome=q.outcome, awaiting="term", product=label, terms=terms, max_age=max_age,
                               fx=self._fx_note(session))
        facts = self._facts("option_no_capacity", intent, outcome=q.outcome, product=label, fx=self._fx_note(session))
        return self._ask_household(session, facts) if can_ask else facts

    def _household_answer(self, session: Session, ctx: ToolContext, awaiting: str, intent: str, message: str,
                          amounts: list[float], mention: money.CurrencyMention) -> dict | None:
        """Respuestas a la pregunta del hogar. None = que siga el flujo general (pidio un asesor, se despide, etc.)."""
        if intent in ("request_human", "closing", "thanks", "greeting", "case_status", "account_inquiry"):
            return None
        if intent == "update_income" and not _THIRD_PARTY.search(norm(message)):
            return None                                           # "ahora gano X": su propio ingreso, no el del hogar
        ccy = get_profile(ctx)["local_currency"]
        if awaiting == "household" and amounts and intent != "confirm_no":
            awaiting = "household_income"                         # "si, mi pareja gana 20.000": ya trae el ingreso
        if awaiting == "household":
            if intent == "confirm_yes":
                session.slots["awaiting"] = "household_income"
                return self._facts("ask_household_income", intent, awaiting="household_income", ccy=ccy)
            if intent == "confirm_no":
                return self._after_household(session, "household_none", intent)
            return None
        if awaiting == "household_income":
            if not amounts:
                if intent == "confirm_no":
                    return self._after_household(session, "household_none", intent)
                if intent in ("unknown", "confirm_yes") and session.slots.get("reasked") != awaiting:
                    session.slots.update(reasked=awaiting, awaiting=awaiting)   # una sola vez
                    facts = self._facts("ask_household_income", intent, awaiting=awaiting, ccy=ccy)
                    facts["pre"] = ["reask_number"]
                    return facts
                return None
            value, conv, err = self._to_local(session, ctx, amounts[0], mention)
            if err:
                session.slots["awaiting"] = "household_income"
                return self._facts("fx_unavailable", intent, awaiting="household_income", ccy=err)
            session.slots["household_pending"] = {"income": value}
            session.slots.pop("reasked", None)
            if len(amounts) > 1 and _INSTALLMENTS.search(norm(message)):   # trajo tambien sus cuotas
                return self._household_answer(session, ctx, "household_debt", intent, message, amounts[1:], mention)
            if _NO_DEBT.search(norm(message)):                             # "...y no paga cuotas": cuotas 0
                return self._household_answer(session, ctx, "household_debt", intent, message, [], mention)
            session.slots["awaiting"] = "household_debt"
            return self._facts("ask_household_debt", intent, awaiting="household_debt")
        # household_debt
        pending = session.slots.pop("household_pending", {})
        debt = amounts[0] if amounts else (0.0 if _NO_DEBT.search(norm(message)) else None)
        if debt is None or not pending:
            # Sin las cuotas de esa persona no se suma su ingreso: el asesor lo completa (politica 0.4, seccion 6)
            session.slots["household_unknown_debt"] = pending.get("income")
            session.actions.append({"type": "household_income_not_added", "reason": "installments_unknown", "verified": True})
            return self._after_household(session, "household_unknown", intent)
        if amounts:
            debt, _, err = self._to_local(session, ctx, debt, mention)
            if err:
                session.slots["household_pending"] = pending
                session.slots["awaiting"] = "household_debt"
                return self._facts("fx_unavailable", intent, awaiting="household_debt", ccy=err)
        declared = self._declared(session)
        declared["household_income"] = pending["income"]
        declared["household_installments"] = debt
        session.actions.append({"type": "household_income_added", "verified": False})
        if session.slots.get("pending_request", {}).get("amount"):
            return self._evaluate(session, ctx, "update_income")
        session.slots["pending_request"] = {**session.slots.get("pending_request", {"product": "personal_loan"}), "amount": None}
        return self._evaluate(session, ctx, "update_income")

    def _after_household(self, session: Session, kind: str, intent: str) -> dict:
        """Tras la pregunta del hogar: si hay una propuesta viable, se pregunta si avanzar; si no, queda abierto."""
        if self._has_proposal(session):
            session.slots["awaiting"] = "proceed"
            facts = self._facts(kind, intent, awaiting="proceed", suggest="yes_no")
            facts["kind2"] = "ask_proceed"
            return facts
        return self._facts("household_none_open" if kind == "household_none" else kind, intent)

    # ------------------------------------------------------------------ solicitud y documentos
    def _doc_list(self, session: Session, ids: list[str]) -> str:
        names = templates.DOC_NAME[session.language]
        return templates.join_list([names[i] for i in ids], session.language)

    def _start_application(self, session: Session, ctx: ToolContext) -> dict:
        req = session.slots.get("pending_request") or {}
        ev = session.slots.get("last_evaluation") or {}
        if not req.get("amount") or ev.get("outcome") not in CONCLUSIVE_OK:
            return self._facts("unknown", "confirm_yes", suggest="start")
        declared = self._declared(session)
        plan = documents.plan(self.rules, self.repo, ctx.customer_id, req["product"],
                              bool(ev.get("income_declared_unverified")), household="household_income" in declared)
        # Aceptacion: se recalcula y se registra la fila de credit_offers (la API, nunca el LLM)
        origin = "proactive" if session.slots.get("offer_made") and session.slots.get("offer_product") == req["product"] \
            else "customer_interest"
        try:
            row = accept_offer(ctx, product=req["product"], amount_local=req["amount"], months=req.get("months"),
                               declared_local=declared, offer_origin=origin, session_id=session.id,
                               language=session.language, required_documents=plan.required)
        except ValueError as exc:
            log(logger, "offer_rejected", error=str(exc))
            return self._facts("unknown", "confirm_yes", suggest="start")
        session.slots["accepted_offer"] = row
        session.actions.append({"type": "offer_accepted", "offer_id": row["offer_id"], "option_code": row["option_code"],
                                "flags": list(row["flags"]), "verified": True})
        session.slots["application"] = {"status": "documents", "product": req["product"], "required": plan.required,
                                        "on_file": plan.on_file, "need": plan.need, "declared": [], "missing": [],
                                        "queue": [], "current": None}
        session.actions.append({"type": "application_started", "required": plan.required, "on_file": plan.on_file,
                                "verified": True})
        if not plan.need:                                        # el banco ya tiene todo lo necesario
            return self._finish_application(session, ctx)
        session.slots["awaiting"] = "docs_all"
        return self._facts("docs_request", "credit_eligibility", awaiting="docs_all", suggest="yes_no",
                           docs=self._doc_list(session, plan.need), lead="")

    def _docs_answer(self, session: Session, ctx: ToolContext, awaiting: str, intent: str) -> dict | None:
        """Responde a la pregunta de documentos. None = que siga el flujo general (pidio un asesor, se despide, etc.)."""
        app = session.slots.get("application")
        if not app or intent in ("request_human", "closing", "thanks", "other_topic", "greeting"):
            return None
        if intent not in ("confirm_yes", "confirm_no"):          # no entendio: se repite la misma pregunta
            session.slots["awaiting"] = awaiting
            lead = templates.REASK[session.language]
            if awaiting == "docs_all":
                return self._facts("docs_request", intent, awaiting="docs_all", suggest="yes_no", lead=lead,
                                   docs=self._doc_list(session, app["need"]))
            return self._facts("docs_item", intent, awaiting="doc_item", suggest="yes_no", lead=lead,
                               doc=self._doc_list(session, [app["current"]]))
        if awaiting == "docs_all":
            if intent == "confirm_yes":
                app["declared"] = list(app["need"])
                return self._finish_application(session, ctx)
            if len(app["need"]) == 1:                            # un solo documento y dijo que no: no se repite la pregunta
                return self._finish_application(session, ctx)
            app["queue"] = list(app["need"])                     # "no": se pregunta uno por uno
            return self._next_doc(session, ctx)
        (app["declared"] if intent == "confirm_yes" else app["missing"]).append(app["current"])
        return self._next_doc(session, ctx)

    def _next_doc(self, session: Session, ctx: ToolContext) -> dict:
        app = session.slots["application"]
        if not app["queue"]:
            return self._finish_application(session, ctx)
        app["current"] = app["queue"].pop(0)
        session.slots["awaiting"] = "doc_item"
        return self._facts("docs_item", "confirm_no", awaiting="doc_item", suggest="yes_no", lead="",
                           doc=self._doc_list(session, [app["current"]]))

    def _finish_application(self, session: Session, ctx: ToolContext) -> dict:
        app = session.slots["application"]
        app["missing"] = [d for d in app["need"] if d not in app["declared"]]
        if app["missing"]:
            app["status"] = "incomplete"
            session.actions.append({"type": "documents_pending", "missing": app["missing"], "verified": True})
            session.slots["awaiting"] = "confirm_handoff"
            session.slots["handoff_reason"] = "DOCS_INCOMPLETE"
            return self._facts("docs_incomplete", "confirm_no", awaiting="confirm_handoff", suggest="yes_no",
                               missing=self._doc_list(session, app["missing"]))
        app["status"] = "ready"
        facts = self._handoff(session, "APPLICATION_READY")      # todo en orden: se deriva a un asesor
        if facts["kind"] == "handoff_created":
            facts["kind"] = "application_ready"
        return self._with_conclusion(session, ctx, facts)

    # ------------------------------------------------------------------ resumen final y correo
    @staticmethod
    def _has_proposal(session: Session) -> bool:
        ev = session.slots.get("last_evaluation") or {}
        return ev.get("outcome") in CONCLUSIVE_OK and bool((session.slots.get("pending_request") or {}).get("amount"))

    def _close_turn(self, session: Session, ctx: ToolContext, intent: str) -> dict:
        if self._has_proposal(session) and not session.slots.get("summary_delivered"):
            facts = self._with_conclusion(session, ctx, self._facts("closing_summary", intent))
            facts["extras"].append("goodbye")
            return facts
        base = self._facts(intent, intent)
        return base if self._has_proposal(session) else self._with_offer(session, ctx, base)

    def conclude(self, session: Session) -> ChatReply:
        """Cierre explicito de la conversacion (POST /end): resumen y aviso de correo si hay una propuesta."""
        ctx = ToolContext(session.customer_id, self.repo, self.policy, self.offers)
        if self._has_proposal(session) and not session.slots.get("summary_delivered"):
            facts = self._with_conclusion(session, ctx, self._facts("closing_summary", "closing"))
            facts["extras"].append("goodbye")
        else:
            facts = self._facts("closing", "closing")
        session.slots["ended"] = True
        facts["variant"] = len(session.history) // 2
        reply = self._render(facts, session.language)
        reply.evidence = build_evidence(ctx.trace, facts)
        session.history.append({"role": "assistant", "text": reply.reply})
        return reply

    def _summary_fmt(self, session: Session, ctx: ToolContext) -> dict[str, str]:
        lang, req, ev = session.language, session.slots["pending_request"], session.slots["last_evaluation"]
        ccy = get_profile(ctx)["local_currency"]
        tx, app = templates.SUMMARY_TEXT[lang], session.slots.get("application") or {}
        if app.get("status") == "ready":
            docs_status = tx["docs_complete"]
        elif app.get("status") == "incomplete":
            docs_status = tx["docs_pending"].format(missing=self._doc_list(session, app["missing"]))
        else:
            docs_status = tx["docs_not_started"]
        ticket = session.handoff["ticket_id"] if session.handoff else ""
        email = self.repo.contact_email_masked(ctx.customer_id) or ""
        return {
            "product": templates.product_label(req["product"], ev.get("tier"), lang),
            "amount": self._m(session, req["amount"], ccy), "months": str(ev["request"]["months"]),
            "rate": fmt_pct(ev["rate_pct"]), "payment": self._m(session, ev["payment"], ccy, 2),
            "dti": fmt_dti(ev["dti_after"]), "max_dti": fmt_pct(self.policy.max_dti * 100),
            "status": tx["provisional"] if ev["outcome"] == eng.ELIGIBLE_PROVISIONAL else tx["eligible"],
            "docs_status": docs_status, "ticket": ticket, "ticket_line": tx["ticket_line"].format(ticket=ticket) if ticket else "",
            "email": email,
        }

    def _with_conclusion(self, session: Session, ctx: ToolContext, facts: dict) -> dict:
        """Agrega el resumen de la propuesta y el aviso de que el detalle se envia por correo en un PDF."""
        if session.slots.get("summary_delivered") or not self._has_proposal(session):
            return facts
        fmt = self._summary_fmt(session, ctx)
        facts["fmt"].update(fmt)
        facts["extras"] = ["summary", "email_notice" if fmt["email"] else "email_notice_noaddr"]
        facts["summary"] = True
        facts["income_declared"] = bool(session.slots["last_evaluation"].get("income_declared_unverified"))   # la propuesta se baso en un ingreso sin verificar
        record = self._queue_email(session, ctx, fmt)
        if record:
            facts["email"] = {"to": fmt["email"] or None, "status": record["status"], "id": record["id"]}
        session.slots["summary_delivered"] = True
        return facts

    def _queue_email(self, session: Session, ctx: ToolContext, fmt: dict[str, str]) -> dict | None:
        if self.outbox is None:
            return None
        lang = session.language
        values = {**fmt, "months_text": f"{fmt['months']} meses", "dti_text": f"{fmt['dti']} (max. {fmt['max_dti']})"}
        rows = [(label, values[key]) for key, label in templates.SUMMARY_LABELS[lang]]
        notes = list(templates.PDF_NOTES[lang])
        fx = self._fx_note(session).strip()
        if fx:
            notes.insert(0, fx)
        pdf = render_summary_pdf(lang=lang, first_name=session.first_name or "", rows=rows, notes=notes,
                                 ticket=fmt["ticket"] or None, policy_version=self.policy.version)
        record = self.outbox.queue(customer_id=ctx.customer_id, to_masked=fmt["email"] or None,
                                   subject=templates.EMAIL_SUBJECT[lang], pdf=pdf, language=lang,
                                   ticket_id=fmt["ticket"] or None)
        session.slots["summary_email"] = record
        session.actions.append({"type": "summary_email_queued", "email_id": record["id"], "status": record["status"],
                                "to_masked": record["to_masked"], "verified": True})
        if session.handoff:                                      # el agente humano ve el resumen y el correo en su ticket
            self.queue.update(session.handoff["ticket_id"], offer_summary=fmt, summary_email=record,
                              actions_taken=list(session.actions))
        return record

    # ------------------------------------------------------------------ monedas
    def _to_local(self, session: Session, ctx: ToolContext, value: float, mention: money.CurrencyMention):
        """(valor en la moneda local, conversion o None, error o None). El cliente habla en su moneda local o en otra
        soportada; las herramientas pasan de moneda local a USD con fx_to_usd del perfil."""
        local = get_profile(ctx)["local_currency"]
        spoken = money.resolve(mention, local)
        if spoken is None:
            return value, None, None                      # no dijo moneda: se asume la local y se conserva lo que haya
        if spoken == local:
            session.slots["spoken_ccy"] = None
            return value, None, None
        conv = money.convert(self.repo, value, spoken, local)
        if conv is None:
            ctx.trace.append({"tool": "fx_rates", "status": "failed", "src": spoken, "dst": local})
            return value, None, spoken                    # sin tasa disponible: se avisa, no se inventa
        ctx.trace.append({"tool": "fx_rates", "status": "success", "src": spoken, "dst": local,
                          "rate": conv.quote.rate, "as_of": conv.quote.as_of})
        session.slots["spoken_ccy"] = spoken
        # Los equivalentes se muestran con la MISMA tasa de esta conversion: las tasas directa e inversa del dataset no son
        # exactamente reciprocas y, si no, pedir 1.000 USD se mostraria como "≈ 1.023 USD" al volver.
        session.slots["spoken_fx"] = {"ccy": spoken, "local": local, "rate": conv.quote.rate}
        return conv.amount_dst, _conv_dict(conv), None

    def _m(self, session: Session, amount: float, ccy: str, decimals: int = 0) -> str:
        """Monto en la moneda local y, si el cliente habla en otra, su equivalente aproximado."""
        base = fmt_money(amount, ccy, decimals)
        spoken, fx = session.slots.get("spoken_ccy"), session.slots.get("spoken_fx")
        if spoken and spoken != ccy and fx and fx["ccy"] == spoken and fx["local"] == ccy:
            return f"{base} (≈ {fmt_money(amount / fx['rate'], spoken, decimals)})"
        return base

    def _fx_note(self, session: Session) -> str:
        """Aviso de conversion (monto original, equivalente, tasa y fecha) mientras se use un monto convertido."""
        notes = []
        for conv in (session.slots.get("pending_request", {}).get("conv"), session.slots.get("declared_income_conv")):
            if conv:
                notes.append(templates.render("fx_note", session.language, {
                    "src_amount": fmt_money(conv["amount_src"], conv["src"], 0), "dst_amount": fmt_money(conv["amount_dst"], conv["dst"], 0),
                    "date": conv["as_of_fmt"], "rate": conv["rate_text"]}))
        return " ".join(notes) + (" " if notes else "")

    # ------------------------------------------------------------------ derivacion
    def _offer_handoff(self, session: Session, reason: str, intent: str, kind: str = "needs_review",
                       outcome: str | None = None, **fmt: str) -> dict:
        """Pide confirmacion antes de derivar (la accion se ejecuta solo con un 'si' explicito)."""
        session.slots["awaiting"] = "confirm_handoff"
        session.slots["handoff_reason"] = reason
        text = templates.REASON_TEXT[session.language].get(reason, "")
        return self._facts(kind, intent, outcome=outcome, awaiting="confirm_handoff", suggest="yes_no", reason=text, **fmt)

    def _handoff(self, session: Session, reason: str) -> dict:
        if session.handoff:
            return self._facts("handoff_exists", "request_human", ticket=session.handoff["ticket_id"],
                               handoff_ticket=session.handoff["ticket_id"])
        ticket, now = new_ticket_id(), utc_iso()
        questions = {
            "USER_REQUEST": ["Motivo de la consulta no especificado por el cliente"],
            "MISSING_DATA": ["Completar score o ingreso faltantes del cliente"],
            "UNCLEAR": ["El asistente no logro entender la consulta"],
            "POLICY_DECLINED": ["El cliente puede pedir revision manual del rechazo"],
            "APPLICATION_READY": ["Verificar los documentos que el cliente declaro tener y el ingreso; completar la revision final"],
            "DOCS_INCOMPLETE": ["Ayudar al cliente a completar la documentacion pendiente: "
                                + ", ".join((session.slots.get("application") or {}).get("missing", []))],
            "ACCOUNT_DETAIL": ["Atender la consulta sobre el producto: el cliente pidio detalle (saldos o movimientos) que el "
                               "asistente no muestra; el asistente solo confirmo que el producto existe"],
            "INCIDENT": ["Atender un incidente que reporta el cliente (fraude, cargo no reconocido, robo u otro): validar los "
                         "hechos y tomar las medidas de seguridad que correspondan"],
            "CASE_FOLLOWUP": ["Informar al cliente el avance de su caso abierto (el asistente solo le dijo categoria, fecha y estado)"],
            "OTHER_TOPIC": ["Atender el tema que el cliente describio (ver case_notes); el asistente no lo resuelve"],
        }.get(reason, ["Revisar el caso segun el motivo indicado"])
        if reason == "USER_REQUEST" and session.slots.get("case"):   # pidio un humano en medio de un tema: retomarlo
            questions = ["Retomar el tema que el cliente describio en el chat (ver case_notes y topic)"]
        session.actions.append({"type": "handoff_created", "ticket_id": ticket, "verified": True, "at": now})
        session.handoff = {"ticket_id": ticket, "created_at": now, "reason": reason}
        if session.slots.get("household_unknown_debt") is not None:
            questions = questions + ["Completar las cuotas mensuales de la persona del hogar que aporta ingresos: el cliente "
                                     "no las supo y su ingreso no se sumo"]
        summary = build_summary(session, ticket, now, reason, session.slots.get("last_evaluation"), questions)
        self._improve_narrative(summary)
        row = session.slots.get("accepted_offer")
        if row and not row.get("handoff_ticket_id") and self.offers is not None:
            # La oferta aceptada queda enlazada a su ticket; la fila nueva reemplaza a la anterior (MERGE por offer_id)
            row.update(status="handed_off", handoff_ticket_id=ticket, advisor_summary=summary["narrative"], updated_at=now)
            self.offers.save(row)
        self.queue.add(summary)
        return self._facts("handoff_created", "request_human", outcome="handed_off", ticket=ticket, handoff_ticket=ticket)

    def _improve_narrative(self, summary: dict) -> None:
        """Si hay LLM, su parrafo reemplaza al de reglas SOLO si no introduce datos ajenos al resumen ni promesas. Un fallo
        o un texto invalido deja el parrafo determinista: el asesor siempre recibe un resumen util."""
        summarize = getattr(self.llm, "summarize", None)
        if summarize is None:
            return
        try:
            candidate = summarize(summary)
        except Exception as exc:
            log(logger, "summary_fallback", error=type(exc).__name__)
            return
        allowed = _digit_runs(json.dumps(summary, ensure_ascii=False, default=str))
        if (candidate and len(candidate) <= 900 and _digit_runs(candidate) <= allowed
                and not _PROMISES.search(norm(candidate)) and not _CLAIMS_HUMAN.search(candidate)):
            summary["narrative"], summary["narrative_source"] = candidate, "llm"

    # ------------------------------------------------------------------ salida
    @staticmethod
    def _facts(kind: str, intent: str, outcome: str | None = None, awaiting: str | None = None,
               suggest: str | None = None, handoff_ticket: str | None = None, **fmt: str) -> dict:
        return {"kind": kind, "intent": intent, "outcome": outcome, "awaiting": awaiting, "suggest": suggest,
                "handoff_ticket": handoff_ticket, "proactive_offer": False,
                "fmt": {k: str(v) for k, v in fmt.items()}}

    def _render(self, facts: dict, lang: str) -> ChatReply:
        draft = templates.render_facts(facts, lang)
        text, rewritten = draft, False
        if facts["kind"] in self.rewrite_kinds and not facts.get("kind2") and not facts.get("extras"):
            try:
                candidate = self.llm.compose(facts, lang, draft)
                if candidate and safe_text(candidate, facts):
                    text, rewritten = candidate, True
            except Exception as exc:
                log(logger, "compose_fallback", error=type(exc).__name__)
        sug = templates.SUGGESTIONS[facts["suggest"]][lang] if facts.get("suggest") else []
        return ChatReply(reply=text, language=lang, intent=facts["intent"], outcome=facts.get("outcome"),
                         handoff_ticket=facts.get("handoff_ticket"), suggested_replies=sug,
                         awaiting=facts.get("awaiting"), llm_rewritten=rewritten,
                         proactive_offer=facts.get("proactive_offer", False),
                         summary_ready=bool(facts.get("summary")), email=facts.get("email"),
                         disclaimer=disclaimer_for(facts))
