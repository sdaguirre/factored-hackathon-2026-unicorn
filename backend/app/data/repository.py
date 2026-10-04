"""Acceso a datos del prototipo. Lee un snapshot parquet (capa gold exportada).

La interfaz `CustomerRepository` es el punto de reemplazo: en produccion seria un SQL Warehouse
con filtros por fila; el resto del sistema no cambia.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class Customer:
    customer_id: str
    document_type: str
    document_number: str
    first_name: str
    country: str
    segment: str
    customer_status: str


class CustomerRepository(Protocol):
    def find_by_document(self, document_number: str) -> Customer | None: ...
    def credit_profile(self, customer_id: str) -> dict: ...
    def products(self, customer_id: str) -> list[dict]: ...
    def recent_transactions(self, customer_id: str, limit: int = 40) -> list[dict]: ...
    def branch_cities(self) -> list[str]: ...
    def branch_city(self, branch_id: str) -> str | None: ...


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    return v


class SnapshotRepository:
    def __init__(self, data_dir: Path):
        self._customers = pd.read_parquet(data_dir / "customers.parquet")
        self._products = pd.read_parquet(data_dir / "products.parquet")
        self._branches = pd.read_parquet(data_dir / "branches.parquet")
        self._tx = pd.read_parquet(data_dir / "transactions.parquet")
        self._by_doc = {str(r.document_number): r.customer_id for r in self._customers.itertuples()}
        self._cust = self._customers.set_index("customer_id")
        self._branch_city = dict(zip(self._branches.branch_id, self._branches.city))
        self._cities = sorted(set(self._branches.city.dropna()))
        self._prod_by_c = {k: g for k, g in self._products.groupby("customer_id")}
        self._tx_by_c = {k: g.sort_values("transaction_date", ascending=False)
                         for k, g in self._tx.groupby("customer_id")}

    def find_by_document(self, document_number: str) -> Customer | None:
        cid = self._by_doc.get(str(document_number).strip())
        if cid is None:
            return None
        r = self._cust.loc[cid]
        return Customer(cid, r.document_type, str(r.document_number), r.first_name, r.country,
                        r.segment, r.customer_status)

    def credit_profile(self, customer_id: str) -> dict:
        r = self._cust.loc[customer_id]
        return {
            "customer_status": r.customer_status,
            "credit_score": _clean(r.credit_score),
            "monthly_income": _clean(r.monthly_income),
            "existing_monthly_debt": _clean(r.existing_monthly_debt) or 0.0,
            "max_days_past_due": int(_clean(r.max_days_past_due) or 0),
            "n_active_products": int(_clean(r.n_active_products) or 0),
            "income_ccy": r.income_ccy,
            "country": r.country,
            # Consentimiento de marketing: SOLO gobierna ofertas proactivas, nunca la respuesta a una solicitud del cliente.
            "accepts_marketing": bool(r.accepts_marketing),
        }

    def products(self, customer_id: str) -> list[dict]:
        g = self._prod_by_c.get(customer_id)
        return [] if g is None else g.to_dict("records")

    def recent_transactions(self, customer_id: str, limit: int = 40) -> list[dict]:
        g = self._tx_by_c.get(customer_id)
        return [] if g is None else g.head(limit).to_dict("records")

    def branch_cities(self) -> list[str]:
        return self._cities

    def branch_city(self, branch_id: str) -> str | None:
        return self._branch_city.get(branch_id)

    def sample_documents(self, n: int = 5) -> list[str]:
        """Solo para pruebas y README (datos sinteticos)."""
        return list(self._by_doc)[:n]
