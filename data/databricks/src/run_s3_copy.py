"""Databricks Asset Bundle task entrypoint: copy S3 objects to staging landing volume."""

import argparse
import inspect
import os
import sys


def get_script_dir() -> str:
    """Safely get script directory across standard execution, exec(), and Databricks ipykernel."""
    raw = globals().get("__file__")
    if raw:
        return os.path.dirname(os.path.abspath(raw))
    if sys.argv and sys.argv[0] and sys.argv[0].endswith(".py"):
        return os.path.dirname(os.path.abspath(sys.argv[0]))
    try:
        frame_file = inspect.getfile(inspect.currentframe())
        if frame_file and not frame_file.startswith("<"):
            return os.path.dirname(os.path.abspath(frame_file))
    except Exception:
        pass
    return os.path.abspath(os.getcwd())


SCRIPT_DIR = get_script_dir()
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from file_copy.s3_copy import run_copy


def resolve_config_path(raw_path: str | None) -> str:
    """Resolve configs/s3_sources.yml robustly across Databricks and local paths.

    Raises FileNotFoundError if the configuration file cannot be found.
    """
    if not raw_path:
        candidate = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "configs", "s3_sources.yml"))
        if os.path.isfile(candidate):
            return candidate
        raise FileNotFoundError(
            f"No config path provided and default candidate not found at '{candidate}'."
        )

    if os.path.isfile(raw_path):
        return os.path.abspath(raw_path)

    # Check relative to script directory
    candidate = os.path.abspath(os.path.join(SCRIPT_DIR, raw_path))
    if os.path.isfile(candidate):
        return candidate

    # Check relative to bundle root (parent of src/)
    candidate = os.path.abspath(os.path.join(SCRIPT_DIR, "..", raw_path.lstrip("/\\")))
    if os.path.isfile(candidate):
        return candidate

    # If raw path contains "configs/s3_sources.yml", extract subpath
    norm = raw_path.replace("\\", "/")
    if "configs/s3_sources.yml" in norm:
        subpath = norm[norm.find("configs/s3_sources.yml"):]
        candidate = os.path.abspath(os.path.join(SCRIPT_DIR, "..", subpath))
        if os.path.isfile(candidate):
            return candidate

    # If the path looks like a Databricks workspace path, return it directly so open() can try it
    if raw_path.startswith("/Workspace"):
        return raw_path

    raise FileNotFoundError(f"[run_s3_copy] Could not find configuration file at '{raw_path}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest LATAM Bank CSVs from S3 to landing volume")
    parser.add_argument("--config", default=None, help="Path to s3_sources.yml configuration file")
    parser.add_argument("--bucket", default=None, help="Override AWS S3 source bucket name")
    args = parser.parse_args()

    config_path = resolve_config_path(args.config)
    print(f"[run_s3_copy] Resolved config path: {config_path}")
    run_copy(config_path, bucket_override=args.bucket)
