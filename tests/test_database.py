import tempfile
import unittest
from datetime import date
from pathlib import Path

from day_distiller_client.database import JobDatabase
from day_distiller_client.domain import JobStage


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = JobDatabase(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_job_lifecycle_and_retry(self) -> None:
        job = self.db.create_job(date(2026, 7, 15), "mock")
        self.assertEqual(job.stage, JobStage.IMPORTING)

        self.db.set_stage(job.id, JobStage.VALIDATING, 0.2)
        self.assertEqual(self.db.get_job(job.id).stage, JobStage.VALIDATING)

        self.db.fail_job(job.id, "broken media")
        failed = self.db.get_job(job.id)
        self.assertEqual(failed.stage, JobStage.FAILED)
        self.assertEqual(failed.error, "broken media")

        retried = self.db.retry_job(job.id)
        self.assertEqual(retried.stage, JobStage.VALIDATING)
        self.assertIsNone(retried.error)

    def test_rejects_skipped_stage(self) -> None:
        job = self.db.create_job(date(2026, 7, 15))
        with self.assertRaises(ValueError):
            self.db.set_stage(job.id, JobStage.ANALYZING)

    def test_settings_round_trip(self) -> None:
        self.db.set_setting("models", {"vision": "example"})
        self.assertEqual(self.db.get_setting("models"), {"vision": "example"})
        self.assertEqual(self.db.get_setting("missing", 42), 42)


if __name__ == "__main__":
    unittest.main()

