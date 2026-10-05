"""Motor de credito: politica 0.4 (docs/CREDIT_RULES.md), la misma que calcula gold.

No reimplementa reglas: carga la implementacion de referencia (data/policy/credit_policy.py) y sus parametros
(data/reference/*.csv), que coinciden con gold en las 1.800.000 opciones (data/scripts/check_engine_parity.py). Este modulo
solo traduce una solicitud del chat (producto, monto, plazo, datos declarados) a una opcion de la grilla y a un resultado.

Todo en USD. Las conversiones a moneda local las hace tools.py con fx_to_usd del perfil.
El LLM nunca aprueba ni calcula: llama a las herramientas y solo redacta.
"""
from __future__ import annotations

import importlib.util
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# Raiz con policy/credit_policy.py y reference/*.csv: data/ del repositorio, o la copia de la imagen (CHAT_POLICY_DIR).
POLICY_ROOT = Path(os.environ.get("CHAT_POLICY_DIR", Path(__file__).resolve().parents[3] / "data"))
_MODULE = "credit_policy_reference"


def _load_reference():
    if _MODULE in sys.modules:
        return sys.modules[_MODULE]
    spec = importlib.util.spec_from_file_location(_MODULE, POLICY_ROOT / "policy" / "credit_policy.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE] = module          # dataclasses resuelven sus tipos por el nombre del modulo
    spec.loader.exec_module(module)
    return module


ref = _load_reference()
Policy = ref.Policy
monthly_installment = ref.monthly_installment

ELIGIBLE = "eligible"
ELIGIBLE_PROVISIONAL = "eligible_provisional"      # depende de datos declarados en el chat (F03): oferta condicional
NEEDS_DATA = "needs_data"
DECLINED = "declined"
UNAVAILABLE = "unavailable"                        # el cliente es elegible, pero no para ese plazo o monto

PRODUCT_CODE = {"personal_loan": "PL", "mortgage": "MG", "credit_card": "CC"}
PRODUCT_NAME = {v: k for k, v in PRODUCT_CODE.items()}


def load_policy(root: Path = POLICY_ROOT) -> Policy:
    return ref.load_policy(root / "reference")


def clean_profile(row: dict) -> dict:
    """Fila de gold (pandas/pyarrow) -> dict de Python: NaN a None, arreglos a listas, escalares numpy a nativos."""
    out = {}
    for k, v in row.items():
        if hasattr(v, "tolist") and not isinstance(v, (str, bytes)):
            v = v.tolist()
        if isinstance(v, float) and math.isnan(v):
            v = None
        if v is not None and type(v).__name__ in ("NaTType",):
            v = None
        out[k] = v
    return out


def offer_options(profile: dict, policy: Policy) -> list[dict]:
    """Opciones de gold para un perfil. Es la funcion que valida check_engine_parity.py --engine."""
    return ref.offer_options(clean_profile(profile), policy)


def band_max_term(policy: Policy, band: str | None, product_code: str) -> int:
    key = "max_term_personal_loan_months" if product_code == "PL" else "max_term_mortgage_months"
    return next((b[key] for b in policy.bands if b["band"] == band), 0)


@dataclass
class Quote:
    """Resultado de una consulta. Montos en USD; `option` es la opcion de la grilla usada (o None)."""
    outcome: str
    product: str
    reasons: list[str] = field(default_factory=list)
    option: dict | None = None
    amount_usd: float | None = None
    term_months: int | None = None
    rate_pct: float | None = None
    installment_usd: float | None = None
    dti_after: float | None = None
    max_amount_usd: float | None = None          # monto maximo de la opcion (o del producto) con la capacidad actual
    min_amount_usd: float | None = None
    allowed_terms: list[int] = field(default_factory=list)   # plazos disponibles del producto para este cliente
    flags: list[str] = field(default_factory=list)
    declared: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)  # perfil recalculado (ingreso, cuotas y capacidad usados)
    policy_version: str = ""

    @property
    def conditional(self) -> bool:
        return bool(self.declared)


def recalculate(profile: dict, policy: Policy, declared_usd: dict | None = None):
    d = declared_usd or {}
    return ref.recalculate(clean_profile(profile), policy, declared_income_usd=d.get("declared_income_usd"),
                           additional_income_usd=d.get("additional_income_usd"),
                           external_installments_usd=d.get("external_installments_usd"))


def featured(options: list[dict]) -> dict[str, dict]:
    """La opcion a presentar primero por producto (is_featured de gold): codigo de producto -> opcion."""
    return {o["product_code"]: o for o in options if o["is_featured"]}


def evaluate(profile: dict, policy: Policy, product: str, amount_usd: float | None = None, term_months: int | None = None,
             declared_usd: dict | None = None) -> Quote:
    """Evalua una solicitud. Sin monto, propone la opcion destacada (la mas alta); sin plazo, el de la opcion destacada."""
    rec = recalculate(profile, policy, declared_usd)
    p, code = rec.profile, PRODUCT_CODE[product]
    q = Quote(outcome=DECLINED, product=product, flags=list(rec.flags), declared=dict(rec.declared), profile=p,
              policy_version=policy.version)
    if not p.get("is_eligible"):
        codes = list(p.get("reason_codes") or [])
        q.reasons = codes
        hard = [c for c in codes if not c.startswith(("R05", "R08"))]
        if not hard and "R05_INCOME_MISSING" in codes:
            q.outcome = NEEDS_DATA
        elif not hard and codes == ["R08_NO_CAPACITY"]:
            q.max_amount_usd = 0.0
        return q

    options = [o for o in rec.options if o["product_code"] == code]
    available = [o for o in options if o["is_available"]]
    q.allowed_terms = sorted({o["term_months"] for o in available})
    star = next((o for o in available if o["is_featured"]), None)

    if star is None:                                    # ninguna opcion del producto: por edad o por capacidad
        q.outcome, q.max_amount_usd = UNAVAILABLE, 0.0
        band_max = band_max_term(policy, p.get("risk_band"), code) if code != "CC" else 0
        by_age = [o for o in options if code != "CC" and o["term_months"] <= band_max and not o["term_allowed"]]
        q.reasons = ["PRODUCT_ABOVE_AGE_AT_MATURITY" if by_age and not any(o["term_allowed"] for o in options)
                     else "NO_CAPACITY_FOR_OPTION"]
        return q

    if code == "CC":
        opt = star
        if amount_usd is not None:
            fits = [o for o in available if o["option_min_amount_usd"] <= amount_usd <= o["offer_max_amount_usd"] + 0.01]
            opt = fits[-1] if fits else star          # la grilla va de Classic a Black: el ultimo que alcanza es el mayor
    else:
        term = term_months or star["term_months"]
        grid_terms = sorted({o["term_months"] for o in options})
        if term is not None and term not in grid_terms:
            q.outcome, q.reasons, q.allowed_terms = UNAVAILABLE, ["TERM_NOT_IN_GRID"], q.allowed_terms or grid_terms
            return q
        opt = next((o for o in options if o["term_months"] == term), None)
        if opt is not None and not opt["term_allowed"]:
            age_capped = term <= band_max_term(policy, p.get("risk_band"), code)
            q.outcome, q.reasons = UNAVAILABLE, ["TERM_ABOVE_AGE_AT_MATURITY" if age_capped else "TERM_ABOVE_BAND_MAXIMUM"]
            q.term_months = term
            return q

    if not opt["is_available"]:                         # plazo permitido, pero la capacidad no llega al minimo
        q.outcome, q.reasons, q.max_amount_usd, q.term_months = UNAVAILABLE, ["NO_CAPACITY_FOR_OPTION"], 0.0, opt["term_months"]
        return q

    q.option, q.term_months, q.rate_pct = opt, opt["term_months"], opt["offer_rate_pct"]
    q.min_amount_usd, q.max_amount_usd = opt["option_min_amount_usd"], opt["offer_max_amount_usd"]
    if code == "CC":                                    # tarjetas: el rango es el de todos los niveles disponibles
        q.min_amount_usd = min(o["option_min_amount_usd"] for o in available)
        q.max_amount_usd = max(o["offer_max_amount_usd"] for o in available)
    amount = q.max_amount_usd if amount_usd is None else amount_usd
    if amount > opt["offer_max_amount_usd"] + 0.01:       # un centavo de tolerancia por la conversion de moneda
        q.amount_usd = amount
        q.installment_usd = monthly_installment(amount, opt["offer_rate_pct"], opt["term_months"])
        q.dti_after = _dti(p, q.installment_usd)
        if q.dti_after is not None and q.dti_after <= policy.max_dti:
            q.outcome, q.reasons = UNAVAILABLE, ["ABOVE_MAX_AMOUNT"]    # cabe en el 20%, pero supera el tope del producto
        else:
            q.outcome, q.reasons = DECLINED, ["DTI_EXCEEDED"]
        return q
    amount = min(amount, opt["offer_max_amount_usd"])
    if amount < opt["option_min_amount_usd"]:
        q.outcome, q.reasons, q.amount_usd = UNAVAILABLE, ["BELOW_MIN_AMOUNT"], amount
        return q
    q.amount_usd = amount
    q.installment_usd = monthly_installment(amount, opt["offer_rate_pct"], opt["term_months"])
    q.dti_after = _dti(p, q.installment_usd)
    q.outcome = ELIGIBLE_PROVISIONAL if q.conditional else ELIGIBLE
    q.reasons = ["F03_DECLARED_DATA"] if q.conditional else ["WITHIN_LIMIT"]
    return q


def _dti(profile: dict, installment: float) -> float | None:
    income = profile.get("income_used_usd")
    return ((profile.get("current_installments_usd") or 0.0) + installment) / income if income else None


def build_credit_offer(*, offer_id: str, quote: Quote, policy: Policy, offer_origin: str, session_id: str | None,
                       language: str | None, required_documents: list[str], created_at: datetime) -> dict:
    """Fila de gold credit_offers para la cotizacion aceptada. La referencia valida monto, opcion y el 20%, y agrega F02."""
    declared = {k: round(v, 2) for k, v in quote.declared.items()}
    row = ref.build_credit_offer(
        offer_id=offer_id, profile=quote.profile, option=quote.option, amount_usd=quote.amount_usd, policy=policy,
        flags=quote.flags, offer_origin=offer_origin, created_at=created_at, session_id=session_id, channel="chat",
        language=language, customer_declared_data=_json(declared) if declared else None,
        required_documents=required_documents)
    return row


def _json(d: dict) -> str:
    import json

    return json.dumps(d, sort_keys=True)


def jsonable(row: dict) -> dict:
    """Fila lista para JSON: fechas en ISO."""
    return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in row.items()}
