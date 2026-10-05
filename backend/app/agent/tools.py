"""Herramientas del agente. Ninguna recibe customer_id: el contexto lo fija la sesion autenticada.

Es la unica via a datos y acciones; el LLM no llama a nada directamente. Las de credito siguen la politica 0.4 (la de gold):
- get_offers: opciones del cliente, la destacada de cada producto primero (is_featured); solo con datos del banco salvo que
  el cliente haya declarado algo en el chat.
- recalculate_offer: una solicitud concreta (producto, monto, plazo) con lo que el cliente declaro.
- accept_offer: vuelve a calcular, valida (monto dentro de la opcion, 20%) y registra la fila de credit_offers.
Montos de entrada y salida en moneda local; la politica trabaja en USD (fx_to_usd del perfil).
"""
from __future__ import annotations

import math
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.agent.context import build_context
from app.core.offers import OfferStore
from app.data.repository import CustomerRepository
from app.policy import engine as eng

PRODUCTS = ("personal_loan", "credit_card", "mortgage")
# Orden en que se presentan las opciones destacadas: el prestamo personal primero, luego la tarjeta y la hipoteca.
OFFER_ORDER = ("PL", "CC", "MG")


@dataclass
class ToolContext:
    customer_id: str
    repo: CustomerRepository
    policy: eng.Policy
    offers: OfferStore | None = None
    # Llamadas reales a herramientas de ESTE turno. Alimenta la "evidencia" que muestra la interfaz (ver agent/evidence.py):
    # solo se registra lo que de verdad se ejecuto.
    trace: list[dict] = field(default_factory=list)


def get_profile(ctx: ToolContext) -> dict:
    profile = ctx.repo.credit_profile(ctx.customer_id)
    ctx.trace.append({"tool": "customer_profile", "profile": profile})
    return profile


def get_customer_context(ctx: ToolContext) -> dict:
    """Casos abiertos y existencia de productos del cliente de la sesion (ver agent/context.py). Solo lectura."""
    context = build_context(ctx.repo.case_context(ctx.customer_id), ctx.repo.product_overview(ctx.customer_id))
    ctx.trace.append({"tool": "customer_context", "open_cases": context["counts"]["open"],
                      "products": len(context["products"])})
    return context


# ------------------------------------------------------------------ monedas (1 unidad local = fx_to_usd USD)
def to_usd(profile: dict, local: float | None) -> float | None:
    return None if local is None else local * profile["fx_to_usd"]


def to_local(profile: dict, usd: float | None, floor: bool = False) -> float | None:
    """USD -> moneda local. `floor` para topes: el monto mostrado, convertido de vuelta, no debe pasarse del maximo."""
    if usd is None:
        return None
    v = usd / profile["fx_to_usd"]
    return float(math.floor(v)) if floor else v


def declared_usd(profile: dict, declared_local: dict | None) -> dict:
    """Lo declarado en el chat (moneda local) en los nombres de la politica (USD)."""
    d = declared_local or {}
    keys = {"income": "declared_income_usd", "household_income": "additional_income_usd",
            "household_installments": "external_installments_usd"}
    return {k_usd: to_usd(profile, d[k]) for k, k_usd in keys.items() if d.get(k) is not None}


# ------------------------------------------------------------------ herramientas de credito
@dataclass
class Offers:
    profile: dict                  # perfil recalculado
    featured: dict[str, dict]      # codigo de producto -> opcion destacada
    options: list[dict]
    flags: list[str]
    declared: dict

    @property
    def ordered(self) -> list[dict]:
        return [self.featured[c] for c in OFFER_ORDER if c in self.featured]


def get_offers(ctx: ToolContext, declared_local: dict | None = None, record: bool = True) -> Offers:
    """Opciones del cliente de la sesion. Sin datos declarados son exactamente las de gold."""
    profile = get_profile(ctx)
    rec = eng.recalculate(profile, ctx.policy, declared_usd(profile, declared_local))
    out = Offers(rec.profile, eng.featured(rec.options), rec.options, list(rec.flags), dict(rec.declared))
    if record:
        best = out.ordered[0] if out.ordered else None
        ctx.trace.append({"tool": "offer_rates", "ccy": profile["local_currency"], "policy_version": ctx.policy.version,
                          "rates": {eng.PRODUCT_NAME[o["product_code"]]: o["offer_rate_pct"] for o in out.ordered},
                          "max_amount": to_local(profile, best["offer_max_amount_usd"], floor=True) if best else None})
    return out


def recalculate_offer(ctx: ToolContext, product: str, amount_local: float | None, months: int | None,
                      declared_local: dict | None = None, record: bool = True) -> eng.Quote:
    """Evalua una solicitud con la politica 0.4. Sin monto, propone el maximo de la opcion destacada."""
    profile = get_profile(ctx)
    quote = eng.evaluate(profile, ctx.policy, product, to_usd(profile, amount_local), months,
                         declared_usd(profile, declared_local))
    if record:
        ctx.trace.append({"tool": "credit_policy", "quote": quote, "ccy": profile["local_currency"],
                          "fx_to_usd": profile["fx_to_usd"], "max_dti": ctx.policy.max_dti,
                          "request": {"product": product, "amount": amount_local, "months": months}})
    return quote


def accept_offer(ctx: ToolContext, *, product: str, amount_local: float, months: int | None, declared_local: dict | None,
                 offer_origin: str, session_id: str | None, language: str | None, required_documents: list[str]) -> dict:
    """Recalcula la cotizacion (nunca toma cifras del texto), la valida y registra la fila de credit_offers."""
    quote = recalculate_offer(ctx, product, amount_local, months, declared_local, record=False)
    if quote.outcome not in (eng.ELIGIBLE, eng.ELIGIBLE_PROVISIONAL):
        raise ValueError(f"offer not available: {quote.outcome} {quote.reasons}")
    row = eng.build_credit_offer(offer_id=str(uuid.uuid4()), quote=quote, policy=ctx.policy, offer_origin=offer_origin,
                                 session_id=session_id, language=language, required_documents=required_documents,
                                 created_at=datetime.now(timezone.utc).replace(microsecond=0))
    if ctx.offers is not None:
        ctx.offers.save(row)
    ctx.trace.append({"tool": "credit_offer", "offer_id": row["offer_id"], "option_code": row["option_code"]})
    return row


@dataclass
class HandoffQueue:
    """Cola en memoria para el prototipo. En produccion: sistema de tickets / cola del contact center."""
    items: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, summary: dict) -> None:
        with self._lock:
            self.items.append(summary)

    def update(self, ticket_id: str, **fields) -> None:
        with self._lock:
            for it in self.items:
                if it["ticket_id"] == ticket_id:
                    it.update(fields)

    def list(self) -> list[dict]:
        with self._lock:
            return list(self.items)


def new_ticket_id() -> str:
    return "HND-" + secrets.token_hex(4).upper()


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
