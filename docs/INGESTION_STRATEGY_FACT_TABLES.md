# Architecture and Ingestion Strategy: S3 Landing to Medallion

## 1. Executive Summary

This document describes the data ingestion architecture used to transfer LATAM Bank datasets from the AWS S3 source bucket (`<s3-source-bucket>`) into Unity Catalog Volumes, through the Medallion pipeline (**Bronze** $\rightarrow$ **Silver** $\rightarrow$ **Gold**) within Databricks.

The architecture addresses both high-volume event tables (`transactions`, `campaign_sends`, `call_center_interactions`, `complaints`) and reference/dimension tables (`customers`, `products`, `branches`, `service_agents`, `marketing_campaigns`, `daily_exchange_rates`).

---

## 2. Ingestion Architecture: Why S3 Streaming Copy to Volume?

### 2.1 Credential & Network Constraints on Serverless Compute
In enterprise Databricks workspaces:
* Direct Serverless Auto Loader (`cloudFiles`) querying external AWS S3 buckets requires a Unity Catalog **Storage Credential** backed by an **AWS IAM Role ARN** with cross-account trust policies.
* The hackathon environment provides **IAM User Access Keys** (`aws-access-key-id`, `aws-secret-access-key`) securely mounted in the `aws-datathon` secret scope.
* Databricks Serverless Compute purposely disallows legacy Hadoop-level S3A configuration (`spark.hadoop.fs.s3a.access.key`).

### 2.2 Chosen Pattern: S3 Copy Task to Landing Volume
To work cleanly under these constraints without manual file uploads:
1. **Task 0 (`s3_copy`)**: Uses `boto3` on serverless compute with credentials fetched on the fly via `dbutils.secrets.get("aws-datathon", ...)`.
2. **Chunked Memory Streaming**: Consumes object bodies directly in 16 MB chunks and streams them into the Unity Catalog Volume mount (`/Volumes/workspace/staging_latam_bank/landing/<table_name>/`), bypassing the driver node's local `/tmp` disk to avoid disk exhaustion (`No space left on device`).
3. **Idempotence & Catch-Up**: Files already present in the target volume with identical byte sizes are skipped. New files and late-arriving partitions (e.g., partitioned dates in `call_center_interactions/`) are pulled automatically in subsequent runs.
4. **Partition Name Flattening**: Nested S3 keys (e.g. `data/transactions/year=2024/month=01/day=15/file.csv`) are mapped to `year=2024__month=01__day=15__file.csv` directly in the table folder, enabling single-directory bulk reads by downstream Spark loaders.

---

## 3. Medallion Pipeline Flow

```text
AWS S3 Source Bucket (s3://<s3-source-bucket>/data/)
  │
  │  Task: s3_copy (boto3, 16 MB chunks, aws-datathon secret scope)
  ▼
Landing Volume: /Volumes/workspace/staging_latam_bank/landing/<entity>/
  │
  │  Task: bronze_load (01_bronze.py - PySpark)
  │  - Explicit schema, all fields as STRING
  │  - _rescued_data enabled for schema drift detection
  │  - Metadata columns: _source_file_path, _bronze_ingested_at
  ▼
Bronze Tables: <catalog>.<bronze_schema>.<entity>
  │
  │  Task: silver_typed_dedup (02_silver.sql - Databricks SQL Warehouse)
  │  - Explicit typed casting (INT, DOUBLE, DATE, TIMESTAMP, BOOLEAN)
  │  - Deduplication via QUALIFY ROW_NUMBER() OVER (PARTITION BY <pk> ORDER BY <sort_key> DESC) = 1
  │    * Event/fact tables: ORDER BY process_date DESC (and timestamp tiebreakers)
  │    * Customer & product dimensions: ORDER BY last_updated DESC
  │    * Reference/lookup tables: ORDER BY _bronze_ingested_at DESC
  │  - Replaces SCD Type 2 intervals with deterministic latest-state representation
  │  - Ingests reference tables (load_silver_reference.sql)
  ▼
Silver Tables: <catalog>.<silver_schema>.<entity>
  │
  │  Task: silver_quality_checks (02_silver_quality_checks.sql)
  │  - Verifies key uniqueness, null thresholds, schema changes
  │  - Records metrics in pipeline_quality_metrics; stops pipeline on 'fail'
  ▼
Gold Engine: (10_customer_credit_profile.sql, 20_customer_credit_offer_options.sql, 30-33 summaries)
  │  - Evaluates credit eligibility (20% DTI capacity, policy 0.4 rules, risk bands)
  │  - Generates 1.8M credit offer alternatives
  │  - Rebuilds customer descriptive summaries
  ▼
Gold Tables: <catalog>.<gold_schema>.*
  │
  │  Task: gold_quality_checks & gold_export_for_serving
  ▼
Serving Export: /Volumes/<catalog>/<gold_schema>/exports/ (_manifest.json + Parquet)
```

---

## 4. Silver Design: Deduplication vs. SCD Type 2

### Why Deduplicated Silver rather than SCD Type 2?
The pipeline uses deterministic SQL deduplication (`ROW_NUMBER() = 1`) rather than Delta Live Tables SCD Type 2 (`__START_AT` / `__END_AT` intervals):
1. **Downstream Simplicity**: The Gold credit profile engine and backend rules evaluate customer state at a given evaluation date (`as_of_date`). Deduplicating in Silver guarantees exactly one current record per business key (`customer_id`, `product_id`, `transaction_id`), avoiding unwanted row multiplication during joins.
2. **Performance**: Deduplication in SQL warehouses is set-based, photon-accelerated, and eliminates stateful streaming checkpoints required by SCD Type 2 pipelines.
3. **Late-Arriving Data**: Reloads naturally absorb delayed files; sorting by business timestamp (`process_date DESC` for events, `last_updated DESC` for dimensions, `_bronze_ingested_at DESC` for lookups) ensures the most recent business state takes precedence regardless of ingestion order.

---

## 5. Operations & Current Ingestion Behavior

* **Transfer Mode**: Sequential processing across the 10 defined sources with chunked memory streaming per file.
* **Shared Landing Volume**: Both `dev` and `prod` targets write to the same landing volume (`/Volumes/workspace/staging_latam_bank/landing`); concurrent runs would write the same files.
* **Deletions**: Files deleted in the source S3 bucket remain in the landing volume; downstream table reloads re-ingest all landed files present in the volume directory.
* **Quality Assurance**: Data quality checks in `silver_quality_checks` and `gold_quality_checks` log validation metrics to `<gold_schema>.pipeline_quality_metrics` on every run.
