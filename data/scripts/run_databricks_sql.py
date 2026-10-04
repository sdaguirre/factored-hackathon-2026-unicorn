"""Runs a SQL file statement by statement on a Databricks SQL warehouse.

Authentication uses a Databricks CLI profile (OAuth, `databricks auth login`); the `databricks`
CLI must be on PATH. Statements are split on ';' at line end.

Usage:
    python data/scripts/run_databricks_sql.py data/databricks/gold_credit_tables.sql --profile <profile> --warehouse-id <id>
"""
import argparse
import sys
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

sys.path.insert(0, str(Path(__file__).parent))
from load_reference import statements  # noqa: E402

RUNNING = (StatementState.PENDING, StatementState.RUNNING)


def run(w: WorkspaceClient, warehouse_id: str, sql: str):
    r = w.statement_execution.execute_statement(statement=sql, warehouse_id=warehouse_id, wait_timeout="50s")
    while r.status.state in RUNNING:
        time.sleep(5)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"{r.status.state}: {r.status.error.message if r.status.error else ''}\n{sql[:300]}")
    return r


def main(args):
    w = WorkspaceClient(profile=args.profile)
    for s in statements(Path(args.file).read_text(encoding="utf-8")):
        t0 = time.time()
        run(w, args.warehouse_id, s)
        print(f"ok ({time.time() - t0:.0f}s): {s.splitlines()[0][:100]}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    p.add_argument("--profile", default=None, help="Databricks CLI profile; default auth if omitted")
    p.add_argument("--warehouse-id", required=True)
    main(p.parse_args())
