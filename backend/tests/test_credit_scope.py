"""The chat only handles credit offers and reads only gold: anything else is handed off to an advisor (with confirmation),
without the assistant looking at products, balances or cases. Team fixture (invented)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR
from app.main import create_app
from tests.conftest import make_settings
from tests.test_support_flow import say, start, summary_of

FIXTURE = BASE_DIR / "data" / "fixture"


@pytest.fixture()
def app(tmp_path):
    return create_app(make_settings(outbox_dir=tmp_path / "outbox", data_dir=FIXTURE))


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def state(app):
    return app.state.ctx


class NoRawData:
    """Fails the test if the conversation touches anything but the gold profile (and identity data)."""

    def __init__(self, repo):
        self.repo = repo

    def __getattr__(self, name):
        if name in ("products", "branch_city", "branch_cities", "branch_country", "branch_countries", "profile_facts"):
            raise AssertionError(f"the assistant read raw data: repo.{name}")
        return getattr(self.repo, name)


def test_welcome_is_always_about_credit_even_with_an_open_complaint(client, state):
    assert state.repo.credit_profile("FXC-002")["open_complaints"] > 0
    _, _, v = start(client, state, "FXC-002")
    assert "ofertas de crédito" in v["greeting"] and "reclamo" not in v["greeting"]
    assert v["suggested_replies"] == ["Ver mis ofertas de crédito", "Hablar con un asesor"]


@pytest.mark.parametrize("message", ["¿cuál es el saldo de mi cuenta de ahorros?", "¿qué tarjetas tengo a mi nombre?",
                                     "¿cómo va mi reclamo?", "olvidé la clave de la app"])
def test_anything_but_credit_is_handed_off_with_confirmation_and_no_bank_data(client, state, message):
    sid, h, _ = start(client, state, "FXC-002")
    state.orchestrator.repo = NoRawData(state.orchestrator.repo)
    r = say(client, sid, h, message)
    assert r["awaiting"] == "confirm_handoff" and r["handoff_ticket"] is None          # nothing done without a yes
    assert "asesor" in r["reply"] and "terminación" not in r["reply"] and "estado es" not in r["reply"]
    assert say(client, sid, h, "sí")["handoff_ticket"]


def test_portuguese_non_credit_topic_goes_to_a_consultant(client, state):
    sid, h, _ = start(client, state, "FXC-018", "pt")
    r = say(client, sid, h, "qual o saldo da minha conta corrente?")
    assert r["awaiting"] == "confirm_handoff" and "consultor" in r["reply"]


def test_off_topic_questions_point_back_to_credit(client, state):
    sid, h, _ = start(client, state, "FXC-016")
    r = say(client, sid, h, "¿cuál es la capital de Francia?")
    assert "ofertas de crédito" in r["reply"] and "productos" not in r["reply"]


def test_the_advisor_summary_carries_gold_counts_only(client, state):
    sid, h, _ = start(client, state, "FXC-002")
    say(client, sid, h, "¿cómo va mi reclamo?")
    say(client, sid, h, "sí")
    ctx = summary_of(client, sid, h)["customer_context"]
    prof = state.repo.credit_profile("FXC-002")
    assert ctx["source"] == "gold.customer_credit_profile" and ctx["open_cases"] == [] and ctx["products"] == []
    assert ctx["credit"]["open_complaints"] == prof["open_complaints"]
    assert ctx["credit"]["active_credit_cards"] == prof["active_credit_cards"]


def test_the_repository_no_longer_holds_raw_cases_transactions_or_fx_files(state):
    for attr in ("case_context", "product_overview", "recent_transactions", "_tx", "_cases", "_fx"):
        assert not hasattr(state.repo, attr), attr
