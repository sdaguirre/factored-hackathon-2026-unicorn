"""Acceso a datos del prototipo. Lee un snapshot parquet (capa gold exportada).

El perfil de credito es la fila de gold customer_credit_profile (USD, politica 0.4) del cliente: credit_profile.parquet,
tomado del export de gold (scripts/build_snapshot.py) o generado por el equipo (scripts/make_fixture.py). Las opciones de
oferta no se guardan: se recalculan en memoria con la politica de referencia, identicas a gold (app/policy/engine.py).

La interfaz `CustomerRepository` es el punto de reemplazo: app/data/databricks_repository.py la implementa contra
el SQL Warehouse (CHAT_REPOSITORY=databricks); el resto del sistema no cambia.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from app.policy.engine import clean_profile


@dataclass(frozen=True)
class Customer:
    customer_id: str
    document_type: str
    document_number: str
    first_name: str
    country: str
    segment: str
    customer_status: str


@dataclass(frozen=True)
class FxQuote:
    """Tasa de referencia: 1 unidad de `source` = `rate` unidades de `target`, a la fecha de corte `as_of`."""
    source: str
    target: str
    rate: float
    as_of: str


class CustomerRepository(Protocol):
    def find_by_document(self, document_number: str) -> Customer | None: ...
    def credit_profile(self, customer_id: str) -> dict: ...
    def products(self, customer_id: str) -> list[dict]: ...
    def branch_cities(self, country: str | None = None) -> list[str]: ...
    def branch_city(self, branch_id: str) -> str | None: ...
    def branch_country(self, branch_id: str) -> str | None: ...
    def branch_countries(self) -> list[str]: ...
    def profile_facts(self, customer_id: str) -> dict: ...
    def random_customer_id(self, rng) -> str: ...
    def fx_rate(self, source: str, target: str) -> FxQuote | None: ...
    def contact_email_masked(self, customer_id: str) -> str | None: ...
    def documents_on_file(self, customer_id: str) -> set[str]: ...
    def policy_versions(self) -> set[str]: ...


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
        self._by_doc = {str(r.document_number): r.customer_id for r in self._customers.itertuples()}
        self._credit = {r["customer_id"]: clean_profile(r)
                        for r in pd.read_parquet(data_dir / "credit_profile.parquet").to_dict("records")}
        self._cust = self._customers.set_index("customer_id")
        self._branch_city = dict(zip(self._branches.branch_id, self._branches.city))
        self._branch_country = dict(zip(self._branches.branch_id, self._branches.country))
        self._cities = sorted(set(self._branches.city.dropna()))
        self._cities_by_country = {c: sorted(set(g.city.dropna())) for c, g in self._branches.groupby("country")}
        self._prod_by_c = {k: g for k, g in self._products.groupby("customer_id")}
        # Tasas de cambio: las del perfil de gold (fx_to_usd y fx_date por moneda local), las mismas con que gold calculo
        # las ofertas. Monedas fuera de esas (y de USD): el agente avisa que no puede convertirlas.
        self._fx_usd: dict[str, tuple[float, str]] = {"USD": (1.0, "")}
        for p in self._credit.values():
            if p.get("local_currency") and p.get("fx_to_usd"):
                self._fx_usd.setdefault(p["local_currency"], (float(p["fx_to_usd"]), str(p.get("fx_date") or "")[:10]))

    def find_by_document(self, document_number: str) -> Customer | None:
        cid = self._by_doc.get(str(document_number).strip())
        if cid is None:
            return None
        r = self._cust.loc[cid]
        return Customer(cid, r.document_type, str(r.document_number), r.first_name, r.country,
                        r.segment, r.customer_status)

    def policy_versions(self) -> set[str]:
        """Versiones de politica con que gold calculo los perfiles cargados (deben ser la de data/reference)."""
        return {str(p.get("policy_version")) for p in self._credit.values()}

    def credit_profile(self, customer_id: str) -> dict:
        """Fila de gold customer_credit_profile (montos en USD). Incluye accepts_marketing: el consentimiento SOLO gobierna
        las ofertas proactivas (offer_mode), nunca la respuesta a una solicitud del cliente."""
        return dict(self._credit[customer_id])

    def products(self, customer_id: str) -> list[dict]:
        g = self._prod_by_c.get(customer_id)
        return [] if g is None else g.to_dict("records")

    def branch_cities(self, country: str | None = None) -> list[str]:
        """Ciudades con sucursal; con `country`, solo las de ese pais (los distractores de una pregunta deben salir de ahi)."""
        return self._cities if country is None else list(self._cities_by_country.get(country, []))

    def branch_city(self, branch_id: str) -> str | None:
        return self._branch_city.get(branch_id)

    def branch_country(self, branch_id: str) -> str | None:
        return self._branch_country.get(branch_id)

    def branch_countries(self) -> list[str]:
        return sorted(self._cities_by_country)

    def profile_facts(self, customer_id: str) -> dict:
        """Datos de perfil que usan las preguntas de seguridad: ocupacion registrada y ano de alta (None si no hay)."""
        r = self._cust.loc[customer_id]
        occupation = _clean(r.occupation) if "occupation" in self._cust.columns else None
        reg = _clean(r.registration_date) if "registration_date" in self._cust.columns else None
        try:
            year = int(pd.Timestamp(reg).year) if reg is not None else None
        except (ValueError, TypeError):
            year = None
        return {"occupation": str(occupation) if occupation else None, "registration_year": year}

    def random_customer_id(self, rng) -> str:
        """Un cliente cualquiera, para que el reto senuelo tenga la misma forma que los reales."""
        return self._customers.customer_id.iloc[rng.randrange(len(self._customers))]

    def fx_rate(self, source: str, target: str) -> FxQuote | None:
        """Tasa source -> target con el fx_to_usd de gold (1 unidad = fx_to_usd USD). None si alguna moneda no esta."""
        if source == target:
            return FxQuote(source, target, 1.0, "")
        a, b = self._fx_usd.get(source), self._fx_usd.get(target)
        if not a or not b:
            return None
        return FxQuote(source, target, a[0] / b[0], max(a[1], b[1]))

    def contact_email_masked(self, customer_id: str) -> str | None:
        """Correo registrado, enmascarado (j***@dominio). La direccion completa nunca sale de esta capa."""
        if "email" not in self._cust.columns:
            return None
        email = _clean(self._cust.loc[customer_id].email)
        if not email or "@" not in str(email):
            return None
        local, domain = str(email).split("@", 1)
        return f"{local[:1]}***@{domain}"

    def documents_on_file(self, customer_id: str) -> set[str]:
        """Documentos del cliente que el banco ya tiene. Sin la columna, solo la identidad (verificada en el chat)."""
        if "docs_on_file" not in self._cust.columns:
            return {"id_copy"}
        raw = _clean(self._cust.loc[customer_id].docs_on_file)
        return {d for d in str(raw).split(",") if d} if raw else set()

    def sample_documents(self, n: int = 5) -> list[str]:
        """Solo para pruebas y README (datos sinteticos)."""
        return list(self._by_doc)[:n]
