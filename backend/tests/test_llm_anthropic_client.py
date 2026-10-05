"""AnthropicLLM against a fake Anthropic client (no network, no API key).

The fake client answers like the real SDK (content[0].text, usage.*_tokens) with scripted outputs or errors. These are
tests of the integration code (prompt, JSON parsing, validation, fallbacks), not measurements of the model: the
scripted outputs are test data and are never reported as results.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pydantic import ValidationError

from app.agent.llm_anthropic import SYSTEM_COMPOSE, SYSTEM_NLU, SYSTEM_SUMMARY, AnthropicLLM
from tests.conftest import customers_by_offer_profile, login

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def reply(text: str, tokens_in: int = 1100, tokens_out: int = 60):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out))


class FakeMessages:
    """Routes by system prompt to a script per task; a script item is a text, a callable(user) or an exception."""

    def __init__(self, nlu=(), compose=(), summary=()):
        self.scripts = {SYSTEM_NLU: list(nlu), SYSTEM_COMPOSE: list(compose), SYSTEM_SUMMARY: list(summary)}
        self.calls = []

    def create(self, *, model, max_tokens, system, messages):
        self.calls.append({"model": model, "max_tokens": max_tokens, "system": system, "messages": messages})
        script = self.scripts[system]
        if not script:
            raise AssertionError("unexpected call: no scripted answer left for this prompt")
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return reply(item(messages[0]["content"]) if callable(item) else item)


def fake_llm(**scripts) -> tuple[AnthropicLLM, FakeMessages]:
    llm = object.__new__(AnthropicLLM)                  # no SDK client, no key
    msgs = FakeMessages(**scripts)
    llm._client, llm._model = SimpleNamespace(messages=msgs), "claude-haiku-4-5-20251001"
    return llm, msgs


def nlu_json(**fields) -> str:
    return json.dumps({"intent": "unknown", "language": "es", "product": None, "amount": None, "months": None,
                       "declared_income": None, "sentiment": "neutral", "sensitive_topic": False, "confidence": 0.9,
                       **fields})


# ------------------------------------------------------------------ extract: request and parsing
def test_extract_sends_the_nlu_prompt_with_the_message_inside_user_message():
    llm, msgs = fake_llm(nlu=[nlu_json(intent="credit_offers")])
    llm.extract("¿cuánto me prestan?", "es")
    call = msgs.calls[0]
    assert call["system"] == SYSTEM_NLU and call["max_tokens"] == 200 and call["model"].startswith("claude-")
    assert call["messages"] == [{"role": "user", "content": "<user_message>¿cuánto me prestan?</user_message>"}]


def test_extract_adds_the_pending_question_note_only_when_a_yes_no_question_is_pending():
    llm, msgs = fake_llm(nlu=[nlu_json(intent="confirm_yes"), nlu_json(intent="greeting")])
    llm.extract("sí", "es", yes_no_pending=True)
    llm.extract("hola", "es")
    assert "pregunta de si/no pendiente" in msgs.calls[0]["messages"][0]["content"]
    assert "pendiente" not in msgs.calls[1]["messages"][0]["content"]


def test_extract_parses_json_wrapped_in_prose():
    llm, _ = fake_llm(nlu=["Claro, aquí está:\n" + nlu_json(intent="credit_eligibility", product="mortgage",
                                                            amount=90000, months=240) + "\nEspero que ayude."])
    r = llm.extract("quiero una hipoteca de 90 mil a 20 años", "es")
    assert (r.intent, r.product, r.amount, r.months) == ("credit_eligibility", "mortgage", 90000, 240)


@pytest.mark.parametrize("bad", [
    "no puedo ayudar con eso",                                  # no JSON at all
    '{"intent": "credit_offers", ',                             # truncated JSON
    nlu_json(intent="approve_credit"),                          # intent outside the taxonomy
    nlu_json(intent="credit_eligibility", amount=-500),         # amount out of range
    nlu_json(intent="update_income", declared_income=0),        # income out of range
    nlu_json(intent="credit_eligibility", months=900),          # term out of range
])
def test_extract_rejects_malformed_or_out_of_range_output(bad):
    llm, _ = fake_llm(nlu=[bad])
    with pytest.raises((ValueError, ValidationError)):
        llm.extract("x", "es")


def test_extract_ignores_fields_the_model_invents():
    llm, _ = fake_llm(nlu=[nlu_json(intent="greeting") [:-1] + ', "approved": true, "customer_id": "CLI-1"}'])
    r = llm.extract("hola", "es")
    assert r.intent == "greeting" and not hasattr(r, "approved") and not hasattr(r, "customer_id")


# ------------------------------------------------------------------ compose and summarize
def test_compose_returns_the_trimmed_text_or_none_when_empty():
    llm, msgs = fake_llm(compose=["  Con gusto le ayudo.  ", "   "])
    assert llm.compose({"kind": "greeting"}, "es", "Hola, ¿en qué le ayudo?") == "Con gusto le ayudo."
    assert llm.compose({"kind": "greeting"}, "pt", "Olá") is None
    assert msgs.calls[0]["system"] == SYSTEM_COMPOSE and "Idioma: es" in msgs.calls[0]["messages"][0]["content"]


# ------------------------------------------------------------------ through the chat: failures fall back to rules
def _api_errors():
    return [anthropic.APITimeoutError(request=REQUEST),
            anthropic.APIConnectionError(request=REQUEST),
            anthropic.InternalServerError("overloaded", response=httpx.Response(529, request=REQUEST), body=None),
            anthropic.RateLimitError("rate limited", response=httpx.Response(429, request=REQUEST), body=None)]


@pytest.mark.parametrize("failure", _api_errors() + ["sin json", nlu_json(intent="approve_credit")],
                         ids=["timeout", "connection", "overloaded_529", "rate_limit_429", "no_json", "bad_intent"])
def test_any_nlu_failure_falls_back_to_the_rules_and_the_chat_still_answers(client, state, failure):
    sid, h = login(client, state, customers_by_offer_profile(state)["consent_pre"][0]["doc"])
    llm, msgs = fake_llm(nlu=[failure], compose=[lambda user: None] * 5, summary=["Resumen."] * 2)
    state.orchestrator.llm = llm
    r = client.post(f"/v1/sessions/{sid}/messages", json={"message": "quiero hablar con un asesor"}, headers=h)
    assert r.status_code == 200 and r.json()["handoff_ticket"]          # rules understood the explicit request
    assert msgs.calls[0]["system"] == SYSTEM_NLU


def test_a_model_rewrite_with_new_numbers_never_reaches_the_customer(client, state):
    from tests.conftest import offer_amount

    c = next(c for c in customers_by_offer_profile(state)["consent_pre"] if offer_amount(state, c["cid"]))
    sid, h = login(client, state, c["doc"])
    amount, months = offer_amount(state, c["cid"], share=0.5)
    llm, _ = fake_llm(nlu=[nlu_json(intent="credit_eligibility", product="personal_loan", amount=float(int(amount)),
                                    months=months)],
                      compose=[lambda user: user + " Además le aprobamos 999999 sin verificar."] * 5,
                      summary=["Resumen."] * 2)
    state.orchestrator.llm = llm
    r = client.post(f"/v1/sessions/{sid}/messages", json={"message": f"quiero un préstamo de {int(amount)} a {months} meses"},
                    headers=h).json()
    assert r["outcome"] == "eligible" and "999999" not in r["reply"]
