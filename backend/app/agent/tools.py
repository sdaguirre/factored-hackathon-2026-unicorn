"""Herramientas del agente. Ninguna recibe customer_id: el contexto lo fija la sesion autenticada.

Es la unica via a datos y acciones; el LLM no llama a nada directamente.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.data.repository import CustomerRepository
from app.policy import credit_engine as ce

DEFAULT_MONTHS = {"personal_loan": 36, "mortgage": 180}
SUPPORTED_PRODUCTS = tuple(DEFAULT_MONTHS)  # tarjeta de credito: se deriva a un asesor


@dataclass
class ToolContext:
    customer_id: str
    repo: CustomerRepository
    policy: dict


@dataclass
class EvalResult:
    decision: ce.Decision
    request: dict
    income_used: float | None
    income_declared: bool
    ccy: str


def get_profile(ctx: ToolContext) -> dict:
    return ctx.repo.credit_profile(ctx.customer_id)


def evaluate_credit(ctx: ToolContext, product: str, amount: float, months: int,
                    declared_income: float | None = None) -> EvalResult:
    """Corre el motor determinista. Un ingreso declarado muy superior al registrado va a revision humana."""
    profile = get_profile(ctx)
    on_file = profile["monthly_income"]
    income = declared_income if declared_income is not None else on_file
    request = {"product": product, "amount": amount, "months": months}
    ccy = profile["income_ccy"]

    if declared_income is not None and on_file:
        max_uplift = ctx.policy["declared_income"]["max_uplift_without_review"]
        if declared_income > on_file * (1 + max_uplift):
            d = ce.Decision(outcome=ce.NEEDS_REVIEW, reasons=["INCOME_UPLIFT_REVIEW"],
                            policy_version=ctx.policy["version"])
            return EvalResult(d, request, income, True, ccy)

    applicant = {
        "customer_status": profile["customer_status"], "credit_score": profile["credit_score"],
        "monthly_income": income, "income_is_declared": declared_income is not None,
        "existing_monthly_debt": profile["existing_monthly_debt"], "max_days_past_due": profile["max_days_past_due"],
        "n_active_products": profile["n_active_products"],
    }
    d = ce.evaluate(applicant, request, ctx.policy)
    return EvalResult(d, request, income, declared_income is not None, ccy)


def offer_rates(ctx: ToolContext, declared_income: float | None = None) -> dict:
    """Tasas de referencia por producto para la banda del cliente, y capacidad maxima de un prestamo personal."""
    profile = get_profile(ctx)
    probe = evaluate_credit(ctx, "personal_loan", 1.0, DEFAULT_MONTHS["personal_loan"], declared_income)
    band = probe.decision.band
    rates = {}
    if band is not None and band not in ctx.policy["no_lending_bands"]:
        for p in ("personal_loan", "mortgage", "credit_card"):
            rates[p] = ce.annual_rate(p, band, profile["n_active_products"], ctx.policy)
    return {"rates": rates, "probe": probe, "ccy": profile["income_ccy"]}


@dataclass
class HandoffQueue:
    """Cola en memoria para el prototipo. En produccion: sistema de tickets / cola del contact center."""
    items: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, summary: dict) -> None:
        with self._lock:
            self.items.append(summary)

    def list(self) -> list[dict]:
        with self._lock:
            return list(self.items)


def new_ticket_id() -> str:
    return "HND-" + secrets.token_hex(4).upper()


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
