import hashlib
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = ("meta.json", "video.avi", "audio.wav", "imu.json")
RECORD = re.compile(r"^REC_\d{4}_\d{6}_\d{6}$")


def make_record(root: Path, name: str, marker: int = 1):
    directory = root / name
    directory.mkdir()
    sizes = {}
    for index, filename in enumerate(FILES, start=1):
        data = bytes([marker]) * index
        (directory / filename).write_bytes(data)
        sizes[filename] = len(data)
    return {"name": name, "files": sizes}


def preflight(root: Path, records, deleted_count=0, resuming=False):
    for index, record in enumerate(records):
        if not RECORD.fullmatch(record["name"]):
            raise ValueError("unsafe record name")
        directory = root / record["name"]
        if resuming and index < deleted_count:
            if directory.exists():
                raise ValueError("deleted record reappeared")
            continue
        allow_partial = resuming and index == deleted_count
        if not directory.exists():
            if allow_partial:
                continue
            raise ValueError("record missing")
        if {path.name for path in directory.iterdir()} - set(FILES):
            raise ValueError("unlisted file")
        for filename, size in record["files"].items():
            path = directory / filename
            if not path.exists():
                if allow_partial:
                    continue
                raise ValueError("file missing")
            if not path.is_file() or path.stat().st_size != size:
                raise ValueError("file changed")


def delete_exact(root: Path, record):
    directory = root / record["name"]
    if not directory.exists():
        return
    for filename, size in record["files"].items():
        path = directory / filename
        if path.exists():
            if not path.is_file() or path.stat().st_size != size:
                raise ValueError("file changed during commit")
            path.unlink()
    directory.rmdir()


class RecoverableTransaction:
    def __init__(self, root: Path, records):
        self.root = root
        self.records = records
        self.state = "prepared"
        self.deleted_count = 0
        encoded = repr(records).encode()
        self.sha256 = hashlib.sha256(encoded).hexdigest()

    def commit(self, sha256, count, crash_after_directory=None, crash_before_progress=False):
        if sha256 != self.sha256 or count != len(self.records):
            raise ValueError("confirmation mismatch")
        if self.state == "committed":
            return
        resuming = self.state == "committing"
        preflight(self.root, self.records, self.deleted_count, resuming)
        self.state = "committing"
        for index in range(self.deleted_count, len(self.records)):
            delete_exact(self.root, self.records[index])
            if crash_after_directory == index and crash_before_progress:
                raise InterruptedError
            self.deleted_count = index + 1
            if crash_after_directory == index:
                raise InterruptedError
        self.state = "committed"

    def abort(self):
        if self.state == "committing":
            raise ValueError("recovery required")
        if self.state == "prepared":
            self.state = "aborted"


class ExportDeleteContractTests(unittest.TestCase):
    def test_confirmation_mismatch_and_preflight_failure_delete_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [
                make_record(root, "REC_0001_260717_110000"),
                make_record(root, "REC_0002_260717_120000"),
            ]
            transaction = RecoverableTransaction(root, records)
            with self.assertRaises(ValueError):
                transaction.commit("0" * 64, 2)
            self.assertTrue(all((root / item["name"]).exists() for item in records))
            (root / records[1]["name"] / "unexpected.bin").write_bytes(b"x")
            with self.assertRaises(ValueError):
                transaction.commit(transaction.sha256, 2)
            self.assertTrue(all((root / item["name"]).exists() for item in records))
            self.assertEqual(transaction.state, "prepared")

    def test_every_directory_boundary_resumes_idempotently(self):
        for crash_index in range(3):
            for before_progress in (False, True):
                with self.subTest(crash_index=crash_index, before_progress=before_progress):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        records = [make_record(root, f"REC_{index:04d}_260717_1{index}0000")
                                   for index in range(3)]
                        transaction = RecoverableTransaction(root, records)
                        with self.assertRaises(InterruptedError):
                            transaction.commit(transaction.sha256, 3, crash_index, before_progress)
                        transaction.commit(transaction.sha256, 3)
                        transaction.commit(transaction.sha256, 3)
                        self.assertEqual(transaction.state, "committed")
                        self.assertEqual(transaction.deleted_count, 3)
                        self.assertTrue(all(not (root / item["name"]).exists() for item in records))

    def test_abort_is_idempotent_and_never_removes_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [make_record(root, "REC_0001_260717_110000")]
            transaction = RecoverableTransaction(root, records)
            transaction.abort()
            transaction.abort()
            self.assertEqual(transaction.state, "aborted")
            self.assertTrue((root / records[0]["name"]).is_dir())

    def test_one_date_commit_does_not_touch_another_date(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = [make_record(root, "REC_0001_260717_110000")]
            second = [make_record(root, "REC_0002_260718_110000", marker=2)]
            transaction = RecoverableTransaction(root, first)
            transaction.commit(transaction.sha256, 1)
            self.assertFalse((root / first[0]["name"]).exists())
            self.assertTrue((root / second[0]["name"]).is_dir())

    def test_path_whitelist_rejects_traversal_and_non_record_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "sentinel.txt"
            sentinel.write_text("keep", encoding="utf-8")
            for unsafe in ("../sentinel.txt", "/sdcard/REC_0001_260717_110000",
                           "REC_0001_260717_110000/child", "C:\\record", "REC_bad"):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaises(ValueError):
                        preflight(root, [{"name": unsafe, "files": {}}])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_resume_rejects_changes_after_current_progress_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [
                make_record(root, "REC_0001_260717_110000"),
                make_record(root, "REC_0002_260717_120000"),
            ]
            (root / records[0]["name"] / "meta.json").unlink()
            (root / records[1]["name"] / "meta.json").unlink()
            with self.assertRaises(ValueError):
                preflight(root, records, deleted_count=0, resuming=True)

    def test_firmware_orders_validation_before_state_and_deletion(self):
        source = (ROOT / "main/src/export/export_service.c").read_text(encoding="utf-8")
        commit = source[source.index("esp_err_t day_export_commit_delete("):]
        self.assertLess(commit.index("parse_verified_manifest("), commit.index("preflight_records("))
        self.assertLess(commit.index("preflight_records("), commit.index('strlcpy(info.state, "committing"'))
        self.assertLess(commit.index("write_transaction_state(&info, state_path)"),
                        commit.index("delete_one_record("))
        self.assertIn("json_fields_are_exact", source)
        self.assertIn("record_name_parse", source)
        self.assertIn("record_file_index", source)
        self.assertNotIn("rm -", source)
        usb_link = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        self.assertIn("transactional_export_v2", usb_link)
        self.assertIn("export_transactions", usb_link)

    def test_abort_firmware_path_has_no_delete_operations(self):
        source = (ROOT / "main/src/export/export_service.c").read_text(encoding="utf-8")
        abort = source[source.index("esp_err_t day_export_abort("):]
        self.assertNotIn("unlink(", abort)
        self.assertNotIn("rmdir(", abort)
        self.assertNotIn("delete_one_record", abort)


if __name__ == "__main__":
    unittest.main()
