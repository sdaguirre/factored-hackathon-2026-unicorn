import io
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

# If pyyaml is not installed locally, mock safe_load for test_load_config_validation
try:
    import yaml
except ImportError:
    yaml = MagicMock()
    yaml.safe_load = lambda stream: {
        "s3_settings": {"bucket": "<s3-source-bucket>", "target_volume": "w.s.v"},
        "sources": [{"table": "test", "prefix": "data/", "pattern": "*.csv"}],
    }
    sys.modules["yaml"] = yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from file_copy.s3_copy import copy_table_files, load_config, split_volume_name


class FakeS3ObjectBody:
    def __init__(self, data: bytes):
        self._io = io.BytesIO(data)

    def iter_chunks(self, chunk_size: int = 16 * 1024 * 1024):
        while True:
            chunk = self._io.read(chunk_size)
            if not chunk:
                break
            yield chunk


class FakeS3Client:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def get_paginator(self, operation_name: str):
        assert operation_name == "list_objects_v2"
        return FakeS3Paginator(self.objects)

    def get_object(self, Bucket: str, Key: str):
        if Key not in self.objects:
            raise KeyError(f"Key {Key} not found in fake bucket {Bucket}")
        return {"Body": FakeS3ObjectBody(self.objects[Key])}


class FakeS3Paginator:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def paginate(self, Bucket: str, Prefix: str):
        matching = []
        for key, content in self.objects.items():
            if key.startswith(Prefix):
                matching.append({"Key": key, "Size": len(content)})
        return [{"Contents": matching}]


class TestS3Copy(unittest.TestCase):
    def test_split_volume_name(self):
        catalog, schema, volume = split_volume_name("workspace.staging_latam_bank.landing")
        self.assertEqual(catalog, "workspace")
        self.assertEqual(schema, "staging_latam_bank")
        self.assertEqual(volume, "landing")

        with self.assertRaises(ValueError):
            split_volume_name("invalid_name")

    def test_load_config_validation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Missing file raises FileNotFoundError
            with self.assertRaises(FileNotFoundError):
                load_config(os.path.join(tmp_dir, "nonexistent.yml"))

            # Config with placeholder bucket and no override raises ValueError
            config_file = os.path.join(tmp_dir, "config.yml")
            with open(config_file, "w", encoding="utf-8") as f:
                f.write(
                    "s3_settings:\n  bucket: '<s3-source-bucket>'\n  target_volume: 'w.s.v'\n"
                    "sources:\n  - table: test\n    prefix: data/\n    pattern: '*.csv'\n"
                )

            with self.assertRaises(ValueError):
                load_config(config_file)

            # Config with bucket override succeeds
            settings, sources = load_config(config_file, bucket_override="my-real-bucket")
            self.assertEqual(settings["bucket"], "my-real-bucket")
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["table"], "test")

    def test_copy_table_files_partition_flattening_and_skip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            payload = b"id,val\n1,foo\n"
            fake_s3 = FakeS3Client({
                "data/transactions/year=2024/month=01/day=15/part1.csv": payload,
                "data/transactions/year=2024/month=01/day=16/part2.csv": payload,
            })

            dest_dir = os.path.join(tmp_dir, "transactions")

            # First copy: both files should be copied and flattened
            copied, skipped = copy_table_files(
                fake_s3,
                bucket="test-bucket",
                destination_dir=dest_dir,
                prefix="data/transactions/",
                pattern="*.csv",
            )
            self.assertEqual(copied, 2)
            self.assertEqual(skipped, 0)

            expected_file1 = os.path.join(dest_dir, "year=2024__month=01__day=15__part1.csv")
            expected_file2 = os.path.join(dest_dir, "year=2024__month=01__day=16__part2.csv")
            self.assertTrue(os.path.isfile(expected_file1))
            self.assertTrue(os.path.isfile(expected_file2))
            with open(expected_file1, "rb") as f:
                self.assertEqual(f.read(), payload)

            # Second copy: identical size -> both files should be skipped (idempotent)
            copied2, skipped2 = copy_table_files(
                fake_s3,
                bucket="test-bucket",
                destination_dir=dest_dir,
                prefix="data/transactions/",
                pattern="*.csv",
            )
            self.assertEqual(copied2, 0)
            self.assertEqual(skipped2, 2)

    def test_copy_table_files_exact_key_single_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            customer_payload = b"customer_id,name\n123,Alice\n"
            fake_s3 = FakeS3Client({
                "data/customers.csv": customer_payload,
                "data/other.csv": b"unrelated",
            })

            dest_dir = os.path.join(tmp_dir, "customers")

            copied, skipped = copy_table_files(
                fake_s3,
                bucket="test-bucket",
                destination_dir=dest_dir,
                prefix="data/customers.csv",
                pattern="customers.csv",
            )
            self.assertEqual(copied, 1)
            self.assertEqual(skipped, 0)

            target_file = os.path.join(dest_dir, "customers.csv")
            self.assertTrue(os.path.isfile(target_file))
            with open(target_file, "rb") as f:
                self.assertEqual(f.read(), customer_payload)


if __name__ == "__main__":
    unittest.main()
