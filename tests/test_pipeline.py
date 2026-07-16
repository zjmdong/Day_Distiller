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
from day_distiller_client.providers.base import DeliveryResult


class _RejectingMailProvider:
    def send(self, subject, plain_text, html, pdf_path, inline_images, message_id):
        return DeliveryResult(False, message_id, "550 rejected")


class _UnexpectedAIProvider:
    def __getattr__(self, name):
        raise AssertionError(f"AI provider should not be called while resuming email stage: {name}")


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

            regenerated, regenerated_render = pipeline.regenerate(job_id)
            resent_render = pipeline.resend(job_id)
            self.assertEqual(regenerated.job_id, job_id)
            self.assertTrue(regenerated_render.pdf_path.is_file())
            self.assertTrue(resent_render.html_path.is_file())
            self.assertEqual(len(list((root / "outbox").glob("*.eml"))), 2)

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
                paths, database, mock, mock, mock, mock, MockMailProvider(root / "outbox")
            )

            job_id = pipeline.import_legacy(source, date(2026, 7, 15))
            result = pipeline.process(job_id, cleanup_source_root=source)

            self.assertEqual(result.cleanup_state, "completed")
            self.assertFalse(record.exists())

    def test_mail_rejection_never_invokes_cleanup(self) -> None:
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
                paths, database, mock, mock, mock, mock, _RejectingMailProvider()
            )
            cleanup_calls = []
            job_id = pipeline.import_legacy(source, date(2026, 7, 15))

            with self.assertRaisesRegex(RuntimeError, "未接受"):
                pipeline.process(
                    job_id,
                    cleanup_handler=lambda job, manifest: cleanup_calls.append((job, manifest)),
                )

            self.assertEqual(database.get_job(job_id).stage, JobStage.FAILED)
            self.assertEqual(cleanup_calls, [])
            self.assertTrue(record.is_dir())

            unexpected = _UnexpectedAIProvider()
            resumed = DistillationPipeline(
                paths,
                database,
                unexpected,
                unexpected,
                unexpected,
                unexpected,
                MockMailProvider(root / "outbox"),
            ).process(job_id)
            self.assertEqual(database.get_job(job_id).stage, JobStage.COMPLETED)
            self.assertTrue(resumed.rendered.pdf_path.is_file())


if __name__ == "__main__":
    unittest.main()
