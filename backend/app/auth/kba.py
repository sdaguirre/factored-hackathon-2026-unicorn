"""Autenticacion por preguntas de seguridad (KBA) generadas desde datos del cliente.

Reglas de diseno:
- Las respuestas correctas viven solo en el servidor; el cliente recibe ids de opcion aleatorios.
- La verificacion es en tiempo constante y exige TODAS las respuestas correctas.
- Para un documento inexistente se genera un reto senuelo con la misma forma, que nunca se aprueba:
  asi la API no revela si un documento existe.
- El LLM no participa: ni genera ni verifica preguntas, y nunca ve las respuestas.

LIMITE CONOCIDO: con 3 preguntas de 4 opciones, adivinar acierta con probabilidad 1/64 por intento.
Es un prototipo: la mitigacion es el limite de intentos y el bloqueo por documento (ver core/ratelimit.py).
"""
from __future__ import annotations

import hmac
import random
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.core.fmt import fmt_date, fmt_money, fmt_month_year
from app.data.repository import Customer, CustomerRepository

PRODUCT_LABELS = {
    "es": {"Cuenta Ahorro": "cuenta de ahorro", "Tarjeta Crédito": "tarjeta de crédito",
           "Cuenta Corriente": "cuenta corriente", "Tarjeta Débito": "tarjeta de débito",
           "Préstamo Personal": "préstamo personal", "Préstamo Hipotecario": "préstamo hipotecario",
           "Inversión": "inversión", "Seguro": "seguro"},
    "pt": {"Cuenta Ahorro": "conta poupança", "Tarjeta Crédito": "cartão de crédito",
           "Cuenta Corriente": "conta corrente", "Tarjeta Débito": "cartão de débito",
           "Préstamo Personal": "empréstimo pessoal", "Préstamo Hipotecario": "financiamento imobiliário",
           "Inversión": "investimento", "Seguro": "seguro"},
}
TX_LABELS = {"es": {"Purchase": "compra", "Withdrawal": "retiro"}, "pt": {"Purchase": "compra", "Withdrawal": "saque"}}
TEXTS = {
    "es": {
        "product_open_city": "¿En qué ciudad abrió su {product} con terminación {last4}?",
        "product_open_date": "¿En qué mes y año abrió su {product} con terminación {last4}?",
        "tx_city": "¿En qué ciudad hizo su {tx} del {date}?",
        "tx_amount": "¿Cuál fue el monto de su {tx} del {date}?",
    },
    "pt": {
        "product_open_city": "Em que cidade foi aberto o produto {product} com final {last4}?",
        "product_open_date": "Em que mês e ano foi aberto o produto {product} com final {last4}?",
        "tx_city": "Em que cidade foi realizada a operação de {tx} em {date}?",
        "tx_amount": "Qual foi o valor da operação de {tx} em {date}?",
    },
}
TX_WINDOW_DAYS = 365
MIN_OPENING = date(2018, 6, 1)


@dataclass
class Option:
    id: str
    label: str


@dataclass
class Question:
    id: str
    kind: str
    text: str
    options: list[Option]
    correct_option_id: str | None  # None en retos senuelo

    def public(self) -> dict:
        return {"id": self.id, "text": self.text, "options": [{"id": o.id, "label": o.label} for o in self.options]}


@dataclass
class Challenge:
    questions: list[Question] = field(default_factory=list)

    def public(self) -> list[dict]:
        return [q.public() for q in self.questions]


def _rid() -> str:
    return secrets.token_hex(4)


def _make_question(kind: str, text: str, correct: str, distractors: list[str], rng: random.Random) -> Question:
    labels = [correct] + distractors[:3]
    rng.shuffle(labels)
    options = [Option(_rid(), lab) for lab in labels]
    cid = next(o.id for o in options if o.label == correct)
    return Question(_rid(), kind, text, options, cid)


def _to_date(v) -> date:
    return v.date() if isinstance(v, datetime) else (v if isinstance(v, date) else datetime.fromisoformat(str(v)).date())


# -- generadores: devuelven Question o None si no hay datos suficientes ------------------------------------------

def _q_product_city(repo: CustomerRepository, cid: str, lang: str, rng: random.Random, **_) -> Question | None:
    prods = [p for p in repo.products(cid) if p.get("opening_branch_id") and repo.branch_city(p["opening_branch_id"])]
    if not prods:
        return None
    p = rng.choice(prods)
    correct = repo.branch_city(p["opening_branch_id"])
    others = [c for c in repo.branch_cities() if c != correct]
    if len(others) < 3:
        return None
    text = TEXTS[lang]["product_open_city"].format(
        product=PRODUCT_LABELS[lang].get(p["product_type"], p["product_type"]), last4=p["last4"])
    return _make_question("product_open_city", text, correct, rng.sample(others, 3), rng)


def _q_product_date(repo: CustomerRepository, cid: str, lang: str, rng: random.Random, as_of: date, **_) -> Question | None:
    prods = [p for p in repo.products(cid) if p.get("opening_date")]
    if not prods:
        return None
    p = rng.choice(prods)
    d = _to_date(p["opening_date"]).replace(day=1)
    correct = fmt_month_year(d, lang)
    months = []
    for shift in rng.sample([s for s in range(-36, 37) if s != 0], 36):
        y, m = divmod(d.year * 12 + d.month - 1 + shift, 12)
        cand = date(y, m + 1, 1)
        if MIN_OPENING <= cand <= as_of:
            lab = fmt_month_year(cand, lang)
            if lab != correct and lab not in months:
                months.append(lab)
        if len(months) == 3:
            break
    if len(months) < 3:
        return None
    text = TEXTS[lang]["product_open_date"].format(
        product=PRODUCT_LABELS[lang].get(p["product_type"], p["product_type"]), last4=p["last4"])
    return _make_question("product_open_date", text, correct, months, rng)


def _recent_unique_tx(repo: CustomerRepository, cid: str, as_of: date) -> list[dict]:
    txs = [t for t in repo.recent_transactions(cid)
           if _to_date(t["transaction_date"]) >= as_of - timedelta(days=TX_WINDOW_DAYS)]
    keys = [(t["transaction_type"], _to_date(t["transaction_date"])) for t in txs]
    return [t for t, k in zip(txs, keys) if keys.count(k) == 1]  # tipo + dia debe identificar un solo movimiento


def _q_tx_city(repo: CustomerRepository, cid: str, lang: str, rng: random.Random, as_of: date, **_) -> Question | None:
    txs = [t for t in _recent_unique_tx(repo, cid, as_of) if t.get("transaction_city")]
    if not txs:
        return None
    t = rng.choice(txs)
    correct = t["transaction_city"]
    others = [c for c in repo.branch_cities() if c != correct]
    if len(others) < 3:
        return None
    text = TEXTS[lang]["tx_city"].format(tx=TX_LABELS[lang][t["transaction_type"]], date=fmt_date(_to_date(t["transaction_date"])))
    return _make_question("tx_city", text, correct, rng.sample(others, 3), rng)


def _q_tx_amount(repo: CustomerRepository, cid: str, lang: str, rng: random.Random, as_of: date, **_) -> Question | None:
    txs = [t for t in _recent_unique_tx(repo, cid, as_of) if t.get("amount")]
    if not txs:
        return None
    t = rng.choice(txs)
    a = float(t["amount"])
    ccy = t.get("currency", "")
    correct = fmt_money(a, ccy)
    labels: list[str] = []
    for factor in rng.sample([0.5, 0.7, 1.3, 1.6, 2.2], 5):
        lab = fmt_money(round(a * factor, 2), ccy)
        if lab != correct and lab not in labels:
            labels.append(lab)
    text = TEXTS[lang]["tx_amount"].format(tx=TX_LABELS[lang][t["transaction_type"]], date=fmt_date(_to_date(t["transaction_date"])))
    return _make_question("tx_amount", text, correct, labels[:3], rng)


GENERATORS = [_q_product_city, _q_product_date, _q_tx_city, _q_tx_amount]


def build_challenge(repo: CustomerRepository, customer: Customer, *, n: int, lang: str,
                    as_of: date, rng: random.Random | None = None) -> Challenge | None:
    """Devuelve n preguntas de tipos distintos, o None si el cliente no tiene datos para n tipos."""
    rng = rng or random.Random(secrets.randbits(64))
    gens = GENERATORS[:]
    rng.shuffle(gens)
    questions: list[Question] = []
    for gen in gens:
        q = gen(repo, customer.customer_id, lang, rng, as_of=as_of)
        if q:
            questions.append(q)
        if len(questions) == n:
            return Challenge(questions)
    return None


def decoy_challenge(repo: CustomerRepository, *, n: int, lang: str, as_of: date) -> Challenge:
    """Reto senuelo con la misma forma que uno real; ninguna respuesta se acepta (correct_option_id=None).

    Es aleatorio por sesion, igual que los retos reales: uno determinista se delataria al repetir la peticion.
    """
    rng = random.Random(secrets.randbits(64))
    cities = repo.branch_cities()
    prods = list(PRODUCT_LABELS[lang])
    qs: list[Question] = []
    kinds = ["product_open_city", "product_open_date", "tx_city", "tx_amount"]
    rng.shuffle(kinds)
    for kind in kinds[:n]:
        if kind in ("product_open_city", "tx_city"):
            ctext = TEXTS[lang][kind].format(product=PRODUCT_LABELS[lang][rng.choice(prods)], last4=f"{rng.randrange(10000):04d}",
                                             tx=TX_LABELS[lang]["Purchase"], date=fmt_date(as_of - timedelta(days=rng.randrange(5, 300))))
            labels = rng.sample(cities, 4)
        elif kind == "product_open_date":
            ctext = TEXTS[lang][kind].format(product=PRODUCT_LABELS[lang][rng.choice(prods)], last4=f"{rng.randrange(10000):04d}")
            labels = [fmt_month_year(date(2019 + rng.randrange(7), 1 + rng.randrange(12), 1), lang) for _ in range(4)]
            labels = list(dict.fromkeys(labels))
            while len(labels) < 4:
                labels.append(fmt_month_year(date(2019 + rng.randrange(7), 1 + rng.randrange(12), 1), lang))
                labels = list(dict.fromkeys(labels))
        else:
            ctext = TEXTS[lang][kind].format(tx=TX_LABELS[lang]["Withdrawal"], date=fmt_date(as_of - timedelta(days=rng.randrange(5, 300))))
            labels = [fmt_money(round(rng.uniform(50, 4000), 2), "") for _ in range(4)]
        qs.append(Question(_rid(), kind, ctext, [Option(_rid(), lab) for lab in labels[:4]], None))
    return Challenge(qs)


def verify(challenge: Challenge, answers: dict[str, str]) -> bool:
    """Todas las preguntas deben estar respondidas y correctas. Recorre todo para no filtrar por tiempo."""
    ok = True
    for q in challenge.questions:
        given = answers.get(q.id, "")
        expected = q.correct_option_id or ""
        match = bool(expected) and hmac.compare_digest(given.encode(), expected.encode())
        ok = ok and match
    return ok and len(answers) == len(challenge.questions)
