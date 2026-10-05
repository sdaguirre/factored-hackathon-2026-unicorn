"""Politica 0.4 en el backend (app/policy/engine.py sobre la implementacion de referencia de data/policy).

Casos sobre el conjunto de ejemplo del equipo (backend/data/fixture, sin datos del organizador) y perfiles sinteticos. La
paridad completa con gold (1.800.000 opciones) la prueba data/scripts/check_engine_parity.py --engine
app.policy.engine:offer_options --pythonpath backend.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.agent.tools import ToolContext, accept_offer, get_offers, recalculate_offer, to_local, to_usd
from app.config import BASE_DIR
from app.core.offers import OfferStore
from app.data.repository import SnapshotRepository
from app.policy import engine as eng

POLICY = eng.load_policy()
FIXTURE = BASE_DIR / "data" / "fixture"


@pytest.fixture(scope="module")
def repo():
    return SnapshotRepository(FIXTURE)


def profile(repo, cid, **kw):
    return {**repo.credit_profile(cid), **kw}


def synthetic(**kw):
    """Perfil elegible banda A, segmento Basic, sin deudas: 10.000 USD de ingreso (2.000 USD de cuota disponible)."""
    base = dict(customer_id="SYN-1", customer_status="Active", risk_band="A", segment="Basic", local_currency="MXN",
                fx_to_usd=0.05, fx_date="2026-06-30", as_of_date="2026-06-30", income_used_usd=10_000.0,
                income_source="declared_profile", current_installments_usd=0.0, max_total_installment_usd=2_000.0,
                available_installment_usd=2_000.0, total_rate_adjustment_pp=-2.0, max_term_personal_loan_months=60,
                max_term_mortgage_months=360, reason_codes=[], is_eligible=True, offer_mode="proactive",
                open_complaints=0, open_priority_complaints=0, open_critical_complaints=0, accepts_marketing=True)
    base.update(kw)
    return base


# ---------------------------------------------------------------- referencia y parametros
def test_policy_is_the_reference_version_with_a_hard_20_percent_limit():
    assert POLICY.version == "0.4" and POLICY.max_dti == 0.20
    assert POLICY.params["max_age_at_maturity_years"] == "75"
    assert [b["band"] for b in POLICY.bands] == ["A", "B", "C", "D", "E"]


def test_a_profile_from_another_policy_version_stops_the_app_outside_dev(tmp_path):
    import shutil

    import pandas as pd

    from app.main import create_app
    from tests.conftest import make_settings

    for f in FIXTURE.glob("*.parquet"):
        shutil.copy(f, tmp_path / f.name)
    prof = pd.read_parquet(tmp_path / "credit_profile.parquet")
    prof["policy_version"] = "0.3"                               # export viejo: max_term_* sin tope por edad
    prof.to_parquet(tmp_path / "credit_profile.parquet", index=False)
    with pytest.raises(RuntimeError, match="0.3"):
        create_app(make_settings(env="prod", data_dir=tmp_path, admin_api_keys="adm"))
    import logging

    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logging.getLogger("chat").addHandler(handler)              # el logger de la app no propaga a caplog
    try:
        create_app(make_settings(env="dev", data_dir=tmp_path))  # en dev arranca y avisa
    finally:
        logging.getLogger("chat").removeHandler(handler)
    assert any("export_gold" in r.getMessage() for r in records)


def test_backend_options_are_the_reference_options(repo):
    for cid in ("FXC-001", "FXC-004", "FXC-012", "FXC-008"):
        p = repo.credit_profile(cid)
        assert eng.offer_options(p, POLICY) == eng.ref.offer_options(p, POLICY)


def test_the_featured_option_is_the_highest_and_is_proposed_without_an_amount(repo):
    p = repo.credit_profile("FXC-001")
    options = eng.offer_options(p, POLICY)
    star = eng.featured(options)
    pl = [o for o in options if o["product_code"] == "PL" and o["is_available"]]
    assert star["PL"]["offer_max_amount_usd"] == max(o["offer_max_amount_usd"] for o in pl)
    q = eng.evaluate(p, POLICY, "personal_loan")
    assert q.outcome == eng.ELIGIBLE and q.amount_usd == star["PL"]["offer_max_amount_usd"]
    assert q.term_months == star["PL"]["term_months"] and q.dti_after <= POLICY.max_dti + 1e-9


# ---------------------------------------------------------------- tasa, plazos por banda y por edad
def test_rate_is_grid_rate_plus_band_and_segment_clamped_to_the_product_range():
    q = eng.evaluate(synthetic(), POLICY, "personal_loan", 20_000, 36)
    grid = next(g for g in POLICY.grid if g["option_code"] == "PL" and g["term_months"] == 36)
    assert q.rate_pct == round(max(grid["reference_rate_pct"] - 2.0, POLICY.catalog["PL"]["min_rate_pct"]), 2)
    q2 = eng.evaluate(synthetic(total_rate_adjustment_pp=-30.0), POLICY, "personal_loan", 20_000, 36)
    assert q2.rate_pct == POLICY.catalog["PL"]["min_rate_pct"]


def test_band_c_caps_personal_loans_at_48_months(repo):
    p = repo.credit_profile("FXC-004")
    assert p["risk_band"] == "C"
    q = eng.evaluate(p, POLICY, "personal_loan", None, 60)
    assert q.outcome == eng.UNAVAILABLE and q.reasons == ["TERM_ABOVE_BAND_MAXIMUM"] and max(q.allowed_terms) == 48
    assert eng.evaluate(p, POLICY, "personal_loan", None, 48).outcome == eng.ELIGIBLE


def test_age_at_maturity_is_told_apart_from_the_band_limit(repo):
    """FXC-012 es banda B (hipotecas hasta 360 meses), pero la edad las limita a 200: 180 si, 240 no por edad."""
    p = repo.credit_profile("FXC-012")
    assert p["risk_band"] == "B" and p["max_term_mortgage_months"] == 200
    q = eng.evaluate(p, POLICY, "mortgage", None, 240)
    assert q.outcome == eng.UNAVAILABLE and q.reasons == ["TERM_ABOVE_AGE_AT_MATURITY"] and q.allowed_terms == [180]
    assert eng.evaluate(p, POLICY, "mortgage", None, 180).outcome == eng.ELIGIBLE


def test_no_mortgage_at_all_because_of_age_keeps_the_card():
    p = synthetic(max_term_mortgage_months=100, max_term_personal_loan_months=36)
    q = eng.evaluate(p, POLICY, "mortgage")
    assert q.outcome == eng.UNAVAILABLE and q.reasons == ["PRODUCT_ABOVE_AGE_AT_MATURITY"]
    assert eng.evaluate(p, POLICY, "credit_card").outcome == eng.ELIGIBLE
    assert eng.evaluate(p, POLICY, "personal_loan", None, 48).reasons == ["TERM_ABOVE_AGE_AT_MATURITY"]


def test_a_term_outside_the_grid_lists_the_terms_on_offer():
    q = eng.evaluate(synthetic(), POLICY, "personal_loan", 20_000, 30)
    assert q.outcome == eng.UNAVAILABLE and q.reasons == ["TERM_NOT_IN_GRID"] and q.allowed_terms == [24, 36, 48, 60]


# ---------------------------------------------------------------- monto y limite del 20% (sin margen)
def test_the_20_percent_limit_has_no_borderline_margin():
    p = synthetic(income_used_usd=2_000.0, max_total_installment_usd=400.0, available_installment_usd=400.0)
    top = eng.evaluate(p, POLICY, "personal_loan", None, 36)
    assert top.outcome == eng.ELIGIBLE and top.dti_after <= 0.20
    over = eng.evaluate(p, POLICY, "personal_loan", top.max_amount_usd * 1.05, 36)    # 5% sobre el maximo: antes, revision
    assert over.outcome == eng.DECLINED and over.reasons == ["DTI_EXCEEDED"] and 0.20 < over.dti_after < 0.22


def test_amount_above_the_product_maximum_is_not_a_capacity_problem():
    q = eng.evaluate(synthetic(income_used_usd=100_000.0, available_installment_usd=20_000.0), POLICY, "personal_loan",
                     200_000, 60)
    assert q.outcome == eng.UNAVAILABLE and q.reasons == ["ABOVE_MAX_AMOUNT"] and q.max_amount_usd == 150_000


def test_amount_below_the_product_minimum():
    q = eng.evaluate(synthetic(), POLICY, "personal_loan", 1_000, 36)
    assert q.outcome == eng.UNAVAILABLE and q.reasons == ["BELOW_MIN_AMOUNT"] and q.min_amount_usd == 5_000


def test_no_capacity_for_the_minimum_at_one_term_suggests_the_others():
    p = synthetic(income_used_usd=600.0, available_installment_usd=120.0)
    q = eng.evaluate(p, POLICY, "personal_loan", None, 24)
    assert q.outcome == eng.UNAVAILABLE and q.reasons == ["NO_CAPACITY_FOR_OPTION"] and 24 not in q.allowed_terms


# ---------------------------------------------------------------- tarjeta por nivel
def test_credit_card_is_offered_by_tier_and_the_amount_picks_the_tier():
    p = synthetic()
    star = eng.evaluate(p, POLICY, "credit_card")
    assert star.outcome == eng.ELIGIBLE and star.option["tier"] == "Black" and star.term_months == 60
    gold = eng.evaluate(p, POLICY, "credit_card", 25_000)
    assert gold.outcome == eng.ELIGIBLE and gold.option["tier"] == "Gold" and gold.amount_usd == 25_000
    assert eng.evaluate(p, POLICY, "credit_card", 500).reasons == ["BELOW_MIN_AMOUNT"]


# ---------------------------------------------------------------- filtros y datos declarados
def test_hard_filters_from_bank_data_do_not_change_with_declared_income(repo):
    p = repo.credit_profile("FXC-006")                     # mora de 120 dias: R03
    q = eng.evaluate(p, POLICY, "personal_loan", None, None, {"declared_income_usd": 50_000.0})
    assert q.outcome == eng.DECLINED and q.reasons == ["R03_DELINQUENCY"]


def test_missing_income_asks_for_it_and_a_declared_income_makes_a_conditional_offer(repo):
    p = repo.credit_profile("FXC-005")
    assert eng.evaluate(p, POLICY, "personal_loan").outcome == eng.NEEDS_DATA
    q = eng.evaluate(p, POLICY, "personal_loan", None, None, {"declared_income_usd": 4_000.0})
    assert q.outcome == eng.ELIGIBLE_PROVISIONAL and "F03_DECLARED_DATA" in q.flags and q.conditional
    assert q.profile["income_source"] == "declared_in_chat"


def test_household_income_comes_with_household_debt():
    p = synthetic(income_used_usd=2_000.0, available_installment_usd=400.0)
    q = eng.evaluate(p, POLICY, "personal_loan", None, 36, {"additional_income_usd": 1_000.0,
                                                           "external_installments_usd": 100.0})
    assert q.profile["income_used_usd"] == 3_000.0 and q.profile["current_installments_usd"] == 100.0
    assert q.profile["available_installment_usd"] == pytest.approx(0.2 * 3_000 - 100)
    assert q.outcome == eng.ELIGIBLE_PROVISIONAL and "F03_DECLARED_DATA" in q.flags


def test_open_complaints_are_flagged_for_the_advisor(repo):
    q = eng.evaluate(repo.credit_profile("FXC-002"), POLICY, "personal_loan")
    assert "F04_OPEN_COMPLAINTS" in q.flags


# ---------------------------------------------------------------- aceptacion y credit_offers
def test_accepting_near_the_limit_on_declared_income_raises_f02(repo, tmp_path):
    store = OfferStore(tmp_path / "offers.jsonl")
    ctx = ToolContext("FXC-005", repo, POLICY, store)
    declared = {"income": 70_000.0}                                   # MXN, sin ingreso registrado
    q = recalculate_offer(ctx, "personal_loan", None, 36, declared, record=False)
    row = accept_offer(ctx, product="personal_loan", amount_local=to_local(q.profile, q.max_amount_usd, floor=True),
                       months=36, declared_local=declared, offer_origin="customer_interest", session_id="S1",
                       language="es", required_documents=["id_copy"])
    assert row["flags"][:2] == ["F02_NEAR_LIMIT_DECLARED_INCOME", "F03_DECLARED_DATA"] and row["is_conditional"]
    assert row["debt_to_income_after"] <= 0.20 and row["policy_version"] == "0.4" and row["status"] == "accepted"
    assert json.loads(row["customer_declared_data"]) == {"declared_income_usd": round(70_000 * q.profile["fx_to_usd"], 2)}
    saved = store.latest()[row["offer_id"]]
    assert saved["amount_usd"] == row["amount_usd"] and saved["valid_until"] == str(row["valid_until"])


def test_an_offer_outside_the_option_is_never_recorded(repo, tmp_path):
    store = OfferStore(tmp_path / "offers.jsonl")
    ctx = ToolContext("FXC-001", repo, POLICY, store)
    big = to_local(repo.credit_profile("FXC-001"), 1_000_000)
    with pytest.raises(ValueError):
        accept_offer(ctx, product="personal_loan", amount_local=big, months=36, declared_local=None,
                     offer_origin="customer_interest", session_id="S1", language="es", required_documents=[])
    assert store.latest() == {}


def test_the_last_row_of_an_offer_wins(tmp_path):
    store = OfferStore(tmp_path / "offers.jsonl")
    row = {"offer_id": "o1", "handoff_ticket_id": None, "created_at": datetime(2026, 10, 5, tzinfo=timezone.utc)}
    store.save(row)
    store.save({**row, "handoff_ticket_id": "HND-1"})
    assert store.latest()["o1"]["handoff_ticket_id"] == "HND-1"


# ---------------------------------------------------------------- monedas y oferta proactiva
def test_local_amounts_round_trip_and_maximums_are_floored(repo):
    p = repo.credit_profile("FXC-001")
    assert to_usd(p, to_local(p, 1234.5)) == pytest.approx(1234.5)
    top = to_local(p, 33_900, floor=True)
    assert top == int(top) and to_usd(p, top) <= 33_900


def test_only_proactive_profiles_get_offers_without_asking(repo):
    modes = {cid: repo.credit_profile(cid)["offer_mode"] for cid in ("FXC-001", "FXC-002", "FXC-003", "FXC-005")}
    assert modes == {"FXC-001": "proactive", "FXC-002": "on_customer_interest", "FXC-003": "on_customer_interest",
                     "FXC-005": "none"}
    offers = get_offers(ToolContext("FXC-001", repo, POLICY), record=False)
    assert [o["product_code"] for o in offers.ordered] == ["PL", "CC", "MG"]


def test_offer_rows_have_exactly_the_columns_of_gold_credit_offers(repo, tmp_path):
    """La fila que escribe la API, el MERGE de scripts/sync_credit_offers.py y la tabla de Databricks coinciden."""
    import re
    import sys

    sys.path.insert(0, str(BASE_DIR / "scripts"))
    from sync_credit_offers import COLUMNS, latest_rows, parameters

    ddl = (BASE_DIR.parent / "data" / "databricks" / "gold" / "00_deploy_objects.sql").read_text(encoding="utf-8")
    body = ddl[ddl.index("credit_offers') ("):ddl.index("CONSTRAINT credit_offers_pk")]
    table = re.findall(r"^\s{4}([a-z_0-9]+)\s+([A-Z]+(?:<[A-Z]+>)?)", body, flags=re.M)
    assert COLUMNS == table
    store = OfferStore(tmp_path / "offers.jsonl")
    ctx = ToolContext("FXC-001", repo, POLICY, store)
    q = recalculate_offer(ctx, "personal_loan", None, None, None, record=False)
    row = accept_offer(ctx, product="personal_loan", amount_local=to_local(q.profile, q.max_amount_usd, floor=True),
                       months=None, declared_local=None, offer_origin="proactive", session_id="S1", language="es",
                       required_documents=["id_copy"])
    assert list(row) == [c for c, _ in COLUMNS]
    store.save({**row, "status": "handed_off", "handoff_ticket_id": "HND-1"})
    (only,) = latest_rows(store.path)
    params = dict(parameters(only))
    assert params["status"] == "handed_off" and params["flags"] == "[]" and params["is_conditional"] == "false"
