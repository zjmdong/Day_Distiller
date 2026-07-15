import json
import math
import tempfile
import unittest
from datetime import date
from pathlib import Path

from day_distiller_client.database import JobDatabase
from day_distiller_client.domain import JobStage
from day_distiller_client.paths import AppPaths
from day_distiller_client.pipeline import DistillationPipeline
from day_distiller_client.providers.mock import MockAIProvider, MockMailProvider


def _write_imu(path: Path) -> None:
    samples = []
    for index in range(100):
        samples.append(
            {
                "time_s": index / 20,
                "raw": {
                    "ax": int(20 * math.sin(index / 4)),
                    "ay": 0,
                    "az": 16384,
                    "gx": 0,
                    "gy": 0,
                    "gz": 0,
                },
            }
        )
    path.write_text(json.dumps({"samples": samples}), encoding="utf-8")


class PipelineTests(unittest.TestCase):
    def test_offline_end_to_end_keeps_source_when_cleanup_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "card"
            record = source / "REC_0001_260715_100000"
            record.mkdir(parents=True)
            _write_imu(record / "imu.json")
            (record / "meta.json").write_text('{"schema_version": 1}', encoding="utf-8")
            paths = AppPaths.from_root(root / "data").ensure()
            database = JobDatabase(paths.database)
            mock = MockAIProvider()
            pipeline = DistillationPipeline(
                paths,
                database,
                mock,
                mock,
                mock,
                MockMailProvider(root / "outbox"),
            )

            job_id = pipeline.import_legacy(source, date(2026, 7, 15))
            result = pipeline.process(job_id)

            self.assertEqual(database.get_job(job_id).stage, JobStage.COMPLETED)
            self.assertEqual(result.cleanup_state, "pending_cleanup")
            self.assertTrue(record.is_dir())
            self.assertTrue(result.rendered.html_path.is_file())
            self.assertGreater(result.rendered.pdf_path.stat().st_size, 0)
            self.assertEqual(len(list((root / "outbox").glob("*.eml"))), 1)
            self.assertEqual(len(database.list_scene_evidence(job_id)), 1)

    def test_verified_cleanup_runs_only_after_mock_mail_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "card"
            record = source / "REC_0001_260715_100000"
            record.mkdir(parents=True)
            _write_imu(record / "imu.json")
            paths = AppPaths.from_root(root / "data").ensure()
            database = JobDatabase(paths.database)
            mock = MockAIProvider()
            pipeline = DistillationPipeline(
                paths, database, mock, mock, mock, MockMailProvider(root / "outbox")
            )

            job_id = pipeline.import_legacy(source, date(2026, 7, 15))
            result = pipeline.process(job_id, cleanup_source_root=source)

            self.assertEqual(result.cleanup_state, "completed")
            self.assertFalse(record.exists())


if __name__ == "__main__":
    unittest.main()
