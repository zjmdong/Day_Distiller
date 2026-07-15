import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np

from day_distiller_client.imu_analysis import FEATURE_NAMES
from day_distiller_client.motion_training import LabeledFeatureSet, train_models


class MotionTrainingTests(unittest.TestCase):
    def test_training_uses_person_isolated_split_and_writes_loadable_bundle(self) -> None:
        random = np.random.default_rng(42)
        rows = random.normal(size=(48, len(FEATURE_NAMES)))
        participants = np.asarray([f"p{index % 4}" for index in range(48)])
        placements = np.asarray(["handheld" if index % 2 else "chest_fixed" for index in range(48)])
        activities = np.asarray(["walking" if index % 3 else "stationary" for index in range(48)])
        dataset = LabeledFeatureSet(rows, participants, placements, activities)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "motion.joblib"

            metrics = train_models(dataset, destination)
            bundle = joblib.load(destination)

            self.assertTrue(set(metrics["train_participants"]).isdisjoint(metrics["test_participants"]))
            self.assertEqual(tuple(bundle["feature_names"]), FEATURE_NAMES)
            self.assertEqual(bundle["version"], "random-forest-v1")


if __name__ == "__main__":
    unittest.main()
