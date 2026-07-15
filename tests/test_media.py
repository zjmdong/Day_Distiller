import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from day_distiller_client.domain import CaptureRecord
from day_distiller_client.media import MediaPreprocessor


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class MediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / "video.avi"
        self.audio = self.root / "audio.wav"
        subprocess.run(
            [
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2",
                "-c:v", "mjpeg", "-y", str(self.video),
            ],
            check=True,
        )
        subprocess.run(
            [
                shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:a", "pcm_s16le", "-y", str(self.audio),
            ],
            check=True,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_probe_and_extract_frames(self) -> None:
        record = CaptureRecord(
            record_id="record-1",
            record_name="REC_0001_260715_090000",
            captured_at=datetime.now().astimezone(),
            source_dir=self.root,
            local_dir=self.root,
            video_path=self.video,
            audio_path=self.audio,
        )
        result = MediaPreprocessor().preprocess_record(record, self.root / "cache")
        self.assertAlmostEqual(result.video_probe.duration_s, 2, delta=0.2)
        self.assertAlmostEqual(result.audio_probe.duration_s, 2, delta=0.2)
        self.assertGreaterEqual(len(result.frames), 1)
        self.assertTrue(all(path.is_file() for path in result.frames))


if __name__ == "__main__":
    unittest.main()

