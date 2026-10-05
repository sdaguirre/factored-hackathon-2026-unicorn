"""Exports the gold credit tables and the silver ref_* policy tables to local Parquet files.

The demo backend reads Parquet (no Databricks credentials in the container), and the data is
static, so the export runs once. Output goes to .local/gold/ (git-ignored: it is derived from
organizer data and must not be committed).

Large results are fetched as Arrow chunks through presigned links (EXTERNAL_LINKS), so the
1.8M-row offer options table does not go through JSON.

Usage:
    python data/scripts/export_gold.py --profile <profile> --warehouse-id <id>
    python data/scripts/export_gold.py --gold-schema workspace.gold_latam_bank_test \\
        --silver-schema workspace.silver_latam_bank_test --profile <profile> --warehouse-id <id>
"""
import argparse
import io
import time
import urllib.request
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState

ROOT = Path(__file__).resolve().parents[2]
GOLD_TABLES = ["customer_credit_profile", "customer_credit_offer_options"]
REF_TABLES = ["ref_product_catalog", "ref_term_grid", "ref_policy_params", "ref_policy_bands",
              "ref_segment_adjustments"]
RUNNING = (StatementState.PENDING, StatementState.RUNNING)


def fetch_arrow(w: WorkspaceClient, warehouse_id: str, sql: str) -> pa.Table:
    r = w.statement_execution.execute_statement(
        statement=sql, warehouse_id=warehouse_id, wait_timeout="50s",
        disposition=Disposition.EXTERNAL_LINKS, format=Format.ARROW_STREAM)
    while r.status.state in RUNNING:
        time.sleep(3)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"{r.status.state}: {r.status.error.message if r.status.error else ''}")
    batches, chunk = [], r.result
    while chunk is not None:
        for link in chunk.external_links or []:
            # Presigned URL: no Databricks auth header must be sent.
            with urllib.request.urlopen(link.external_link) as resp:
                batches.extend(ipc.open_stream(io.BytesIO(resp.read())))
        nxt = chunk.next_chunk_index if chunk.external_links else None
        if nxt is None and chunk.external_links:
            nxt = chunk.external_links[-1].next_chunk_index
        chunk = w.statement_execution.get_statement_result_chunk_n(r.statement_id, nxt) if nxt is not None else None
    if not batches:  # empty result: build an empty table with the manifest schema
        cols = r.manifest.schema.columns or []
        return pa.table({c.name: pa.array([], type=pa.string()) for c in cols})
    return pa.Table.from_batches(batches)


def main(args):
    w = WorkspaceClient(profile=args.profile)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    targets = [(f"{args.gold_schema}.{t}", t) for t in GOLD_TABLES] + \
              [(f"{args.silver_schema}.{t}", t) for t in REF_TABLES]
    for full_name, name in targets:
        t0 = time.time()
        table = fetch_arrow(w, args.warehouse_id, f"SELECT * FROM {full_name}")
        pq.write_table(table, out / f"{name}.parquet", compression="zstd")
        print(f"{name}: {table.num_rows:,} rows, {len(table.schema)} columns ({time.time() - t0:.0f}s)")
    print(f"Exported to {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--profile", default=None, help="Databricks CLI profile; default auth if omitted")
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--gold-schema", default="workspace.gold_latam_bank")
    p.add_argument("--silver-schema", default="workspace.silver_latam_bank")
    p.add_argument("--out", default=str(ROOT / ".local" / "gold"))
    main(p.parse_args())
