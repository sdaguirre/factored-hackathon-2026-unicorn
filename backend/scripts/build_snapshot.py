"""Construye el snapshot de datos del prototipo (parquet) a partir del dataset del organizador.

Uso:  python backend/scripts/build_snapshot.py --customers 400
       python backend/scripts/build_snapshot.py --gold-only     # solo el perfil de credito de gold, misma muestra
Requiere las tablas del organizador (HACKATHON_RAW_DIR), los parquet derivados de analysis/ (HACKATHON_WORK_DIR) y el export
de gold (HACKATHON_GOLD_DIR, por defecto .local/gold/: python data/scripts/export_gold.py --profile <perfil>).
El resultado NO se versiona (backend/data/snapshot/ esta en .gitignore); el repo lleva un conjunto de ejemplo propio.

Salida: backend/data/snapshot/{customers,credit_profile,products,branches}.parquet (the assistant reads only the
gold credit profile; customers, products and branches are used by the identity check)
credit_profile.parquet son las filas de gold customer_credit_profile (politica 0.4, USD) de los clientes de la muestra.
Todo es dato sintetico del organizador. Se toma una muestra estratificada de clientes que tienen
datos suficientes para las preguntas de seguridad (productos activos y movimientos recientes).
"""
from __future__ import annotations

import argparse
import hashlib
import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
RAW = Path(os.environ.get("HACKATHON_RAW_DIR", REPO / ".local" / "raw"))
WORK = Path(os.environ.get("HACKATHON_WORK_DIR", REPO / ".local" / "derived"))
GOLD = Path(os.environ.get("HACKATHON_GOLD_DIR", REPO / ".local" / "gold"))
OUT = Path(__file__).resolve().parents[1] / "data" / "snapshot"
TX_PER_CUSTOMER = 40
SEED = 42
AS_OF = date(2026, 6, 17)   # igual que Settings.as_of_date: ultimo dia de datos del dataset


def add_profile_fields() -> None:
    """Agrega ocupacion y fecha de registro al snapshot EXISTENTE (preguntas de seguridad) sin cambiar su muestra de clientes."""
    path = OUT / "customers.parquet"
    snap = pd.read_parquet(path).drop(columns=["occupation", "registration_date"], errors="ignore")
    raw = pd.read_csv(RAW / "customers.csv", encoding="utf-8-sig", usecols=["customer_id", "occupation", "registration_date"])
    out = snap.merge(raw, on="customer_id", how="left")
    out.to_parquet(path, index=False)
    print(f"customers.parquet: ocupacion en {out.occupation.notna().mean():.0%} y fecha de registro en "
          f"{out.registration_date.notna().mean():.0%} de {len(out)} clientes")


def read_gold_profile() -> pd.DataFrame:
    """customer_credit_profile del export de gold (carpeta Parquet). La politica la fija el export, no este script."""
    return pd.read_parquet(GOLD / "customer_credit_profile")


def write_credit_profile(gold: pd.DataFrame, customer_ids: set[str]) -> None:
    prof = gold[gold.customer_id.isin(customer_ids)]
    missing = customer_ids - set(prof.customer_id)
    if missing:
        raise SystemExit(f"{len(missing)} clientes del snapshot no estan en el export de gold (p. ej. {sorted(missing)[:3]})")
    prof.to_parquet(OUT / "credit_profile.parquet", index=False)
    print(f"credit_profile: {len(prof)} clientes, politica {sorted(prof.policy_version.unique())}, "
          f"corte {sorted(map(str, prof.as_of_date.unique()))}, elegibles {int(prof.is_eligible.sum())}, "
          f"proactivos {int((prof.offer_mode == 'proactive').sum())}")


def gold_only() -> None:
    """Reemplaza el perfil de credito del snapshot EXISTENTE por el de gold, sin cambiar la muestra de clientes."""
    path = OUT / "customers.parquet"
    snap = pd.read_parquet(path)
    write_credit_profile(read_gold_profile(), set(snap.customer_id))
    snap.drop(columns=[c for c in LEGACY_CREDIT_COLUMNS if c in snap.columns]).to_parquet(path, index=False)


# Columnas del perfil preliminar (antes de gold 0.4) que ya no van en customers.parquet: ahora estan en credit_profile.
LEGACY_CREDIT_COLUMNS = ("credit_score", "monthly_income", "existing_monthly_debt", "max_days_past_due", "n_active_products",
                         "income_ccy")


def main(n_customers: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cu = pd.read_csv(RAW / "customers.csv", encoding="utf-8-sig")
    pr = pd.read_csv(RAW / "products.csv", encoding="utf-8-sig")
    br = pd.read_csv(RAW / "branches.csv", encoding="utf-8-sig")
    gold = read_gold_profile()
    tx = pd.read_parquet(
        WORK / "transactions.parquet",
        columns=["transaction_id", "transaction_date", "customer_id", "product_id", "transaction_type",
                 "amount", "currency", "channel", "merchant_name", "transaction_city",
                 "transaction_country", "transaction_status"],
    )

    # Normalizacion minima (silver): el pais de transacciones mezcla "Mexico"/"México"
    for df, col in ((cu, "country"), (tx, "transaction_country")):
        df[col] = df[col].replace({"Mexico": "México"})

    tx = tx[(tx.transaction_status == "Approved") & tx.transaction_city.notna()
            & tx.transaction_type.isin(["Purchase", "Withdrawal"])].copy()
    tx["transaction_date"] = pd.to_datetime(tx.transaction_date)
    tx["amount"] = pd.to_numeric(tx.amount)

    # Un mismo (cliente, tipo, dia) debe ser unico: la pregunta de seguridad nombra tipo + fecha.
    tx["day"] = tx.transaction_date.dt.date
    tx = tx.sort_values("transaction_date", ascending=False)
    tx = tx.drop_duplicates(["customer_id", "transaction_type", "day"], keep=False)

    active = pr[pr.product_status == "Active"]
    elig = (
        cu[(cu.customer_status == "Active") & cu.customer_id.isin(active.customer_id)]
        .merge(tx.groupby("customer_id").size().rename("n_tx"), left_on="customer_id", right_index=True)
        .query("n_tx >= 6")
    )
    # Estratificado por pais y por disponibilidad de datos de credito para ejercitar todos los caminos.
    elig = elig.merge(gold[["customer_id", "credit_score", "income_used_usd"]], on="customer_id")
    elig["has_credit_data"] = elig.credit_score.notna() & elig.income_used_usd.notna()
    rng = np.random.default_rng(SEED)
    parts = []
    for (country, ok), g in elig.groupby(["country", "has_credit_data"]):
        share = 0.7 if ok else 0.3
        k = max(1, int(n_customers * share * len(g) / max(len(elig[elig.country == country]), 1) / 3))
        parts.append(g.sample(min(k, len(g)), random_state=int(rng.integers(1e6))))
    pick = pd.concat(parts).drop_duplicates("customer_id")
    ids = set(pick.customer_id)

    customers = (
        cu[cu.customer_id.isin(ids)][["customer_id", "document_type", "document_number", "first_name",
                                      "country", "segment", "customer_status", "accepts_marketing", "email",
                                      "occupation", "registration_date"]]   # ocupacion y ano de alta: preguntas de seguridad
    )
    # Documentos que el banco "ya tiene": el dataset no los trae, asi que se INVENTAN de forma determinista por cliente
    # (hash del id). La identidad siempre; comprobante de domicilio en ~60%; de ingresos en ~25%.
    def docs(cid: str) -> str:
        h = hashlib.sha256(cid.encode()).digest()
        out = ["id_copy"] + (["address_proof"] if h[0] < 153 else []) + (["income_proof"] if h[1] < 64 else [])
        return ",".join(sorted(out))

    customers["docs_on_file"] = customers.customer_id.map(docs)
    products = active[active.customer_id.isin(ids)][
        ["product_id", "customer_id", "product_type", "product_number", "currency", "opening_date",
         "opening_branch_id", "opening_channel", "product_status"]
    ].copy()
    products["last4"] = products.product_number.astype(str).str[-4:]
    branches = br[["branch_id", "branch_name", "city", "state", "country"]].copy()
    branches["country"] = branches.country.replace({"Mexico": "México"})
    customers.to_parquet(OUT / "customers.parquet", index=False)
    write_credit_profile(gold, ids)
    products.to_parquet(OUT / "products.parquet", index=False)
    branches.to_parquet(OUT / "branches.parquet", index=False)
    print(f"customers={len(customers)} products={len(products)} branches={len(branches)}")
    print("por pais:", customers.country.value_counts().to_dict())
    print("sin datos de credito completos:", int((~pick.has_credit_data).sum()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--customers", type=int, default=400)
    ap.add_argument("--gold-only", action="store_true",
                    help="solo reemplaza el perfil de credito por el de gold (credit_profile.parquet) sin cambiar la muestra")
    ap.add_argument("--profile-only", action="store_true",
                    help="solo agrega ocupacion y fecha de registro al customers.parquet existente (no cambia la muestra)")
    args = ap.parse_args()
    if args.gold_only:
        gold_only()
    elif args.profile_only:
        add_profile_fields()
    else:
        main(args.customers)
