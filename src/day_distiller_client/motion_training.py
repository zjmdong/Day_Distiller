from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import GroupShuffleSplit

from .imu_analysis import FEATURE_NAMES, extract_features, load_imu


@dataclass(frozen=True)
class LabeledFeatureSet:
    values: np.ndarray
    participants: np.ndarray
    placements: np.ndarray
    activities: np.ndarray


def load_labeled_features(csv_path: Path) -> LabeledFeatureSet:
    csv_path = Path(csv_path).resolve()
    rows: list[list[float]] = []
    participants: list[str] = []
    placements: list[str] = []
    activities: list[str] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"path", "participant_id", "placement", "activity"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"label CSV must contain: {', '.join(sorted(required))}")
        for item in reader:
            imu_path = (csv_path.parent / item["path"]).resolve()
            features = extract_features(load_imu(imu_path))
            rows.append([features[name] for name in FEATURE_NAMES])
            participants.append(item["participant_id"].strip())
            placements.append(item["placement"].strip())
            activities.append(item["activity"].strip())
    if len(rows) < 20:
        raise ValueError("at least 20 labeled recordings are required")
    if len(set(participants)) < 2:
        raise ValueError("at least two participants are required for person-isolated evaluation")
    return LabeledFeatureSet(
        values=np.asarray(rows, dtype=np.float64),
        participants=np.asarray(participants),
        placements=np.asarray(placements),
        activities=np.asarray(activities),
    )


def train_models(dataset: LabeledFeatureSet, output_path: Path) -> dict[str, object]:
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)
    train_index, test_index = next(splitter.split(dataset.values, groups=dataset.participants))
    common = dict(
        n_estimators=300,
        class_weight="balanced_subsample",
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    placement_model = RandomForestClassifier(**common)
    activity_model = RandomForestClassifier(**common)
    placement_model.fit(dataset.values[train_index], dataset.placements[train_index])
    activity_model.fit(dataset.values[train_index], dataset.activities[train_index])
    placement_prediction = placement_model.predict(dataset.values[test_index])
    activity_prediction = activity_model.predict(dataset.values[test_index])
    metrics: dict[str, object] = {
        "train_participants": sorted(set(dataset.participants[train_index].tolist())),
        "test_participants": sorted(set(dataset.participants[test_index].tolist())),
        "placement_accuracy": float(accuracy_score(dataset.placements[test_index], placement_prediction)),
        "activity_accuracy": float(accuracy_score(dataset.activities[test_index], activity_prediction)),
        "placement_report": classification_report(
            dataset.placements[test_index], placement_prediction, output_dict=True, zero_division=0
        ),
        "activity_report": classification_report(
            dataset.activities[test_index], activity_prediction, output_dict=True, zero_division=0
        ),
    }
    bundle = {
        "version": "random-forest-v1",
        "feature_names": FEATURE_NAMES,
        "placement_model": placement_model,
        "activity_model": activity_model,
        "metrics": metrics,
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, output_path)
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train Day Distiller multi-placement motion classifiers")
    parser.add_argument("labels", type=Path, help="CSV with path, participant_id, placement, activity")
    parser.add_argument("output", type=Path, help="Output .joblib model bundle")
    args = parser.parse_args(argv)
    dataset = load_labeled_features(args.labels)
    metrics = train_models(dataset, args.output)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

