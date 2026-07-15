import json
import math
import tempfile
import unittest
from pathlib import Path

from day_distiller_client.imu_analysis import ImuDataError, analyze_imu, extract_features, load_imu


def write_imu(path: Path, activity: str, sample_rate: int = 104, duration_s: float = 5.0) -> None:
    samples = []
    count = int(sample_rate * duration_s)
    for index in range(count):
        t = index / sample_rate
        if activity == "walking":
            vertical_g = 1.0 + 0.18 * math.sin(2 * math.pi * 1.8 * t)
            gyro = int(700 * math.sin(2 * math.pi * 1.8 * t))
        else:
            vertical_g = 1.0
            gyro = 0
        samples.append(
            {
                "id": index,
                "time_us": int(t * 1_000_000),
                "time_s": t,
                "raw": {
                    "ax": 0,
                    "ay": 0,
                    "az": int(vertical_g * 16384),
                    "gx": gyro,
                    "gy": 0,
                    "gz": 0,
                },
                "pose": {"roll": 0, "pitch": 0, "yaw": 0},
            }
        )
    path.write_text(json.dumps({"sample_rate_hz": sample_rate, "samples": samples}), encoding="utf-8")


class ImuAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_stationary_classification(self) -> None:
        path = self.root / "stationary.json"
        write_imu(path, "stationary")
        result = analyze_imu(path)
        self.assertEqual(result.activity, "stationary")
        self.assertGreater(result.activity_confidence, 0.8)

    def test_walking_features_and_classification(self) -> None:
        path = self.root / "walking.json"
        write_imu(path, "walking")
        series = load_imu(path)
        features = extract_features(series)
        self.assertAlmostEqual(features["sample_rate_hz"], 104, delta=1)
        self.assertAlmostEqual(features["dominant_frequency_hz"], 1.8, delta=0.3)
        result = analyze_imu(path)
        self.assertEqual(result.activity, "walking")

    def test_rejects_too_few_samples(self) -> None:
        path = self.root / "short.json"
        path.write_text('{"samples":[]}', encoding="utf-8")
        with self.assertRaises(ImuDataError):
            load_imu(path)


if __name__ == "__main__":
    unittest.main()

