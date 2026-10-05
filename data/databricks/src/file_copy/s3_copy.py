"""Copy objects from AWS S3 into a Unity Catalog volume.

Files land under /Volumes/<catalog>/<schema>/<volume>/<table_name>/
for consumption by 01_bronze.py. A file already present in the volume
with the same size is skipped. Streaming uses 16 MB chunks directly into
the volume path to eliminate driver disk pressure.
"""

import fnmatch
import os


def get_secret(scope: str, key: str) -> str:
    """Retrieve secret from Databricks dbutils or environment fallback.

    Re-raises original Databricks dbutils exceptions when running on Databricks.
    Only falls back to environment variables when pyspark/dbutils is not available locally.
    """
    try:
        from pyspark.dbutils import DBUtils
        from pyspark.sql import SparkSession

        spark = SparkSession.builder.getOrCreate()
        dbutils = DBUtils(spark)
    except (ImportError, AttributeError):
        env_key = f"{scope}__{key}".upper().replace("-", "_")
        value = os.environ.get(env_key)
        if value is None:
            raise RuntimeError(
                f"Running locally without Databricks dbutils, and environment "
                f"variable '{env_key}' is not set."
            )
        return value

    try:
        return dbutils.secrets.get(scope=scope, key=key)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to retrieve secret '{scope}/{key}' from Databricks secret scope: {exc}"
        ) from exc


def split_volume_name(volume_name: str) -> tuple[str, str, str]:
    """Parse catalog.schema.volume into separate parts."""
    parts = str(volume_name).split(".")
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            f"target_volume must be formatted as 'catalog.schema.volume', got '{volume_name}'"
        )
    return parts[0], parts[1], parts[2]


def load_config(config_path: str | None, bucket_override: str | None = None) -> tuple[dict, list[dict]]:
    """Load S3 settings and source definitions from YAML file.

    Fails fast if the file is missing, invalid, or lacks required configuration.
    """
    if not config_path or not os.path.exists(config_path):
        raise FileNotFoundError(
            f"S3 sources configuration file not found at '{config_path}'. "
            "Ensure the YAML config is packaged with the bundle."
        )

    import yaml

    with open(config_path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    s3_settings = data.get("s3_settings")
    if not s3_settings or not isinstance(s3_settings, dict):
        raise ValueError(f"Missing or invalid 's3_settings' section in '{config_path}'.")

    sources = data.get("sources")
    if not sources or not isinstance(sources, list):
        raise ValueError(f"Missing or invalid 'sources' section in '{config_path}'.")

    if bucket_override and bucket_override.strip() and not bucket_override.startswith("{{"):
        s3_settings["bucket"] = bucket_override.strip()

    bucket = s3_settings.get("bucket", "").strip()
    if not bucket or bucket.startswith("<") or bucket.startswith("{{"):
        raise ValueError(
            f"S3 bucket name must be configured via --bucket or in '{config_path}' (got: {bucket!r})."
        )

    print(f"[s3_copy] Loaded configuration from {config_path} (bucket: {bucket})")
    return s3_settings, sources


def build_s3_client(settings: dict):
    """Build boto3 S3 client using credentials from secret scope."""
    import boto3

    access_key = get_secret(settings["secret_scope"], settings["access_key_secret"])
    secret_key = get_secret(settings["secret_scope"], settings["secret_key_secret"])

    return boto3.client(
        "s3",
        region_name=settings.get("region", "us-east-2"),
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def copy_table_files(s3_client, bucket: str, destination_dir: str, prefix: str, pattern: str) -> tuple[int, int]:
    """Copy matching S3 objects into destination_dir using 16 MB streaming chunks."""
    os.makedirs(destination_dir, exist_ok=True)
    copied = 0
    skipped = 0

    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue

            file_name = key.rsplit("/", 1)[-1]
            if not fnmatch.fnmatch(file_name, pattern):
                continue

            if prefix.endswith("/"):
                relative = key[len(prefix):]
                flat_name = relative.lstrip("/").replace("/", "__")
            else:
                flat_name = file_name

            if not flat_name:
                flat_name = file_name

            target_path = os.path.join(destination_dir, flat_name)

            if os.path.exists(target_path) and os.path.getsize(target_path) == obj["Size"]:
                skipped += 1
                continue

            response = s3_client.get_object(Bucket=bucket, Key=key)
            with open(target_path, "wb") as f_out:
                for chunk in response["Body"].iter_chunks(chunk_size=16 * 1024 * 1024):
                    f_out.write(chunk)
            copied += 1

    return copied, skipped


def run_copy(config_path: str | None = None, bucket_override: str | None = None):
    """Orchestrate S3 copy for all defined entities."""
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    s3_settings, sources = load_config(config_path, bucket_override=bucket_override)

    catalog, schema, volume = split_volume_name(s3_settings["target_volume"])
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`")
    spark.sql(f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{schema}`.`{volume}`")

    base_volume_path = f"/Volumes/{catalog}/{schema}/{volume}"
    bucket = s3_settings["bucket"]

    print(f"[s3_copy] Starting S3 copy from bucket '{bucket}' to '{base_volume_path}'")
    s3_client = build_s3_client(s3_settings)

    total_copied = 0
    total_skipped = 0

    for source in sources:
        table_name = source["table"]
        prefix = source.get("prefix", "data/")
        pattern = source.get("pattern", "*.csv")
        target_dir = os.path.join(base_volume_path, table_name)

        copied, skipped = copy_table_files(s3_client, bucket, target_dir, prefix, pattern)
        total_copied += copied
        total_skipped += skipped
        print(f"  -> {table_name:25s}: copied={copied:<4d} skipped={skipped:<4d} -> {target_dir}")

    print(f"[s3_copy] Finished: {total_copied} copied, {total_skipped} skipped (already up to date).")
