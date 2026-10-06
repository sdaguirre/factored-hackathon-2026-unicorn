"""Acceso a datos directo a Databricks (SQL Warehouse): mismo contrato que SnapshotRepository (`CustomerRepository`).

Lee de las tablas reales: silver (customers, products, branches) y gold (customer_credit_profile). Cada consulta lleva
sus valores como parametros nombrados (nunca concatenados). La API de sentencias tarda de 1 a 3 s por consulta, asi que:
- lo estatico (sucursales, tasas de cambio, version de politica) se carga una vez al arrancar;
- la busqueda por documento trae al cliente y sus productos activos en UNA consulta (la usan las preguntas de seguridad);
- lo leido por cliente se guarda en memoria `ttl_s` segundos, no mas;
- el reto senuelo (documento inexistente) sale de un grupo de clientes cargado al arrancar, para que tarde lo mismo que
  uno real y no revele si el documento existe.
Si Databricks no responde se lanza DataUnavailable (la API contesta 503). Nunca se registran documentos ni valores.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from app.data.repository import Customer, FxQuote, _clean
from app.errors import DataUnavailable

logger = logging.getLogger("chat.databricks")

_SCHEMA = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_INTS = {"INT", "LONG", "SHORT", "BYTE"}
_FLOATS = {"DOUBLE", "FLOAT", "DECIMAL"}


def valid_schema(name: str) -> str:
    """`catalogo.esquema`: se interpola en el SQL, asi que solo letras, numeros y guion bajo."""
    if not _SCHEMA.match(name):
        raise ValueError(f"esquema invalido: {name!r} (use catalogo.esquema)")
    return name


def _typed(value: str | None, type_name: str):
    """Valor de la API de sentencias (todo llega como texto) al tipo de Python que dan los parquet de gold."""
    if value is None:
        return None
    if type_name in _INTS:
        return int(value)
    if type_name in _FLOATS:
        return float(value)
    if type_name == "BOOLEAN":
        return value == "true"
    if type_name == "DATE":
        return date.fromisoformat(value)
    if type_name == "TIMESTAMP":
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    if type_name in ("ARRAY", "STRUCT", "MAP"):
        return json.loads(value)
    return value


class SqlWarehouse:
    """Ejecuta SQL en un SQL Warehouse. Credenciales: perfil del CLI (desarrollo) o variables DATABRICKS_HOST y
    DATABRICKS_TOKEN / DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET (contenedor); el SDK las toma del entorno."""

    def __init__(self, warehouse_id: str, profile: str = "", timeout_s: int = 60, client=None):
        if not warehouse_id:
            raise ValueError("CHAT_DATABRICKS_WAREHOUSE_ID es obligatorio con CHAT_REPOSITORY=databricks")
        if client is None:
            from databricks.sdk import WorkspaceClient
            client = WorkspaceClient(profile=profile) if profile else WorkspaceClient()
        self._w = client
        self.warehouse_id = warehouse_id
        self.timeout_s = timeout_s

    def query(self, sql: str, params: dict[str, str | None] | list[tuple[str, str | None]] | None = None) -> list[dict]:
        """Filas como dicts con tipos de Python. `params`: nombre -> valor (texto) de los marcadores :nombre."""
        from databricks.sdk.service.sql import StatementParameterListItem, StatementState

        items = list(params.items()) if isinstance(params, dict) else list(params or [])
        used = [StatementParameterListItem(name=n, value=v) for n, v in items if f":{n}" in sql]
        deadline = time.monotonic() + self.timeout_s
        try:
            r = self._w.statement_execution.execute_statement(
                statement=sql, warehouse_id=self.warehouse_id, wait_timeout="30s", parameters=used or None)
            while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
                if time.monotonic() > deadline:
                    self._w.statement_execution.cancel_execution(r.statement_id)
                    raise DataUnavailable("tiempo de espera agotado")
                time.sleep(1)
                r = self._w.statement_execution.get_statement(r.statement_id)
            if r.status.state != StatementState.SUCCEEDED:
                # El mensaje del warehouse puede traer valores de la consulta: solo se registra el estado.
                raise DataUnavailable(f"consulta {r.status.state.value}")
            return self._rows(r)
        except DataUnavailable:
            raise
        except Exception as exc:   # red, autenticacion, cuota...
            raise DataUnavailable(type(exc).__name__) from exc

    def _rows(self, r) -> list[dict]:
        if r.manifest is None or r.result is None:
            return []
        cols = [(c.name, c.type_name.value if c.type_name else "STRING") for c in r.manifest.schema.columns]
        raw = list(r.result.data_array or [])
        chunk = r.result.next_chunk_index
        while chunk is not None:
            part = self._w.statement_execution.get_statement_result_chunk_n(r.statement_id, chunk)
            raw.extend(part.data_array or [])
            chunk = part.next_chunk_index
        return [{n: _typed(v, t) for (n, t), v in zip(cols, row)} for row in raw]


@dataclass
class _Entry:
    customer: Customer
    facts: dict
    email: str | None
    products: list[dict]
    loaded_at: float = field(default_factory=time.monotonic)
    pinned: bool = False


# Sin columna en Databricks: el dataset no trae que documentos tiene el banco. Se inventan igual que en build_snapshot.py
# (hash del id: identidad siempre, domicilio ~60 %, ingresos ~25 %) para que la demo se comporte igual con ambas fuentes.
def docs_on_file(customer_id: str) -> set[str]:
    import hashlib

    h = hashlib.sha256(customer_id.encode()).digest()
    return {"id_copy"} | ({"address_proof"} if h[0] < 153 else set()) | ({"income_proof"} if h[1] < 64 else set())


_CUSTOMER_SELECT = """
SELECT c.customer_id, c.document_type, c.document_number, c.first_name, c.country, c.segment, c.customer_status,
       c.occupation, c.registration_date, c.email,
       p.product_id, p.product_type, p.product_number, p.currency, p.opening_date, p.opening_branch_id,
       p.opening_channel, p.product_status
FROM {silver}.customers c
LEFT JOIN {silver}.products p ON p.customer_id = c.customer_id AND p.product_status = 'Active'
WHERE {where}"""

_PRODUCT_KEYS = ("product_id", "product_type", "product_number", "currency", "opening_date", "opening_branch_id",
                 "opening_channel", "product_status")


def _normalize_country(c: str | None) -> str | None:
    return "México" if c == "Mexico" else c      # igual que build_snapshot.py


class DatabricksRepository:
    def __init__(self, sql: SqlWarehouse, silver_schema: str, gold_schema: str, ttl_s: int = 300,
                 decoy_pool: int = 200, max_entries: int = 5000):
        self._sql = sql
        self._silver, self._gold = valid_schema(silver_schema), valid_schema(gold_schema)
        self._ttl, self._max = ttl_s, max_entries
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._profiles: dict[str, tuple[float, dict]] = {}

        branches = sql.query(f"SELECT branch_id, city, country FROM {self._silver}.branches")
        self._branch_city = {b["branch_id"]: b["city"] for b in branches}
        self._branch_country = {b["branch_id"]: _normalize_country(b["country"]) for b in branches}
        by_country: dict[str, set[str]] = {}
        for b in branches:
            if b["city"]:
                by_country.setdefault(_normalize_country(b["country"]), set()).add(b["city"])
        self._cities_by_country = {c: sorted(v) for c, v in by_country.items()}
        self._cities = sorted({city for v in self._cities_by_country.values() for city in v})

        self._versions = {str(r["policy_version"]) for r in
                          sql.query(f"SELECT DISTINCT policy_version FROM {self._gold}.customer_credit_profile")}
        # Moneda local -> (fx_to_usd, fecha): las de gold, las mismas con que gold calculo las ofertas.
        self._fx_usd: dict[str, tuple[float, str]] = {"USD": (1.0, "")}
        for r in sql.query(f"SELECT local_currency, MAX(fx_to_usd) AS fx, MAX(fx_date) AS d "
                           f"FROM {self._gold}.customer_credit_profile WHERE fx_to_usd IS NOT NULL GROUP BY local_currency"):
            if r["local_currency"] and r["fx"]:
                self._fx_usd.setdefault(r["local_currency"], (float(r["fx"]), str(r["d"] or "")[:10]))

        self._decoys = self._load_decoys(decoy_pool)
        logger.info("databricks repository ready: %d branches, %d decoys, policy %s",
                    len(branches), len(self._decoys), sorted(self._versions))

    # -------------------------------------------------------------------- carga y cache
    def _fetch(self, where: str, params: dict[str, str | None], pinned: bool = False) -> list[_Entry]:
        rows = self._sql.query(_CUSTOMER_SELECT.format(silver=self._silver, where=where), params)
        grouped: dict[str, _Entry] = {}
        for r in rows:
            e = grouped.get(r["customer_id"])
            if e is None:
                country = _normalize_country(r["country"])
                customer = Customer(r["customer_id"], r["document_type"], str(r["document_number"]), r["first_name"],
                                    country, r["segment"], r["customer_status"])
                reg = r["registration_date"]
                facts = {"occupation": str(r["occupation"]) if r["occupation"] else None,
                         "registration_year": int(pd.Timestamp(reg).year) if reg is not None else None}
                e = grouped[r["customer_id"]] = _Entry(customer, facts, r["email"], [], pinned=pinned)
            if r["product_id"] is not None:
                p = {k: r[k] for k in _PRODUCT_KEYS}
                p["customer_id"] = r["customer_id"]
                p["last4"] = str(r["product_number"])[-4:]
                e.products.append(p)
        with self._lock:
            if len(self._entries) >= self._max:
                for cid in [c for c, e in sorted(self._entries.items(), key=lambda kv: kv[1].loaded_at) if not e.pinned][:self._max // 10]:
                    del self._entries[cid]
            for cid, e in grouped.items():
                e.pinned = e.pinned or (cid in self._entries and self._entries[cid].pinned)
            self._entries.update(grouped)
        return list(grouped.values())

    def _load_decoys(self, n: int) -> list[str]:
        """Clientes con datos para un reto completo (activos, con ocupacion y un producto abierto en sucursal)."""
        pick = (f"SELECT customer_id FROM {self._silver}.customers WHERE customer_status = 'Active' AND occupation IS NOT NULL "
                f"AND customer_id IN (SELECT customer_id FROM {self._silver}.products "
                f"WHERE product_status = 'Active' AND opening_channel = 'Branch') ORDER BY rand() LIMIT {int(n)}")
        entries = self._fetch(f"c.customer_id IN ({pick})", {}, pinned=True)
        return [e.customer.customer_id for e in entries]

    def _entry(self, customer_id: str) -> _Entry:
        with self._lock:
            e = self._entries.get(customer_id)
        if e is not None and (e.pinned or time.monotonic() - e.loaded_at < self._ttl):
            return e
        found = self._fetch("c.customer_id = :cid", {"cid": customer_id})
        if not found:
            raise KeyError(customer_id)
        return found[0]

    # -------------------------------------------------------------------- CustomerRepository
    def find_by_document(self, document_number: str) -> Customer | None:
        found = self._fetch("c.document_number = :doc", {"doc": str(document_number).strip()})
        return found[0].customer if found else None

    def policy_versions(self) -> set[str]:
        return set(self._versions)

    def credit_profile(self, customer_id: str) -> dict:
        with self._lock:
            hit = self._profiles.get(customer_id)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return dict(hit[1])
        rows = self._sql.query(f"SELECT * FROM {self._gold}.customer_credit_profile WHERE customer_id = :cid",
                               {"cid": customer_id})
        if not rows:
            raise KeyError(customer_id)
        with self._lock:
            if len(self._profiles) >= self._max:
                self._profiles.clear()
            self._profiles[customer_id] = (time.monotonic(), rows[0])
        return dict(rows[0])

    def products(self, customer_id: str) -> list[dict]:
        return [dict(p) for p in self._entry(customer_id).products]

    def branch_cities(self, country: str | None = None) -> list[str]:
        return self._cities if country is None else list(self._cities_by_country.get(country, []))

    def branch_city(self, branch_id: str) -> str | None:
        return self._branch_city.get(branch_id)

    def branch_country(self, branch_id: str) -> str | None:
        return self._branch_country.get(branch_id)

    def branch_countries(self) -> list[str]:
        return sorted(self._cities_by_country)

    def profile_facts(self, customer_id: str) -> dict:
        return dict(self._entry(customer_id).facts)

    def random_customer_id(self, rng) -> str:
        return self._decoys[rng.randrange(len(self._decoys))]

    def fx_rate(self, source: str, target: str) -> FxQuote | None:
        if source == target:
            return FxQuote(source, target, 1.0, "")
        a, b = self._fx_usd.get(source), self._fx_usd.get(target)
        if not a or not b:
            return None
        return FxQuote(source, target, a[0] / b[0], max(a[1], b[1]))

    def contact_email_masked(self, customer_id: str) -> str | None:
        email = _clean(self._entry(customer_id).email)
        if not email or "@" not in str(email):
            return None
        local, domain = str(email).split("@", 1)
        return f"{local[:1]}***@{domain}"

    def documents_on_file(self, customer_id: str) -> set[str]:
        return docs_on_file(customer_id)


class DatabricksOfferSink:
    """Escribe las ofertas aceptadas en <gold>.credit_offers (MERGE por offer_id). Lo llama OfferStore despues de guardar
    el JSONL. El MERGE tarda varios segundos, asi que va en un hilo aparte (una cola, en orden: aceptacion y luego derivacion
    de la misma oferta) y no detiene la respuesta al cliente. Si agota los reintentos queda en el log y en el JSONL:
    scripts/sync_credit_offers.py la sube despues."""

    ATTEMPTS = 3

    def __init__(self, sql: SqlWarehouse, gold_schema: str, retry_wait_s: float = 3.0):
        import queue

        from app.data.offers_sql import merge_sql

        self._sql, self._wait = sql, retry_wait_s
        self._merge = merge_sql(f"{valid_schema(gold_schema)}.credit_offers")
        self._q: queue.Queue[dict] = queue.Queue()
        threading.Thread(target=self._run, name="offer-sink", daemon=True).start()

    def __call__(self, row: dict) -> None:
        self._q.put(row)

    def flush(self) -> None:
        """Espera a que se escriba lo pendiente (pruebas y apagado ordenado)."""
        self._q.join()

    def _run(self) -> None:
        from app.data.offers_sql import parameters

        while True:
            row = self._q.get()
            try:
                params = parameters(row)
                for attempt in range(1, self.ATTEMPTS + 1):
                    try:
                        self._sql.query(self._merge, params)
                        break
                    except DataUnavailable:
                        if attempt == self.ATTEMPTS:
                            raise
                        time.sleep(self._wait * attempt)   # dos escrituras simultaneas pueden chocar en Delta; o Databricks cayo
            except Exception as exc:
                logger.warning("offer_sync_failed offer_id=%s error=%s", row.get("offer_id"), type(exc).__name__)
            finally:
                self._q.task_done()
