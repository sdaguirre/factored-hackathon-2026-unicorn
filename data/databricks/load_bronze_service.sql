-- Loads complaints and call_center_interactions into bronze from the landing volume.
-- Same pattern as bronze.transactions / bronze.campaign_sends (CTAS from read_files,
-- partitioned by process_date), outside the latam-bank-ingestion pipeline.
-- Bronze = data as delivered: no dedup, no cleaning; types inferred from the CSVs,
-- columns merged by name across files (schema changes between partitions are tolerated),
-- unparseable values kept in _rescued_data.
-- Landing files: /Volumes/workspace/staging_latam_bank/landing/<table>/
--   year=YYYY__month=MM__day=DD__<table>_YYYYMMDD.csv  (one file per day, from S3 data/<table>/)

CREATE OR REPLACE TABLE workspace.bronze_latam_bank.call_center_interactions
PARTITIONED BY (process_date)
COMMENT 'Raw call center interactions (one CSV per day, June 2023 - June 2026) loaded as delivered from S3 data/call_center_interactions/. Not deduplicated. Lineage: _source_file_path, _bronze_ingested_at.'
AS SELECT
    *,
    _metadata.file_path  AS _source_file_path,
    current_timestamp()  AS _bronze_ingested_at
FROM read_files(
    '/Volumes/workspace/staging_latam_bank/landing/call_center_interactions/',
    format => 'csv', header => true, inferSchema => true, mergeSchema => true
);

CREATE OR REPLACE TABLE workspace.bronze_latam_bank.complaints
PARTITIONED BY (process_date)
COMMENT 'Raw customer complaints (one CSV per day, June 2023 - June 2026) loaded as delivered from S3 data/complaints/. Not deduplicated. Lineage: _source_file_path, _bronze_ingested_at.'
AS SELECT
    *,
    _metadata.file_path  AS _source_file_path,
    current_timestamp()  AS _bronze_ingested_at
FROM read_files(
    '/Volumes/workspace/staging_latam_bank/landing/complaints/',
    format => 'csv', header => true, inferSchema => true, mergeSchema => true
);
