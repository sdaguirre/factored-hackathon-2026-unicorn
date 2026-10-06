"""Repositorio directo a Databricks y escritura de ofertas, con un SQL Warehouse falso (sin red ni credenciales)."""
from __future__ import annotations

import random
import time
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.offers import OfferStore
from app.data import offers_sql
from app.data.databricks_repository import (DatabricksOfferSink, DatabricksRepository, SqlWarehouse, _typed, docs_on_file,
                                            valid_schema)
from app.errors import DataUnavailable
from tests.conftest import make_settings

SILVER, GOLD = "w.silver", "w.gold"
PROFILE = {"customer_id": "CLI-1", "country": "México", "local_currency": "MXN", "fx_to_usd": 0.05, "fx_date": date(2026, 6, 17),
           "policy_version": "0.4", "is_eligible": True, "reason_codes": ["R02_SHORT_TENURE"], "credit_score": 700}


def crow(cid, doc, product=None, **kw):
    base = {"customer_id": cid, "document_type": "CC", "document_number": doc, "first_name": "Ana", "country": "Mexico",
            "segment": "Basic", "customer_status": "Active", "occupation": "Engineer", "registration_date": date(2021, 3, 4),
            "email": "ana@example.com", "product_id": None, "product_type": None, "product_number": None, "currency": None,
            "opening_date": None, "opening_branch_id": None, "opening_channel": None, "product_status": None}
    base.update(kw)
    if product:
        base.update(product_id=product, product_type="Cuenta Ahorro", product_number="1234567890", currency="MXN",
                    opening_date=date(2020, 1, 2), opening_branch_id="B1", opening_channel="Branch", product_status="Active")
    return base


class FakeSql:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.down = False

    def query(self, sql, params=None):
        self.calls.append((sql, params))
        if self.down:
            raise DataUnavailable("caido")
        if f"{SILVER}.branches" in sql:
            return [{"branch_id": "B1", "city": "Guadalajara", "country": "Mexico"},
                    {"branch_id": "B2", "city": "Monterrey", "country": "Mexico"},
                    {"branch_id": "B3", "city": "Bogota", "country": "Colombia"}]
        if "SELECT DISTINCT policy_version" in sql:
            return [{"policy_version": "0.4"}]
        if "GROUP BY local_currency" in sql:
            return [{"local_currency": "MXN", "fx": 0.05, "d": date(2026, 6, 17)},
                    {"local_currency": "COP", "fx": 0.00025, "d": date(2026, 6, 17)}]
        if "ORDER BY rand()" in sql:
            return [crow("CLI-D1", "999", "P-D1"), crow("CLI-D2", "998", "P-D2")]
        if "c.document_number = :doc" in sql:
            return [crow("CLI-1", "123", "P1"), crow("CLI-1", "123", "P2")] if params["doc"] == "123" else []
        if "c.customer_id = :cid" in sql:
            return [crow(params["cid"], "123", "P1")]
        if "customer_credit_profile WHERE customer_id" in sql:
            return [dict(PROFILE)] if params["cid"] == "CLI-1" else []
        raise AssertionError(sql)


@pytest.fixture()
def fake():
    return FakeSql()


@pytest.fixture()
def repo(fake):
    return DatabricksRepository(fake, SILVER, GOLD, ttl_s=300, decoy_pool=2)


def test_startup_loads_static_data_once(repo, fake):
    assert repo.policy_versions() == {"0.4"}
    assert repo.branch_countries() == ["Colombia", "México"]               # "Mexico" se normaliza como en el snapshot
    assert repo.branch_cities("México") == ["Guadalajara", "Monterrey"] and repo.branch_city("B3") == "Bogota"
    assert repo.fx_rate("MXN", "USD").rate == 0.05 and repo.fx_rate("MXN", "COP").rate == pytest.approx(200)
    assert repo.fx_rate("MXN", "BRL") is None
    n = len(fake.calls)
    repo.branch_cities(), repo.fx_rate("COP", "USD"), repo.policy_versions()
    assert len(fake.calls) == n                                              # nada de eso vuelve a consultar


def test_one_lookup_brings_customer_facts_and_active_products(repo, fake):
    n = len(fake.calls)
    c = repo.find_by_document(" 123 ")
    assert (c.customer_id, c.country, c.first_name) == ("CLI-1", "México", "Ana")
    assert repo.profile_facts("CLI-1") == {"occupation": "Engineer", "registration_year": 2021}
    assert [p["product_id"] for p in repo.products("CLI-1")] == ["P1", "P2"] and repo.products("CLI-1")[0]["last4"] == "7890"
    assert repo.contact_email_masked("CLI-1") == "a***@example.com"
    assert len(fake.calls) == n + 1                                          # una consulta, no cuatro
    assert fake.calls[-1][1] == {"doc": "123"}                               # valor como parametro, nunca en el SQL
    assert "123" not in fake.calls[-1][0]


def test_unknown_document_is_none_and_unknown_profile_raises(repo):
    assert repo.find_by_document("0") is None
    with pytest.raises(KeyError):
        repo.credit_profile("CLI-X")


def test_decoy_customers_are_preloaded_so_a_decoy_costs_no_query(repo, fake):
    n = len(fake.calls)
    cid = repo.random_customer_id(random.Random(1))
    assert cid in {"CLI-D1", "CLI-D2"}
    assert repo.profile_facts(cid)["occupation"] == "Engineer" and repo.products(cid)
    assert len(fake.calls) == n


def test_per_customer_data_expires_but_decoys_do_not(fake):
    repo = DatabricksRepository(fake, SILVER, GOLD, ttl_s=0, decoy_pool=2)
    repo.find_by_document("123")
    n = len(fake.calls)
    repo.products("CLI-1")                                                   # ttl 0: se vuelve a leer
    assert len(fake.calls) == n + 1
    repo.products("CLI-D1")                                                  # los senuelos son fijos
    assert len(fake.calls) == n + 1


def test_credit_profile_is_cached_and_returned_as_a_copy(repo, fake):
    p = repo.credit_profile("CLI-1")
    p["is_eligible"] = False
    n = len(fake.calls)
    assert repo.credit_profile("CLI-1")["is_eligible"] is True and len(fake.calls) == n


def test_documents_on_file_are_deterministic_and_always_include_identity():
    assert docs_on_file("CLI-1") == docs_on_file("CLI-1") and "id_copy" in docs_on_file("CLI-1")
    shares = [docs_on_file(f"CLI-{i}") for i in range(2000)]
    assert 0.5 < sum("address_proof" in d for d in shares) / 2000 < 0.7        # ~60 %
    assert 0.15 < sum("income_proof" in d for d in shares) / 2000 < 0.35       # ~25 %


def test_schema_names_are_validated_before_they_reach_the_sql():
    assert valid_schema("workspace.gold_latam_bank") == "workspace.gold_latam_bank"
    for bad in ("gold", "a.b; DROP TABLE x", "a.b.c", "a. b"):
        with pytest.raises(ValueError):
            valid_schema(bad)


# ---------------------------------------------------------------- cliente SQL

def test_statement_values_are_typed_like_the_gold_parquet():
    assert _typed("12", "INT") == 12 and _typed("1.5", "DOUBLE") == 1.5 and _typed("true", "BOOLEAN") is True
    assert _typed("2026-06-17", "DATE") == date(2026, 6, 17) and _typed('["a","b"]', "ARRAY") == ["a", "b"]
    assert _typed("2026-10-05T06:13:51.425Z", "TIMESTAMP").year == 2026 and _typed(None, "INT") is None


class FakeStatements:
    def __init__(self, states, chunks=()):
        self.states, self.chunks, self.sent = list(states), list(chunks), None

    def _resp(self, state):
        cols = [SimpleNamespace(name="n", type_name=SimpleNamespace(value="INT")),
                SimpleNamespace(name="flag", type_name=SimpleNamespace(value="BOOLEAN"))]
        return SimpleNamespace(statement_id="S", status=SimpleNamespace(state=state, error=None),
                               manifest=SimpleNamespace(schema=SimpleNamespace(columns=cols)),
                               result=SimpleNamespace(data_array=[["1", "true"]], next_chunk_index=1 if self.chunks else None))

    def execute_statement(self, **kw):
        self.sent = kw
        return self._resp(self.states.pop(0))

    def get_statement(self, _):
        return self._resp(self.states.pop(0))

    def get_statement_result_chunk_n(self, _, i):
        data = self.chunks.pop(0)
        return SimpleNamespace(data_array=data, next_chunk_index=None)

    def cancel_execution(self, _):
        pass


def _sql(statements, timeout=5):
    return SqlWarehouse("WH", timeout_s=timeout, client=SimpleNamespace(statement_execution=statements))


def test_query_returns_typed_rows_follows_chunks_and_sends_only_used_parameters():
    from databricks.sdk.service.sql import StatementState

    st = FakeStatements([StatementState.SUCCEEDED], chunks=[[["2", "false"]]])
    rows = _sql(st).query("SELECT * FROM t WHERE a = :a", {"a": "x", "unused": "y"})
    assert rows == [{"n": 1, "flag": True}, {"n": 2, "flag": False}]
    assert [(p.name, p.value) for p in st.sent["parameters"]] == [("a", "x")] and st.sent["warehouse_id"] == "WH"


def test_query_failures_and_timeouts_become_data_unavailable(monkeypatch):
    from databricks.sdk.service.sql import StatementState

    with pytest.raises(DataUnavailable):
        _sql(FakeStatements([StatementState.FAILED])).query("SELECT 1")
    monkeypatch.setattr(time, "sleep", lambda _: None)
    with pytest.raises(DataUnavailable):
        _sql(FakeStatements([StatementState.RUNNING] * 50), timeout=0).query("SELECT 1")

    class Boom:
        def execute_statement(self, **_):
            raise ConnectionError("token abc123")

    with pytest.raises(DataUnavailable) as e:
        _sql(Boom()).query("SELECT 1")
    assert "abc123" not in str(e.value)                                      # el detalle del SDK no se propaga


# ---------------------------------------------------------------- ofertas

ROW = {c: None for c, _ in offers_sql.COLUMNS} | {"offer_id": "O1", "status": "handed_off", "flags": [], "is_conditional": False}


def test_sink_writes_in_order_retries_and_never_blocks_the_caller():
    sql = FakeSql()
    sink = DatabricksOfferSink(sql, GOLD, retry_wait_s=0)
    seen, fail_once = [], {"n": 1}

    def query(statement, params):
        if fail_once["n"]:
            fail_once["n"] -= 1
            raise DataUnavailable("choque")
        seen.append(dict(params)["status"])

    sql.query = query
    sink({**ROW, "status": "accepted"}), sink({**ROW, "status": "handed_off"})
    sink.flush()
    assert seen == ["accepted", "handed_off"]                                # reintento y orden
    assert "MERGE INTO w.gold.credit_offers" in sink._merge


def test_sink_failure_is_logged_not_raised_and_the_jsonl_keeps_the_offer(tmp_path, caplog):
    sql = FakeSql()
    sql.down = True
    sink = DatabricksOfferSink(sql, GOLD, retry_wait_s=0)
    store = OfferStore(tmp_path / "o.jsonl", sink)
    store.save(ROW)
    sink.flush()
    assert store.latest()["O1"]["status"] == "handed_off"
    assert "offer_sync_failed" in caplog.text and "O1" in caplog.text


def test_a_failing_sink_does_not_break_save(tmp_path):
    def boom(_):
        raise RuntimeError("x")

    store = OfferStore(tmp_path / "o.jsonl", boom)
    store.save(ROW)
    assert "O1" in store.latest()


# ---------------------------------------------------------------- configuracion y API

def test_databricks_repository_needs_a_warehouse():
    with pytest.raises(ValueError):
        make_settings(repository="databricks")
    assert make_settings(repository="databricks", databricks_warehouse_id="W").data_source == "databricks"


def test_unreachable_data_source_is_a_503_not_a_500(app):
    def down(_):
        raise DataUnavailable("x")

    app.state.ctx.repo.find_by_document = down
    r = TestClient(app, raise_server_exceptions=False).post("/v1/sessions", json={"document_number": "12345678", "language": "es"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "DATA_UNAVAILABLE"
