"""Genera el conjunto de ejemplo del equipo (backend/data/fixture/*.parquet).

TODO ES INVENTADO POR EL EQUIPO con una semilla fija: no se deriva de ningun dato del organizador. Existe para que quien
clone el repositorio pueda ejecutar el backend, las pruebas y el CI sin acceso al bucket. Tiene la misma forma que el
snapshot derivado del dataset (ver scripts/build_snapshot.py) y cubre cada escenario de la politica.

credit_profile.parquet tiene las columnas de gold customer_credit_profile (politica 0.4, USD). Aqui las calcula el equipo con
las mismas reglas (bandas, ajustes, filtros R01-R08, offer_mode) a partir de los datos inventados; en el snapshot vienen del
export de gold.

Uso:  python scripts/make_fixture.py
"""
from __future__ import annotations

import random
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "data" / "fixture"
AS_OF = date(2026, 6, 17)
SEED = 7
rng = random.Random(SEED)
extra = random.Random(SEED + 101)   # campos agregados despues: generador aparte para no alterar los datos previos
nrng = np.random.default_rng(SEED)

CITIES = {
    "México": ["Ciudad de México", "Guadalajara", "Monterrey", "Puebla", "Tijuana", "Querétaro"],
    "Colombia": ["Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena"],
    "Argentina": ["Buenos Aires", "Córdoba", "Rosario", "Mendoza", "La Plata"],
}
CCY = {"México": "MXN", "Colombia": "COP", "Argentina": "ARS"}
DOC_TYPE = {"México": "DNI", "Colombia": "CC", "Argentina": "DNI"}
NAMES = ["Alicia", "Bruno", "Carla", "Daniel", "Elena", "Felipe", "Gabriela", "Héctor", "Inés", "Joaquín", "Karina",
         "Lucas", "Marisol", "Nicolás", "Olivia", "Pablo", "Renata", "Santiago", "Teresa", "Valentín", "Ximena", "Yago"]
MERCHANTS = ["Super Ahorro", "Mercado Central", "Tienda del Barrio", "Restaurante La Esquina", "Farmacia Salud", "Cine Centro"]
PRODUCTS = ["Cuenta Ahorro", "Cuenta Corriente", "Tarjeta Débito", "Tarjeta Crédito"]

# (pais, consentimiento, score, ingreso, ratio de deuda actual, mora maxima, con movimientos)
# Cada fila existe para ejercitar un camino de la politica o de la oferta proactiva.
SPEC = [
    ("México", True, 740, 95_000, 0.03, 0, True), ("México", True, 690, 62_000, 0.05, 0, True),
    ("México", False, 756, 80_000, 0.02, 0, True), ("México", False, 640, 52_000, 0.04, 0, True),
    ("México", False, 790, None, 0.0, 0, True), ("México", True, 675, 103_000, 0.03, 120, True),
    ("México", True, 502, 44_000, 0.0, 0, True), ("México", False, 619, 25_000, 0.45, 0, True),
    ("México", False, 658, 70_000, 0.03, 45, True), ("México", True, 720, 88_000, 0.02, 0, False),
    ("Colombia", True, 613, 10_500_000, 0.03, 0, True), ("Colombia", True, 700, 7_200_000, 0.04, 0, True),
    ("Colombia", False, 640, 5_800_000, 0.05, 0, True), ("Colombia", True, None, 9_000_000, 0.02, 0, True),
    ("Colombia", False, 800, None, 0.0, 0, True), ("Colombia", True, 580, 6_000_000, 0.03, 0, True),
    ("Argentina", True, 565, 586_000, 0.03, 0, True), ("Argentina", True, 720, 1_900_000, 0.04, 0, True),
    ("Argentina", False, 554, 575_000, 0.02, 0, True), ("Argentina", True, 658, 3_500_000, 0.03, 90, True),
    ("Argentina", True, 700, None, 0.0, 0, True),
]


def month_start(d: date) -> date:
    return d.replace(day=1)


OCCUPATIONS = ["Accountant", "Administrative", "Artist", "Consultant", "Director", "Doctor", "Driver", "Employee", "Engineer",
               "Entrepreneur", "Homemaker", "Independent Professional", "Lawyer", "Manager", "Merchant", "Retired",
               "Salesperson", "Student", "Teacher", "Technician"]
NO_DATA_CUSTOMER = "FXC-010"
# Meses hasta los 75 anos (tope de plazo por edad, politica 0.4). Solo este cliente: su banda B permite hipotecas a 360 meses,
# pero la edad las limita a 200 (15 anos si, 20 no). Al resto no se le fija fecha de nacimiento: sin tope, como en gold.
AGE_CAP_MONTHS = {"FXC-012": 200}
FX_PER_USD = {"MXN": 17.30, "COP": 4000.0, "ARS": 350.0}     # invented rates; they go to fx_to_usd in credit_profile.parquet


def add_security_question_data(customers: list[dict], products: list[dict]) -> None:
    """Ocupacion y fecha de registro INVENTADAS (generador propio: no altera los demas datos) y garantia de que cada cliente
    tiene un producto abierto en sucursal, salvo NO_DATA_CUSTOMER (sin ocupacion y sin productos abiertos en sucursal: solo
    puede recibir 2 tipos de pregunta, asi que no se le puede verificar por este canal)."""
    prof = random.Random(SEED + 303)
    for c in customers:
        year = prof.randrange(2018, 2026)
        c["registration_date"] = datetime(year, prof.randrange(1, 13), prof.randrange(1, 28), 10, 0, 0).isoformat(sep=" ")
        c["occupation"] = None if c["customer_id"] == NO_DATA_CUSTOMER else prof.choice(OCCUPATIONS)
    seen: set[str] = set()
    for p in products:
        cid = p["customer_id"]
        if cid == NO_DATA_CUSTOMER:
            p["opening_channel"] = "App" if p["opening_channel"] == "Branch" else p["opening_channel"]
        elif cid not in seen:
            p["opening_channel"] = "Branch"          # el primero de cada cliente: ciudad de apertura recordable
        seen.add(cid)


# (cliente, origen, categoria, tipo, prioridad, estado, dias abierto, escalado, SLA incumplido, sentimiento, reincidente, canal)
# Cada fila ejercita un camino de la conversacion: caso pendiente, caso critico, enojo previo, caso viejo, varios casos.
# FXC-001 queda sin casos a proposito: es el cliente "limpio" (preaprobado, con consentimiento) de varias pruebas.
CASES = [
    (11, "complaint", "Fees", "Complaint", "High", "In Process", 12, False, False, None, False, "App"),
    (2, "complaint", "Transactions", "Claim", "Critical", "Escalated", 31, True, True, None, True, "Call Center"),
    (2, "interaction", "Queja", None, None, "Unresolved", 9, True, False, "Muy Negativo", False, "Phone"),
    (3, "interaction", "Queja", None, None, "Unresolved", 20, False, False, "Negativo", False, "Phone"),
    (4, "interaction", "Transaccional", None, None, "Unresolved", 45, True, False, "Neutral", False, "Web Chat"),
    (5, "complaint", "Service", "Complaint", "Low", "Open", 400, False, False, None, False, "Branch"),   # viejo: no se menciona al saludar
    (6, "complaint", "Technical", "Request", "Medium", "Open", 6, False, False, None, False, "Web"),
    (6, "interaction", "Técnico", None, None, "Unresolved", 3, False, False, "Neutral", False, "WhatsApp"),
]


def make_case_context() -> pd.DataFrame:
    """Casos abiertos INVENTADOS (misma forma que scripts/case_context.py). Generador propio: no altera el resto."""
    r = random.Random(SEED + 202)
    rows = []
    for n, (i, src, cat, ctype, prio, status, days, esc, sla, sent, rep, chan) in enumerate(CASES, start=1):
        opened = AS_OF - timedelta(days=days)
        rows.append({
            "customer_id": f"FXC-{i:03d}", "case_source": src,
            "case_id": f"FXK-{n:03d}-{r.randrange(1000, 9999)}", "opened_on": opened.isoformat(), "days_open": days,
            "channel": chan, "category": cat, "case_type": ctype, "priority": prio, "status": status,
            "is_escalated": esc, "sla_breached": sla, "sentiment": sent, "is_repeat_complainer": rep,
            "as_of_date": AS_OF.isoformat(),
        })
    return pd.DataFrame(rows)


def make_credit_profiles(customers: list[dict], products: list[dict], spec: list[tuple], cases: pd.DataFrame) -> pd.DataFrame:
    """Filas de gold customer_credit_profile para los clientes inventados (misma logica que gold/10_customer_credit_profile.sql).

    Deterministico y sin azar: no altera los demas datos del fixture."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.policy.engine import load_policy

    policy = load_policy()
    prm = policy.params
    max_dti, min_income = float(prm["max_debt_to_income"]), float(prm["min_income_usd"])
    complaints = cases[(cases.case_source == "complaint") & cases.status.isin(["Open", "In Process", "Escalated"])]
    rows = []
    for c, (_country, _consent, score, income, debt_ratio, dpd, _tx) in zip(customers, spec):
        cid, ccy = c["customer_id"], CCY[c["country"]]
        fx = round(1 / FX_PER_USD[ccy], 6)
        mine = [p for p in products if p["customer_id"] == cid and p["product_status"] == "Active"]
        band = next((b for b in policy.bands if score is not None and score >= b["min_credit_score"]), None)
        income_usd = round(income * fx, 2) if income else None
        installments = round(income * debt_ratio * fx, 2) if income else 0.0
        reg = datetime.fromisoformat(c["registration_date"]).date()
        tenure = (AS_OF.year - reg.year) * 12 + AS_OF.month - reg.month - (AS_OF.day < reg.day)
        comp = complaints[complaints.customer_id == cid]
        crit = int((comp.priority == "Critical").sum())
        age_cap = AGE_CAP_MONTHS.get(cid)
        seg_adj = policy.segments.get(c["segment"], 0.0)
        band_adj = band["rate_adjustment_pp"] if band else None
        max_total = round(max_dti * income_usd, 2) if income_usd is not None else None
        available = round(max_total - installments, 2) if max_total is not None else None
        codes = [code for code, hit in (
            ("R01_INACTIVE_CUSTOMER", c["customer_status"] != "Active"),
            ("R02_SHORT_TENURE", tenure < int(prm["min_tenure_months"])),
            ("R03_DELINQUENCY", dpd > int(prm["max_days_past_due"])),
            ("R05_INCOME_MISSING", income_usd is None),
            ("R05_INCOME_BELOW_MIN", income_usd is not None and income_usd < min_income),
            ("R06_SCORE_MISSING", band is None),
            ("R06_SCORE_BELOW_MIN", band is not None and not band["offer_allowed"]),
            ("R08_NO_CAPACITY", available is not None and available <= 0)) if hit]
        eligible = not codes
        mode = "none" if not eligible else ("proactive" if c["accepts_marketing"] and crit == 0 else "on_customer_interest")
        types = [p["product_type"] for p in mine]

        def term(key: str) -> int:
            t = band[key] if band else 0
            return min(t, age_cap) if age_cap is not None else t

        rows.append({
            "customer_id": cid, "country": c["country"], "local_currency": ccy, "segment": c["segment"],
            "customer_status": c["customer_status"], "registration_date": reg, "tenure_months": tenure,
            "accepts_marketing": c["accepts_marketing"], "credit_score": float(score) if score is not None else None,
            "risk_band": band["band"] if band else None, "declared_income_local": float(income) if income else None,
            "declared_income_usd": income_usd, "avg_monthly_deposits_usd_6m": None, "deposit_months_6m": 0,
            "income_used_usd": income_usd, "income_source": "declared_profile" if income_usd is not None else "missing",
            "active_products": len(mine), "active_credit_products": types.count("Tarjeta Crédito"),
            "active_credit_cards": types.count("Tarjeta Crédito"), "active_personal_loans": 0, "active_mortgages": 0,
            "card_limit_usd": None, "card_balance_usd": None, "card_utilization": None,
            "current_installments_usd": installments,
            "current_debt_to_income": round(installments / income_usd, 4) if income_usd else None,
            "max_debt_to_income": max_dti, "max_total_installment_usd": max_total, "available_installment_usd": available,
            "max_days_past_due": dpd, "has_blocked_or_suspended_product": False, "confirmed_fraud_tx_recent": 0,
            "open_complaints": len(comp), "open_priority_complaints": int(comp.priority.isin(["High", "Critical"]).sum()),
            "open_critical_complaints": crit, "band_rate_adjustment_pp": band_adj, "segment_rate_adjustment_pp": seg_adj,
            "max_term_personal_loan_months": term("max_term_personal_loan_months"),
            "max_term_mortgage_months": term("max_term_mortgage_months"), "max_term_by_age_months": age_cap,
            "total_rate_adjustment_pp": (band_adj or 0.0) + seg_adj, "reason_codes": codes, "is_eligible": eligible,
            "offer_mode": mode,
            "not_proactive_reason": None if mode != "on_customer_interest" else (
                "no_marketing_consent" if not c["accepts_marketing"] else "open_critical_complaint"),
            "can_become_eligible_with_declared_income": income_usd is None, "requires_advisor_review": False,
            "fx_to_usd": fx, "fx_date": AS_OF, "as_of_date": AS_OF, "policy_version": policy.version,
        })
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    branches = []
    for country, cities in CITIES.items():
        for city in cities:
            for k in range(3):
                branches.append({"branch_id": f"FXS-{country[:2].upper()}-{len(branches):03d}", "branch_name": f"{city} {k + 1}",
                                 "city": city, "state": city, "country": country})
    br_by_country = {c: [b for b in branches if b["country"] == c] for c in CITIES}

    used_docs: set[str] = set()
    customers, products = [], []
    for i, (country, consent, score, income, debt_ratio, dpd, with_tx) in enumerate(SPEC, start=1):
        cid = f"FXC-{i:03d}"
        doc = f"{rng.randrange(10_000_000, 99_999_999)}"
        while doc in used_docs:
            doc = f"{rng.randrange(10_000_000, 99_999_999)}"
        used_docs.add(doc)
        ccy = CCY[country]
        rng.choice(CITIES[country])                      # former home city of the transactions: draw kept

        n_prod = rng.choice([3, 3, 4])
        types = rng.sample(PRODUCTS, k=n_prod)
        months = rng.sample(range(0, 90), k=n_prod)      # meses de apertura distintos entre 2018-06 y 2025-12
        last4s = rng.sample(range(1000, 9999), k=n_prod)
        cust_products = []
        for t, m, l4 in zip(types, months, last4s):
            y, mo = divmod(2018 * 12 + 5 + m, 12)
            opening = date(y, mo + 1, rng.randrange(1, 28))
            br = rng.choice(br_by_country[country])
            pid = f"FXP-{i:03d}-{l4}"
            cust_products.append(pid)
            products.append({"product_id": pid, "customer_id": cid, "product_type": t, "product_number": f"{rng.randrange(10**11, 10**12 - 1)}{l4}",
                             "currency": ccy, "opening_date": opening.isoformat(), "opening_branch_id": br["branch_id"],
                             "opening_channel": rng.choice(["Branch", "App", "Web"]), "product_status": "Active", "last4": str(l4)})

        customers.append({
            "customer_id": cid, "document_type": DOC_TYPE[country], "document_number": doc,
            "first_name": rng.choice(NAMES), "country": country, "segment": rng.choice(["Basic", "Plus", "Premium"]),
            "customer_status": "Active", "accepts_marketing": consent,
        })

        # Correo (dominio reservado example.com) y documentos que el banco ya tiene. El primer cliente tiene todos
        # (camino directo a un asesor); el segundo, solo la identidad; el resto, al azar.
        base = unicodedata.normalize("NFKD", customers[-1]["first_name"]).encode("ascii", "ignore").decode().lower()
        on_file = ["id_copy"]
        if i == 1:
            on_file += ["address_proof", "income_proof"]
        elif i > 2:
            on_file += [d for d, pr in (("address_proof", 0.6), ("income_proof", 0.25)) if extra.random() < pr]
        customers[-1]["email"] = f"{base}{i}@example.com"
        customers[-1]["docs_on_file"] = ",".join(sorted(on_file))

        if with_tx:
            # No transactions are written anymore (the assistant reads only the gold-shaped profile), but the same random
            # draws are kept: the generator is shared, so dropping them would change every later customer of the fixture.
            seen: set[tuple[str, date]] = set()
            scale = (income or 1_000) / 40
            for _ in range(rng.randrange(8, 13)):
                for _try in range(20):
                    day = AS_OF - timedelta(days=rng.randrange(3, 330))
                    ttype = rng.choice(["Purchase", "Purchase", "Withdrawal"])
                    if (ttype, day) not in seen:
                        seen.add((ttype, day))
                        break
                if rng.random() >= 0.8:
                    rng.choice(CITIES[country])
                rng.choice(cust_products), rng.randrange(8, 22), rng.randrange(60)
                nrng.lognormal(mean=np.log(scale), sigma=0.6)
                if ttype == "Purchase":
                    rng.choice(MERCHANTS)

    add_security_question_data(customers, products)
    pd.DataFrame(customers).to_parquet(OUT / "customers.parquet", index=False)
    pd.DataFrame(products).to_parquet(OUT / "products.parquet", index=False)
    pd.DataFrame(branches).to_parquet(OUT / "branches.parquet", index=False)
    # The assistant reads only the gold-shaped credit profile: no transactions, FX or case files are written.
    # The invented cases only feed the complaint counts of the profile (as complaints feed gold).
    cases = make_case_context()
    credit = make_credit_profiles(customers, products, SPEC, cases)
    credit.to_parquet(OUT / "credit_profile.parquet", index=False)
    print(f"fixture: perfil de credito (politica {credit.policy_version.iloc[0]}): elegibles={int(credit.is_eligible.sum())} "
          f"proactivos={int((credit.offer_mode == 'proactive').sum())} de {len(credit)}")
    print(f"fixture: casos abiertos={len(cases)} en {cases.customer_id.nunique()} clientes")
    print(f"fixture: clientes={len(customers)} productos={len(products)} sucursales={len(branches)} -> {OUT}")


if __name__ == "__main__":
    main()
