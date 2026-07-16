import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from day_distiller_client.legacy_import import (
    ImportVerificationError,
    available_record_dates,
    delete_verified_source_records,
    import_legacy_day,
    normalize_source_root,
    parse_record_datetime,
    scan_record_directories,
)


def make_record(root: Path, name: str, payload: bytes = b"video") -> Path:
    record = root / name
    record.mkdir()
    (record / "video.avi").write_bytes(payload)
    (record / "audio.wav").write_bytes(b"audio")
    (record / "imu.json").write_text('{"sample_rate_hz":104,"samples":[]}', encoding="utf-8")
    (record / "meta.json").write_text(json.dumps({"duration_s": 5}), encoding="utf-8")
    return record


class LegacyImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.destination = self.root / "destination"
        self.source.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parses_and_filters_legacy_directories(self) -> None:
        make_record(self.source, "REC_0001_260715_090000")
        make_record(self.source, "REC_0002_260716_090000")
        (self.source / "not-a-record").mkdir()

        self.assertEqual(parse_record_datetime("REC_0001_260715_090000").date(), date(2026, 7, 15))
        self.assertEqual(len(scan_record_directories(self.source, date(2026, 7, 15))), 1)
        self.assertEqual(available_record_dates(self.source), [date(2026, 7, 16), date(2026, 7, 15)])

    def test_import_is_verified_and_resumable(self) -> None:
        make_record(self.source, "REC_0001_260715_090000")
        records, manifest = import_legacy_day(
            self.source, date(2026, 7, 15), self.destination, "job-1", "device-1"
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(manifest.is_file())
        first_hashes = [item.sha256 for item in records[0].files]

        resumed, resumed_manifest = import_legacy_day(
            self.source, date(2026, 7, 15), self.destination, "job-1", "device-1"
        )
        self.assertEqual(resumed_manifest, manifest)
        self.assertEqual([item.sha256 for item in resumed[0].files], first_hashes)

    def test_reimporting_same_record_for_new_job_gets_new_record_id_and_manifest(self) -> None:
        make_record(self.source, "REC_0001_260715_090000")
        first, first_manifest = import_legacy_day(
            self.source, date(2026, 7, 15), self.destination, "job-1", "device-1"
        )
        second, second_manifest = import_legacy_day(
            self.source, date(2026, 7, 15), self.destination, "job-2", "device-1"
        )
        self.assertNotEqual(first[0].record_id, second[0].record_id)
        self.assertNotEqual(first_manifest, second_manifest)
        self.assertTrue(first_manifest.is_file())
        self.assertTrue(second_manifest.is_file())

    def test_normalize_source_root_accepts_explorer_quotes(self) -> None:
        self.assertEqual(normalize_source_root(f'"{self.source}"'), self.source)

    def test_normalize_source_root_rejects_nul(self) -> None:
        with self.assertRaisesRegex(ValueError, "无效字符"):
            normalize_source_root(str(self.source) + "\x00")

    def test_cleanup_requires_unchanged_source(self) -> None:
        record = make_record(self.source, "REC_0001_260715_090000")
        _records, manifest = import_legacy_day(
            self.source, date(2026, 7, 15), self.destination, "job-1"
        )
        (record / "video.avi").write_bytes(b"changed")
        with self.assertRaises(ImportVerificationError):
            delete_verified_source_records(self.source, manifest)
        self.assertTrue(record.exists())

    def test_cleanup_deletes_only_manifest_records(self) -> None:
        selected = make_record(self.source, "REC_0001_260715_090000")
        other = make_record(self.source, "REC_0002_260716_090000")
        _records, manifest = import_legacy_day(
            self.source, date(2026, 7, 15), self.destination, "job-1"
        )
        deleted = delete_verified_source_records(self.source, manifest)
        self.assertEqual(deleted, [selected.name])
        self.assertFalse(selected.exists())
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
