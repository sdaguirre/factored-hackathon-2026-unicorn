"""Genera backend/DATOS_DE_PRUEBA.md: clientes sinteticos del conjunto de datos que cubren cada escenario, con una ficha
por cliente para contestar las preguntas de seguridad.

Uso:  python scripts/gen_test_data.py
Los datos son sinteticos (dataset del organizador); no hay personas reales.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent.tools import ToolContext, evaluate_credit, offer_rates  # noqa: E402
from app.auth import kba  # noqa: E402
from app.config import Settings  # noqa: E402
from app.core.fmt import fmt_date, fmt_money, fmt_month_year  # noqa: E402
from app.data.repository import SnapshotRepository  # noqa: E402
from app.policy import credit_engine as ce  # noqa: E402

import argparse

ap = argparse.ArgumentParser()
ap.add_argument("--data-dir", help="carpeta con los parquet (por defecto la que usa el backend)")
ap.add_argument("--out", default="DATOS_DE_PRUEBA.md")
args = ap.parse_args()
S = Settings(_env_file=None, **({"data_dir": Path(args.data_dir)} if args.data_dir else {}))
repo = SnapshotRepository(S.data_dir)
policy = ce.load_policy(S.policy_path)
AS_OF = S.as_of_date
PROD = {"Cuenta Ahorro": "cuenta de ahorro", "Tarjeta Crédito": "tarjeta de crédito", "Cuenta Corriente": "cuenta corriente",
        "Tarjeta Débito": "tarjeta de débito", "Préstamo Personal": "préstamo personal",
        "Préstamo Hipotecario": "préstamo hipotecario", "Inversión": "inversión", "Seguro": "seguro"}
TX = {"Purchase": "compra", "Withdrawal": "retiro"}

rows = []
for r in repo._customers.itertuples():
    cid = r.customer_id
    prof = repo.credit_profile(cid)
    ctx = ToolContext(cid, repo, policy)
    info = offer_rates(ctx)
    probe = info["probe"].decision
    pre = probe.outcome == ce.ELIGIBLE and bool(info["rates"]) and (probe.max_amount or 0) > 0
    income = prof["monthly_income"]
    ok_kba = all(kba.build_challenge(repo, repo.find_by_document(str(r.document_number)), n=3, lang="es", as_of=AS_OF)
                 for _ in range(5))
    rows.append(dict(cid=cid, doc=str(r.document_number), name=r.first_name, country=r.country, ccy=prof["income_ccy"],
                     consent=prof["accepts_marketing"], income=income, score=prof["credit_score"], dpd=prof["max_days_past_due"],
                     band=probe.band, pre=pre, max_amount=probe.max_amount, ok_kba=ok_kba, rates=info["rates"],
                     dti_now=(prof["existing_monthly_debt"] / income) if income else None))
df = pd.DataFrame(rows)


def pick(mask, n=1, prefer_countries=True):
    sub = df[mask & df.ok_kba]
    if sub.empty:
        return []
    out, seen = [], set()
    for _, r in sub.iterrows():
        if prefer_countries and r.country in seen and len(out) < n and sub.country.nunique() > len(seen):
            continue
        out.append(r); seen.add(r.country)
        if len(out) == n:
            break
    return out


SCENARIOS = [
    ("A", "Consiente marketing y está preaprobado: recibe la oferta proactiva al cerrar", pick(df.consent & df.pre, 3)),
    ("B", "NO consiente marketing pero está preaprobado: puede pedir crédito, nunca recibe oferta proactiva", pick(~df.consent & df.pre, 2)),
    ("C", "Sin ingreso registrado: el asistente pide que lo declare (queda provisional)", pick(df.income.isna() & df.score.notna() & (df.dpd <= 30), 1)),
    ("D", "Sin score registrado: no se puede evaluar solo, ofrece derivar", pick(df.score.isna() & df.income.notna(), 1)),
    ("E", "Mora de 31 a 90 días: va a revisión de un asesor", pick((df.dpd > 30) & (df.dpd <= 90) & df.income.notna() & df.score.notna(), 1)),
    ("F", "Mora de más de 90 días: crédito rechazado", pick((df.dpd > 90) & df.income.notna() & df.score.notna(), 1)),
    ("G", "Sin capacidad de endeudamiento (su deuda actual ya supera el 20% del ingreso)", pick(df.dti_now.notna() & (df.dti_now >= 0.2) & (df.dpd <= 30) & df.score.notna(), 1)),
    ("H", "Score muy bajo (banda 1): crédito rechazado", pick((df.band == 1) & (df.dpd <= 30) & df.income.notna(), 1)),
]
# Sin datos suficientes para 3 preguntas: AUTH_UNAVAILABLE
unavail = df[~df.ok_kba]


def card(r) -> str:
    cid = r["cid"]
    lines = []
    prods = repo.products(cid)
    for p in prods:
        city = repo.branch_city(p["opening_branch_id"])
        lines.append(f"  - {PROD.get(p['product_type'], p['product_type'])} con terminación **{p['last4']}**: abierta en "
                     f"**{city}**, en **{fmt_month_year(pd.Timestamp(p['opening_date']).replace(day=1), 'es')}**")
    txs = [t for t in repo.recent_transactions(cid)
           if pd.Timestamp(t["transaction_date"]).date() >= AS_OF - timedelta(days=365)]
    keys = [(t["transaction_type"], pd.Timestamp(t["transaction_date"]).date()) for t in txs]
    uniq = [t for t, k in zip(txs, keys) if keys.count(k) == 1]
    tx_lines = [f"  - {fmt_date(pd.Timestamp(t['transaction_date']))}, {TX[t['transaction_type']]}: ciudad **{t['transaction_city']}**, "
                f"monto **{fmt_money(float(t['amount']), t['currency'])}**" for t in uniq]
    return "\n".join(["- Productos (ciudad y mes de apertura):"] + lines + ["- Movimientos de los últimos 12 meses:"] + (tx_lines or ["  - (ninguno)"]))


def phrases(r) -> list[str]:
    out = []
    inc = r["income"]
    if r["pre"]:
        out += ["«¿qué tasas tienen para mí?»", "«gracias, eso es todo» (debe llegar la oferta)" if r["consent"] else "«gracias, eso es todo» (NO debe llegar oferta)"]
    if pd.notna(inc) and inc:
        small, big = int(inc * 0.3), int(inc * 6)
        res_s = evaluate_credit(ToolContext(r["cid"], repo, policy), "personal_loan", small, 24)
        res_b = evaluate_credit(ToolContext(r["cid"], repo, policy), "personal_loan", big, 36)
        out += [f"«quiero un préstamo de {small} a 24 meses» → {res_s.decision.outcome}",
                f"«necesito un préstamo de {big}» → {res_b.decision.outcome}"]
        out.append(f"«ahora gano {int(inc * 1.4)} al mes» (tras un rechazo por capacidad, recalcula como provisional)")
    else:
        out += ["«quiero un préstamo de 3000» → pide el ingreso; luego «gano 5000»"]
    out += ["«no reconozco un cargo en mi cuenta» → pide confirmar la derivación; nunca ofrece crédito",
            "«quiero hablar con un asesor» → deriva y muestra el número de seguimiento"]
    return out


def header(r) -> str:
    inc = fmt_money(r["income"], r["ccy"], 0) if pd.notna(r["income"]) else "sin ingreso registrado"
    sc = int(r["score"]) if pd.notna(r["score"]) else "sin score"
    return (f"**{r['name']}** · documento `{r['doc']}` · {r['country']} · moneda {r['ccy']} · ingreso {inc} · score {sc} · "
            f"mora máx. {int(r['dpd'])} días · marketing: {'sí' if r['consent'] else 'no'}")


md = ["# Datos de prueba del asistente",
      "",
      "Todo es **sintético**; no hay personas reales. Se genera con "
      "`python scripts/gen_test_data.py` a partir de los datos que use el backend (`data/fixture/` por defecto).",
      "",
      "## Cómo probar",
      "",
      "1. Abra la interfaz (`http://localhost:8080`) y escriba el **documento** de un cliente de abajo.",
      "2. Responda las **preguntas de seguridad** con la ficha del cliente. Las preguntas cambian en cada intento y salen "
      "de estos mismos datos: ciudad o mes y año de apertura de un producto (se identifica por su terminación), y ciudad o "
      "monto de un movimiento (se identifica por su fecha y tipo). Las opciones incorrectas son inventadas.",
      "3. Pruebe las frases sugeridas de cada escenario. Para portugués, cambie el selector de idioma antes de empezar.",
      "",
      "Tres intentos fallidos bloquean ese documento 15 minutos (también un documento inexistente, a propósito). "
      "Para desbloquear, reinicie el backend: `docker compose restart chat-backend`.",
      "",
      "Los montos de los ejemplos están en la moneda del ingreso de cada cliente. Los resultados indicados (eligible, "
      "declined…) son los de la política provisional con los datos de hoy.",
      ""]
for letter, title, custs in SCENARIOS:
    md += [f"## Escenario {letter}: {title}", ""]
    if not custs:
        md += ["_No hay un cliente así en el snapshot actual._", ""]
        continue
    for r in custs:
        md += [header(r), "", card(r), "", "Frases para probar:", ""] + [f"- {p}" for p in phrases(r)] + [""]
md += ["## Escenario I: sin datos suficientes para 3 preguntas (no se puede verificar por este canal)", ""]
if unavail.empty:
    md += ["_No hay un cliente así en el snapshot actual._", ""]
else:
    md += ["Estos documentos reciben el aviso «No es posible verificar su identidad por este canal»: "
           + ", ".join(f"`{d}`" for d in unavail.doc.head(4)), ""]
md += ["## Escenario J: documento inexistente", "",
       "Cualquier número que no esté arriba (por ejemplo `99999999`) recibe preguntas igual que un cliente real, pero nunca "
       "se aprueban: así no se revela qué documentos existen.", ""]
out = ROOT / args.out
out.write_text("\n".join(md), encoding="utf-8")
print("escrito", out, "| escenarios con cliente:", sum(1 for _, _, c in SCENARIOS if c), "de", len(SCENARIOS), "| sin kba:", len(unavail))
