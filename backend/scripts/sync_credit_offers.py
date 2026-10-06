"""Sube las ofertas aceptadas en el chat (JSONL local) a <gold>.credit_offers en Databricks.

La API agrega una fila por evento (aceptacion y, luego, derivacion con su ticket) a CHAT_OFFERS_PATH; aqui se toma la
ultima fila de cada offer_id y se hace MERGE por offer_id. Con CHAT_REPOSITORY=databricks la API ya escribe al aceptar;
este script sirve para reintentar las que fallaron (el JSONL es siempre el respaldo) o cuando el contenedor no tiene credenciales.
Las restricciones CHECK de la tabla (estado, origen, montos positivos, limite del 20%) tambien validan en Databricks.

Uso (sin --apply solo muestra lo que subiria):
    python backend/scripts/sync_credit_offers.py --file <CHAT_OFFERS_PATH>
    python backend/scripts/sync_credit_offers.py --file <CHAT_OFFERS_PATH> --apply --profile <perfil> --warehouse-id <id>
Requiere databricks-sdk y el CLI de Databricks en el PATH (data/scripts/requirements.txt).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.data.offers_sql import COLUMNS, merge_sql, parameters  # noqa: E402,F401  (las usa la API; aqui el reintento)


def latest_rows(path: Path) -> list[dict]:
    """Ultima fila de cada offer_id, en el orden en que se aceptaron."""
    rows: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                rows[row["offer_id"]] = row
    return list(rows.values())


def main(args) -> None:
    rows = latest_rows(Path(args.file))
    table = f"{args.gold_schema}.credit_offers"
    print(f"{len(rows)} ofertas en {args.file} -> {table}")
    for r in rows:
        print(f"  {r['offer_id']} {r['customer_id']} {r['option_code']}/{r['term_months']} {r['amount_usd']:,.2f} USD "
              f"dti {r['debt_to_income_after']:.4f} flags {r['flags']} ticket {r['handoff_ticket_id']} [{r['status']}]")
    if not args.apply:
        print("Sin --apply no se escribe nada.")
        return
    if not args.warehouse_id:
        raise SystemExit("--warehouse-id es obligatorio con --apply")
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import StatementParameterListItem

    sys.path.insert(0, str(ROOT / "data" / "scripts"))
    from run_databricks_sql import run  # noqa: E402

    w = WorkspaceClient(profile=args.profile)
    sql = merge_sql(table)
    for r in rows:
        run(w, args.warehouse_id, sql, [StatementParameterListItem(name=n, value=v) for n, v in parameters(r)])
    print(f"MERGE de {len(rows)} ofertas en {table}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", required=True, help="JSONL de ofertas aceptadas (CHAT_OFFERS_PATH)")
    p.add_argument("--gold-schema", default="workspace.gold_latam_bank")
    p.add_argument("--profile", default=None, help="perfil del CLI de Databricks")
    p.add_argument("--warehouse-id", default=None)
    p.add_argument("--apply", action="store_true", help="escribe en Databricks; sin esto solo muestra lo que subiria")
    main(p.parse_args())
