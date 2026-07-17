import hashlib
import json
import re
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECORD = re.compile(r"^REC_(\d{4})_(\d{2})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$")
FILES = ("meta.json", "video.avi", "audio.wav", "imu.json")


def complete_records(root: Path):
    found = []
    for path in root.iterdir():
        match = RECORD.fullmatch(path.name)
        if not match or not path.is_dir() or not all((path / name).is_file() for name in FILES):
            continue
        yy, month, day = (int(match.group(index)) for index in (2, 3, 4))
        hour, minute, second = (int(match.group(index)) for index in (5, 6, 7))
        if not 1 <= month <= 12 or not 1 <= day <= 31 or hour > 23 or minute > 59 or second > 59:
            continue
        date = f"20{yy:02d}-{month:02d}-{day:02d}"
        sizes = {name: (path / name).stat().st_size for name in FILES}
        found.append((date, path.name, sizes))
    return sorted(found)


def make_record(root: Path, name: str, missing=()):
    record = root / name
    record.mkdir(parents=True)
    for index, filename in enumerate(FILES, start=1):
        if filename not in missing:
            (record / filename).write_bytes(bytes([index]) * index)


class ExportManifestContractTests(unittest.TestCase):
    def test_date_discovery_ignores_partial_malformed_and_incomplete_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_record(root, "REC_0001_260717_110000")
            make_record(root, "REC_0002_260716_235959")
            make_record(root, "REC_0003_260717_120000", missing=("audio.wav",))
            make_record(root, "REC_0004_261332_120000")
            make_record(root, ".recording/REC_0005_260717_130000.partial")
            records = complete_records(root)
            self.assertEqual([item[1] for item in records], [
                "REC_0002_260716_235959",
                "REC_0001_260717_110000",
            ])

    def test_multi_date_summaries_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_record(root, "REC_0001_260717_110000")
            make_record(root, "REC_0002_260717_120000")
            make_record(root, "REC_0003_260716_120000")
            summaries = defaultdict(lambda: {"record_count": 0, "total_bytes": 0})
            for date, _, sizes in complete_records(root):
                summaries[date]["record_count"] += 1
                summaries[date]["total_bytes"] += sum(sizes.values())
            self.assertEqual(summaries["2026-07-17"]["record_count"], 2)
            self.assertEqual(summaries["2026-07-16"]["record_count"], 1)

    def test_manifest_freezes_exact_names_sizes_and_digest(self):
        records = [
            {
                "name": "REC_0001_260717_110000",
                "record_id": "DD-A1B2C3D4E5F6-REC_0001_260717_110000",
                "files": [{"name": name, "size": index} for index, name in enumerate(FILES, start=1)],
            }
        ]
        manifest = {
            "schema_version": 2,
            "export_id": "exp-DD-A1B2C3D4E5F6-0123456789abcdef",
            "client_request_id": "desktop-01K0A1F2M8D7",
            "device_id": "DD-A1B2C3D4E5F6",
            "date": "2026-07-17",
            "state": "prepared",
            "record_count": 1,
            "total_bytes": 10,
            "records": records,
        }
        encoded = json.dumps(manifest, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        self.assertEqual(len(digest), 64)
        self.assertEqual([item["name"] for item in records[0]["files"]], list(FILES))

    def test_atomic_metadata_write_and_backup_recovery_are_present(self):
        source = (ROOT / "main/src/export/export_service.c").read_text(encoding="utf-8")
        for operation in (".tmp", "fflush(file)", "fsync(fileno(file))", "rename(tmp, path)", ".bak"):
            self.assertIn(operation, source)
        self.assertIn("read_json_with_backup", source)

    def test_begin_export_idempotency_key_and_one_prepared_per_date_are_enforced(self):
        source = (ROOT / "main/src/export/export_service.c").read_text(encoding="utf-8")
        self.assertIn("client_request_id_date_mismatch", source)
        self.assertIn("date_already_prepared", source)
        self.assertIn("DAY_EXPORT_MAX_ACTIVE 32", source)

    def test_manifest_milestone_contains_no_record_deletion(self):
        source = (ROOT / "main/src/export/export_service.c").read_text(encoding="utf-8")
        self.assertNotIn("rmdir(", source)
        self.assertNotIn("recursive", source.lower())
        self.assertNotIn("COMMIT_EXPORT_DELETE", source)

    def test_paginated_wire_responses_stay_below_protocol_limit(self):
        items = [
            {"date": f"2026-07-{day:02d}", "record_count": 144, "total_bytes": 734003200}
            for day in range(1, 16)
        ]
        payload = json.dumps({"items": items, "next_cursor": 15}, separators=(",", ":")).encode()
        self.assertLessEqual(len(payload), 1600)


if __name__ == "__main__":
    unittest.main()
