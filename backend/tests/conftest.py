from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.main import create_app


def make_settings(**kw) -> Settings:
    import tempfile
    from pathlib import Path

    base = dict(env="dev", jwt_secret="test-secret", llm_provider="mock", api_keys="", admin_api_keys="",
                offers_path=Path(tempfile.mkdtemp()) / "credit_offers.jsonl")   # nunca el archivo real de ofertas
    base.update(kw)
    return Settings(_env_file=None, **base)


@pytest.fixture()
def app(tmp_path):
    # PDFs y ofertas aceptadas de cada prueba en su propia carpeta
    return create_app(make_settings(outbox_dir=tmp_path / "outbox", offers_path=tmp_path / "credit_offers.jsonl"))


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def state(app):
    return app.state.ctx


def hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def open_session(client, doc: str, language: str = "es"):
    r = client.post("/v1/sessions", json={"document_number": doc, "language": language})
    return r


def correct_answers(state, sid: str) -> list[dict]:
    """Lee las respuestas correctas del servidor (solo posible desde un test)."""
    ch = state.store.get(sid).challenge
    return [{"question_id": q.id, "option_id": q.correct_option_id} for q in ch.questions]


def login(client, state, doc: str, language: str = "es") -> tuple[str, dict]:
    r = open_session(client, doc, language)
    assert r.status_code == 201, r.text
    sid, token = r.json()["session_id"], r.json()["token"]
    v = client.post(f"/v1/sessions/{sid}/verify", json={"answers": correct_answers(state, sid)}, headers=hdr(token))
    assert v.status_code == 200 and v.json()["status"] == "authenticated", v.text
    return sid, hdr(token)


def authenticable(state, doc) -> bool:
    """El cliente tiene datos para un reto de seguridad completo (si no, la API responde AUTH_UNAVAILABLE)."""
    from app.auth import kba

    customer = state.repo.find_by_document(str(doc))
    return kba.build_challenge(state.repo, customer, n=state.settings.auth_questions, lang="es",
                               as_of=state.settings.as_of_date) is not None


def local_income(state, cid: str) -> float | None:
    """Ingreso usado por la politica (gold, USD) en la moneda local del cliente."""
    p = state.repo.credit_profile(cid)
    return None if p.get("income_used_usd") is None else p["income_used_usd"] / p["fx_to_usd"]


def _profiles(state):
    import pandas as pd

    prof = pd.DataFrame([state.repo.credit_profile(c) for c in state.repo._customers.customer_id])
    return state.repo._customers[["customer_id", "document_number"]].merge(prof, on="customer_id")


def pick_customers(state):
    """Clientes por perfil de gold (que ademas pueden autenticarse), para pruebas deterministas de la politica:
    elegibles con datos del banco, y elegibles salvo por el ingreso que falta (R05_INCOME_MISSING)."""
    df = _profiles(state)
    df["income"] = df.customer_id.map(lambda c: local_income(state, c))
    ok = df[df.is_eligible]
    no_income = df[df.reason_codes.map(lambda r: list(r) == ["R05_INCOME_MISSING"])]
    return ok[ok.document_number.map(lambda d: authenticable(state, d))],         no_income[no_income.document_number.map(lambda d: authenticable(state, d))]


def customers_by_offer_profile(state) -> dict[str, list[dict]]:
    """Clasifica por (consentimiento, preaprobacion con datos del banco) para pruebas de oferta proactiva. Preaprobado =
    elegible en gold con alguna opcion disponible; la oferta proactiva ademas exige offer_mode = proactive."""
    from app.agent.context import blocks_proactive_offer
    from app.agent.tools import ToolContext, get_customer_context, get_offers

    # "consent_pre_case": preaprobado y con consentimiento, pero con un caso abierto que frena la oferta proactiva
    out: dict[str, list[dict]] = {"consent_pre": [], "consent_pre_case": [], "noconsent_pre": [], "consent_notpre": []}
    df = state.repo._customers
    for r in df.itertuples():
        if not authenticable(state, r.document_number):
            continue                                  # sin datos para el reto de seguridad: no puede iniciar sesion en una prueba
        prof = state.repo.credit_profile(r.customer_id)
        tc = ToolContext(r.customer_id, state.repo, state.policy)
        pre = bool(prof["is_eligible"]) and bool(get_offers(tc, record=False).ordered)
        rec = {"doc": r.document_number, "income": local_income(state, r.customer_id), "cid": r.customer_id}
        proactive = prof["offer_mode"] == "proactive"
        if proactive and pre and blocks_proactive_offer(get_customer_context(tc)):
            out["consent_pre_case"].append(rec)
        elif proactive and pre:
            out["consent_pre"].append(rec)
        elif not prof["accepts_marketing"] and pre:
            out["noconsent_pre"].append(rec)
        elif prof["accepts_marketing"] and not pre:
            out["consent_notpre"].append(rec)
    return out



def offer_amount(state, cid: str, product: str = "personal_loan", share: float = 0.6):
    """(monto local, plazo) dentro de la opcion destacada de `product` del cliente (`share` del maximo, nunca bajo el
    minimo), o None si no tiene esa opcion."""
    import math

    from app.agent.tools import ToolContext, get_offers
    from app.policy.engine import PRODUCT_CODE

    o = get_offers(ToolContext(cid, state.repo, state.policy), record=False).featured.get(PRODUCT_CODE[product])
    if o is None:
        return None
    fx = state.repo.credit_profile(cid)["fx_to_usd"]
    usd = max(o["offer_max_amount_usd"] * share, o["option_min_amount_usd"] * 1.01)
    return float(math.floor(usd / fx)), o["term_months"]


def within_offer(state, product: str, share: float = 0.6):
    """(documento, cliente, monto local, plazo) del primer cliente elegible con la opcion destacada de `product`."""
    ok, _ = pick_customers(state)
    for r in ok.itertuples():
        found = offer_amount(state, r.customer_id, product, share)
        if found:
            return r.document_number, r.customer_id, found[0], found[1]
    raise AssertionError(f"ningun cliente con {product} disponible")
