from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.main import create_app


def make_settings(**kw) -> Settings:
    base = dict(env="dev", jwt_secret="test-secret", llm_provider="mock", api_keys="", admin_api_keys="")
    base.update(kw)
    return Settings(_env_file=None, **base)


@pytest.fixture()
def app(tmp_path):
    return create_app(make_settings(outbox_dir=tmp_path / "outbox"))      # PDFs de cada prueba en su propia carpeta


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


def pick_customers(state):
    """Clientes del snapshot por perfil, para pruebas deterministas de la politica."""
    df = state.repo._customers
    ok = df[(df.customer_status == "Active") & df.credit_score.notna() & df.monthly_income.notna()
            & (df.credit_score >= 680) & (df.max_days_past_due == 0)]
    no_income = df[df.credit_score.notna() & df.monthly_income.isna() & (df.customer_status == "Active")
                   & (df.credit_score >= 680) & (df.max_days_past_due == 0)]
    return ok, no_income


def customers_by_offer_profile(state) -> dict[str, list[dict]]:
    """Clasifica el snapshot por (consentimiento, preaprobacion con datos del banco) para pruebas de oferta proactiva."""
    from app.agent.tools import ToolContext, offer_rates
    from app.policy import credit_engine as ce

    out: dict[str, list[dict]] = {"consent_pre": [], "noconsent_pre": [], "consent_notpre": []}
    df = state.repo._customers
    for r in df.itertuples():
        prof = state.repo.credit_profile(r.customer_id)
        info = offer_rates(ToolContext(r.customer_id, state.repo, state.policy))
        pre = info["probe"].decision.outcome == ce.ELIGIBLE and bool(info["rates"]) and (info["probe"].decision.max_amount or 0) > 0
        rec = {"doc": r.document_number, "income": prof["monthly_income"], "cid": r.customer_id}
        if prof["accepts_marketing"] and pre:
            out["consent_pre"].append(rec)
        elif not prof["accepts_marketing"] and pre:
            out["noconsent_pre"].append(rec)
        elif prof["accepts_marketing"] and not pre:
            out["consent_notpre"].append(rec)
    return out
