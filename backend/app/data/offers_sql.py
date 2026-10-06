"""MERGE de una oferta aceptada en <gold>.credit_offers. Lo comparten la API (escritura directa al aceptar) y
scripts/sync_credit_offers.py (reintento desde el JSONL). Las columnas son las de data/databricks/gold/00_deploy_objects.sql."""
from __future__ import annotations

import json

# Columnas de gold credit_offers y su tipo, en orden. Una prueba las compara con el DDL.
COLUMNS = [
    ("offer_id", "STRING"), ("customer_id", "STRING"), ("session_id", "STRING"), ("channel", "STRING"),
    ("language", "STRING"), ("offer_origin", "STRING"), ("option_code", "STRING"), ("product_code", "STRING"),
    ("product_type", "STRING"), ("tier", "STRING"), ("term_months", "INT"), ("amount_usd", "DOUBLE"),
    ("annual_rate_pct", "DOUBLE"), ("monthly_installment_usd", "DOUBLE"), ("local_currency", "STRING"),
    ("fx_to_usd", "DOUBLE"), ("fx_date", "DATE"), ("amount_local", "DOUBLE"), ("monthly_installment_local", "DOUBLE"),
    ("income_used_usd", "DOUBLE"), ("income_source", "STRING"), ("current_installments_usd", "DOUBLE"),
    ("max_total_installment_usd", "DOUBLE"), ("debt_to_income_after", "DOUBLE"), ("risk_band", "STRING"),
    ("segment", "STRING"), ("rate_adjustment_pp", "DOUBLE"), ("customer_declared_data", "STRING"),
    ("open_complaints", "INT"), ("open_priority_complaints", "INT"), ("open_critical_complaints", "INT"),
    ("is_conditional", "BOOLEAN"), ("flags", "ARRAY<STRING>"), ("required_documents", "ARRAY<STRING>"),
    ("status", "STRING"), ("handoff_ticket_id", "STRING"), ("advisor_summary", "STRING"), ("valid_until", "DATE"),
    ("profile_as_of_date", "DATE"), ("policy_version", "STRING"), ("created_at", "TIMESTAMP"), ("updated_at", "TIMESTAMP"),
]


def _expr(name: str, typ: str) -> str:
    if typ.startswith("ARRAY"):
        return f"from_json(:{name}, '{typ}') AS {name}"
    return f"CAST(:{name} AS {typ}) AS {name}"


def merge_sql(table: str) -> str:
    select = ",\n        ".join(_expr(n, t) for n, t in COLUMNS)
    return (f"MERGE INTO {table} AS t\nUSING (SELECT\n        {select}) AS s\nON t.offer_id = s.offer_id\n"
            "WHEN MATCHED THEN UPDATE SET *\nWHEN NOT MATCHED THEN INSERT *")


def parameters(row: dict) -> list[tuple[str, str | None]]:
    out = []
    for name, typ in COLUMNS:
        v = row.get(name)
        if v is None:
            out.append((name, None))
        elif typ.startswith("ARRAY"):
            out.append((name, json.dumps(v)))
        elif typ == "BOOLEAN":
            out.append((name, "true" if v else "false"))
        else:
            out.append((name, str(v)))
    return out
