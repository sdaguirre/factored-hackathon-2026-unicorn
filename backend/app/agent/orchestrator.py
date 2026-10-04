"""Orquestador: maquina de estados del chat de credito.

Flujo por turno:  mensaje -> NLU (LLM o reglas) -> validacion en codigo -> accion/herramienta/politica
                  -> hechos verificados -> texto (plantilla es/pt, opcionalmente reescrito por el LLM).
El LLM nunca decide: la elegibilidad sale de app.policy.credit_engine y las acciones, de tools.py.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.agent import proactive, templates
from app.agent.handoff import build_summary
from app.agent.language import norm, parse_amounts, parse_months
from app.agent.llm import LLM, MockLLM
from app.agent.nlu import HUMAN_REQUEST, NLUResult
from app.agent.tools import (DEFAULT_MONTHS, SUPPORTED_PRODUCTS, HandoffQueue, ToolContext, evaluate_credit,
                             get_profile, new_ticket_id, offer_rates, utc_iso)
from app.core.fmt import fmt_money, fmt_pct
from app.core.sessions import Session
from app.logging_setup import log
from app.policy import credit_engine as ce

logger = logging.getLogger(__name__)
MAX_REPLY_CHARS = 1200
# Solo estos mensajes pueden ser reescritos por el LLM. Decisiones de credito, ofertas, derivaciones y avisos legales
# salen siempre de la plantilla revisada: el guardia de numeros no detecta frases nuevas que cambien el compromiso.
DEFAULT_REWRITE_KINDS = frozenset({"greeting", "thanks", "closing", "unknown", "ask_amount", "ask_income",
                                   "handoff_declined", "offer_declined", "offer_accepted"})


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


def _digit_runs(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def safe_text(candidate: str, facts: dict) -> bool:
    """Acepta el texto del LLM solo si no introduce numeros ajenos a los hechos y tiene tamano razonable."""
    allowed = _digit_runs(" ".join(facts.get("fmt", {}).values()))
    return 0 < len(candidate) <= MAX_REPLY_CHARS and _digit_runs(candidate) <= allowed


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


class Orchestrator:
    def __init__(self, repo, policy: dict, llm: LLM, queue: HandoffQueue,
                 rewrite_kinds: frozenset[str] = DEFAULT_REWRITE_KINDS):
        self.repo, self.policy, self.llm, self.queue = repo, policy, llm, queue
        self.rewrite_kinds = rewrite_kinds
        self._fallback = MockLLM()

    # ------------------------------------------------------------------ entrada
    def handle(self, session: Session, message: str) -> ChatReply:
        nlu = self._understand(session, message)
        if nlu.language:
            session.language = nlu.language
        lang = session.language
        session.history.append({"role": "user", "text": message})
        ctx = ToolContext(session.customer_id, self.repo, self.policy)
        if nlu.sentiment == "negative" or nlu.sensitive_topic:
            session.slots["no_offers"] = True  # en toda la sesion: ninguna oferta comercial

        facts = self._dispatch(session, ctx, nlu, message)
        reply = self._render(facts, lang)
        session.history.append({"role": "assistant", "text": reply.reply})
        log(logger, "turn", intent=nlu.intent, kind=facts["kind"], outcome=facts.get("outcome"),
            awaiting=facts.get("awaiting"), proactive=reply.proactive_offer, llm=self.llm.name,
            llm_rewritten=reply.llm_rewritten, lang=lang)
        return reply

    def _understand(self, session: Session, message: str) -> NLUResult:
        pending = session.slots.get("awaiting") in ("confirm_handoff", "offer_interest")  # hay una pregunta de si/no
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

        if awaiting == "confirm_handoff":
            if intent == "confirm_yes":
                reason = session.slots.pop("handoff_reason", "USER_REQUEST")
                facts = self._handoff(session, reason)
                return self._with_offer(session, ctx, facts) if reason == "OTHER_TOPIC" else facts
            if intent == "confirm_no":
                reason = session.slots.pop("handoff_reason", "USER_REQUEST")
                facts = self._facts("handoff_declined", intent)
                return self._with_offer(session, ctx, facts) if reason == "OTHER_TOPIC" else facts
        if awaiting == "offer_interest":
            if intent == "confirm_yes":
                session.slots["pending_request"] = {"product": "personal_loan", "amount": None,
                                                    "months": DEFAULT_MONTHS["personal_loan"]}
                session.slots["awaiting"] = "amount"
                return self._facts("offer_accepted", intent, awaiting="amount", months=str(DEFAULT_MONTHS["personal_loan"]))
            if intent == "confirm_no":
                session.slots["offer_declined"] = True
                return self._facts("offer_declined", intent)
        if awaiting == "amount" and amounts and intent in ("unknown", "credit_eligibility", "confirm_yes"):
            nlu = nlu.model_copy(update={"intent": "credit_eligibility", "amount": amounts[0]})
            intent = "credit_eligibility"
        if awaiting == "income" and amounts and intent in ("unknown", "update_income"):
            nlu = nlu.model_copy(update={"intent": "update_income", "declared_income": amounts[0]})
            intent = "update_income"

        session.unknown_streak = session.unknown_streak + 1 if intent == "unknown" else 0

        if intent == "request_human":
            return self._handoff(session, "USER_REQUEST")
        if intent == "greeting":
            return self._facts("greeting", intent, first_name=session.first_name or "", suggest="start")
        if intent == "thanks":
            return self._with_offer(session, ctx, self._facts("thanks", intent))
        if intent == "closing":
            return self._with_offer(session, ctx, self._facts("closing", intent))
        if intent == "other_topic":
            return self._offer_handoff(session, "OTHER_TOPIC", intent, kind="other_topic")
        if intent == "credit_offers":
            return self._offers(session, ctx)
        if intent == "credit_eligibility":
            return self._eligibility(session, ctx, nlu)
        if intent == "update_income":
            return self._income(session, ctx, nlu)
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
        facts["kind2"] = "offer_proactive"
        facts["fmt"].update(d.fmt)
        facts.update(awaiting="offer_interest", suggest="yes_no", proactive_offer=True)
        return facts

    # ------------------------------------------------------------------ casos
    def _eligibility(self, session: Session, ctx: ToolContext, nlu: NLUResult) -> dict:
        pending = session.slots.get("pending_request") or {}
        product = nlu.product or pending.get("product") or "personal_loan"
        if product not in SUPPORTED_PRODUCTS:
            return self._offer_handoff(session, "UNSUPPORTED_PRODUCT", "credit_eligibility", kind="unsupported_product")
        months = nlu.months or DEFAULT_MONTHS[product]
        amount = nlu.amount or (pending.get("amount") if pending.get("product") == product and not nlu.product else None)
        if amount is None:
            session.slots["awaiting"] = "amount"
            session.slots["pending_request"] = {"product": product, "amount": None, "months": months}
            return self._facts("ask_amount", "credit_eligibility", awaiting="amount",
                               product=templates.PRODUCT_NAME[session.language][product], months=str(months))
        session.slots["pending_request"] = {"product": product, "amount": amount, "months": months}
        return self._evaluate(session, ctx, "credit_eligibility")

    def _income(self, session: Session, ctx: ToolContext, nlu: NLUResult) -> dict:
        profile = get_profile(ctx)
        if nlu.declared_income is None:
            session.slots["awaiting"] = "income"
            return self._facts("ask_income", "update_income", awaiting="income", ccy=profile["income_ccy"])
        inc = nlu.declared_income
        session.slots["declared_income"] = inc
        on_file = profile["monthly_income"]
        if on_file and inc > on_file * (1 + self.policy["declared_income"]["max_uplift_without_review"]):
            return self._offer_handoff(session, "INCOME_UPLIFT_REVIEW", "update_income", kind="income_review")
        if session.slots.get("pending_request", {}).get("amount"):
            return self._evaluate(session, ctx, "update_income")  # recalculo inmediato con el dato nuevo
        return self._facts("income_saved", "update_income", income=fmt_money(inc, profile["income_ccy"], 0))

    def _offers(self, session: Session, ctx: ToolContext) -> dict:
        info = offer_rates(ctx, session.slots.get("declared_income"))
        probe, ccy = info["probe"], info["ccy"]
        if not info["rates"]:
            return self._from_decision(session, "credit_offers", probe.decision, ccy)
        names = templates.PRODUCT_NAME[session.language]
        lines = ", ".join(f"{names[p]}: {fmt_pct(r)}" for p, r in info["rates"].items())
        d = probe.decision
        if d.max_amount:
            cap = templates.render("offers_capacity", session.language,
                                   {"months": str(DEFAULT_MONTHS["personal_loan"]), "max_amount": fmt_money(d.max_amount, ccy, 0)})
        else:
            cap = templates.render("offers_no_capacity", session.language, {})
        session.slots.setdefault("verified_facts", []).append(
            {"type": "offer_rates", "rates": {k: round(v, 2) for k, v in info["rates"].items()},
             "policy_version": self.policy["version"]})
        return self._facts("offers", "credit_offers", outcome="offers", lines=lines, capacity=cap)

    def _evaluate(self, session: Session, ctx: ToolContext, intent: str) -> dict:
        req = session.slots["pending_request"]
        res = evaluate_credit(ctx, req["product"], req["amount"], req["months"], session.slots.get("declared_income"))
        d = res.decision
        evaluation = {"request": res.request, "outcome": d.outcome, "reasons": d.reasons, "band": d.band,
                      "rate_pct": d.rate_pct, "payment": d.payment, "dti_after": d.dti_after,
                      "max_amount": d.max_amount, "policy_version": d.policy_version,
                      "income_declared_unverified": res.income_declared}
        session.slots["last_evaluation"] = evaluation
        session.actions.append({"type": "credit_evaluation", "outcome": d.outcome, "verified": True,
                                "policy_version": d.policy_version})
        session.slots.setdefault("verified_facts", []).append({"type": "credit_evaluation", **evaluation})
        if d.outcome in (ce.ELIGIBLE, ce.ELIGIBLE_PROVISIONAL):
            p = self.policy
            return self._facts(d.outcome, intent, outcome=d.outcome,
                               product=templates.PRODUCT_NAME[session.language][req["product"]],
                               amount=fmt_money(req["amount"], res.ccy, 0), months=str(req["months"]),
                               payment=fmt_money(d.payment, res.ccy, 2), rate=fmt_pct(d.rate_pct),
                               dti=fmt_pct(d.dti_after * 100), max_dti=fmt_pct(p["max_dti"] * 100))
        return self._from_decision(session, intent, d, res.ccy, req)

    def _from_decision(self, session: Session, intent: str, d: ce.Decision, ccy: str, req: dict | None = None) -> dict:
        reason = d.reasons[0] if d.reasons else ""
        if d.outcome == ce.NEEDS_DATA:
            if "monthly_income" in d.missing and "credit_score" not in d.missing:
                session.slots["awaiting"] = "income"
                return self._facts("ask_income", intent, outcome=d.outcome, awaiting="income", ccy=ccy)
            return self._offer_handoff(session, "MISSING_DATA", intent, kind="needs_data_score", outcome=d.outcome)
        if d.outcome == ce.DECLINED and reason == "DTI_EXCEEDED" and req and (d.max_amount or 0) <= 0:
            return self._facts("declined_no_capacity", intent, outcome=d.outcome,
                               dti=fmt_pct(d.dti_after * 100), max_dti=fmt_pct(self.policy["max_dti"] * 100))
        if d.outcome == ce.DECLINED and reason == "DTI_EXCEEDED" and req:
            return self._facts("declined_dti", intent, outcome=d.outcome,
                               dti=fmt_pct(d.dti_after * 100), max_dti=fmt_pct(self.policy["max_dti"] * 100),
                               months=str(req["months"]), max_amount=fmt_money(d.max_amount or 0, ccy, 0))
        if d.outcome == ce.DECLINED:
            return self._offer_handoff(session, "POLICY_DECLINED", intent, kind="declined_generic", outcome=d.outcome)
        return self._offer_handoff(session, reason or "MISSING_DATA", intent, kind="needs_review", outcome=d.outcome)

    # ------------------------------------------------------------------ derivacion
    def _offer_handoff(self, session: Session, reason: str, intent: str, kind: str = "needs_review",
                       outcome: str | None = None) -> dict:
        """Pide confirmacion antes de derivar (la accion se ejecuta solo con un 'si' explicito)."""
        session.slots["awaiting"] = "confirm_handoff"
        session.slots["handoff_reason"] = reason
        text = templates.REASON_TEXT[session.language].get(reason, "")
        return self._facts(kind, intent, outcome=outcome, awaiting="confirm_handoff", suggest="yes_no", reason=text)

    def _handoff(self, session: Session, reason: str) -> dict:
        if session.handoff:
            return self._facts("handoff_exists", "request_human", ticket=session.handoff["ticket_id"],
                               handoff_ticket=session.handoff["ticket_id"])
        ticket, now = new_ticket_id(), utc_iso()
        questions = {
            "USER_REQUEST": ["Motivo de la consulta no especificado por el cliente"],
            "MISSING_DATA": ["Completar score o ingreso faltantes del cliente"],
            "INCOME_UPLIFT_REVIEW": ["Verificar el ingreso declarado, muy superior al registrado"],
            "UNSUPPORTED_PRODUCT": ["Atender solicitud de tarjeta de credito"],
            "UNCLEAR": ["El asistente no logro entender la consulta"],
            "POLICY_DECLINED": ["El cliente puede pedir revision manual del rechazo"],
        }.get(reason, ["Revisar el caso segun el motivo indicado"])
        session.actions.append({"type": "handoff_created", "ticket_id": ticket, "verified": True, "at": now})
        session.handoff = {"ticket_id": ticket, "created_at": now, "reason": reason}
        self.queue.add(build_summary(session, ticket, now, reason, session.slots.get("last_evaluation"), questions))
        return self._facts("handoff_created", "request_human", outcome="handed_off", ticket=ticket, handoff_ticket=ticket)

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
        if facts["kind"] in self.rewrite_kinds and not facts.get("kind2"):
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
                         proactive_offer=facts.get("proactive_offer", False))
