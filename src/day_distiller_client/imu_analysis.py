from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .domain import MotionAssessment


ACCEL_LSB_PER_G = 16384.0
GYRO_DPS_PER_LSB = 0.00875
FEATURE_NAMES = (
    "duration_s",
    "sample_rate_hz",
    "dynamic_mean",
    "dynamic_std",
    "dynamic_rms",
    "jerk_rms",
    "gyro_rms",
    "gyro_std",
    "orientation_range",
    "dominant_frequency_hz",
    "periodicity",
    "vehicle_vibration_ratio",
    "spectral_entropy",
)


class ImuDataError(ValueError):
    pass


@dataclass(frozen=True)
class ImuSeries:
    time_s: np.ndarray
    acceleration_g: np.ndarray
    gyro_dps: np.ndarray
    pose_deg: np.ndarray | None


def load_imu(path: Path) -> ImuSeries:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImuDataError(f"cannot read IMU JSON: {path}") from exc
    samples = value.get("samples")
    if not isinstance(samples, list) or len(samples) < 20:
        raise ImuDataError("at least 20 IMU samples are required")
    times: list[float] = []
    acceleration: list[list[float]] = []
    gyro: list[list[float]] = []
    poses: list[list[float]] = []
    has_pose = True
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("raw"), dict):
            raise ImuDataError("invalid IMU sample")
        raw = sample["raw"]
        time_s = sample.get("time_s")
        if time_s is None:
            time_s = float(sample.get("time_us", 0)) / 1_000_000.0
        times.append(float(time_s))
        acceleration.append([float(raw[key]) / ACCEL_LSB_PER_G for key in ("ax", "ay", "az")])
        gyro.append([float(raw[key]) * GYRO_DPS_PER_LSB for key in ("gx", "gy", "gz")])
        pose = sample.get("pose")
        if isinstance(pose, dict) and all(key in pose for key in ("roll", "pitch", "yaw")):
            poses.append([float(pose[key]) for key in ("roll", "pitch", "yaw")])
        else:
            has_pose = False
    time_array = np.asarray(times, dtype=np.float64)
    if not np.all(np.isfinite(time_array)) or np.any(np.diff(time_array) <= 0):
        raise ImuDataError("IMU timestamps must be finite and strictly increasing")
    return ImuSeries(
        time_s=time_array,
        acceleration_g=np.asarray(acceleration, dtype=np.float64),
        gyro_dps=np.asarray(gyro, dtype=np.float64),
        pose_deg=np.asarray(poses, dtype=np.float64) if has_pose else None,
    )


def extract_features(series: ImuSeries) -> dict[str, float]:
    dt = np.diff(series.time_s)
    median_dt = float(np.median(dt))
    if median_dt <= 0:
        raise ImuDataError("invalid sample interval")
    sample_rate = 1.0 / median_dt
    acceleration_norm = np.linalg.norm(series.acceleration_g, axis=1)
    gravity_window = max(3, min(len(acceleration_norm) // 2 * 2 - 1, int(round(sample_rate * 0.5)) | 1))
    kernel = np.ones(gravity_window, dtype=np.float64) / gravity_window
    gravity = np.convolve(acceleration_norm, kernel, mode="same")
    edge = gravity_window // 2
    if edge:
        gravity[:edge] = gravity[edge]
        gravity[-edge:] = gravity[-edge - 1]
    dynamic = acceleration_norm - gravity
    jerk = np.diff(dynamic) / dt
    gyro_norm = np.linalg.norm(series.gyro_dps, axis=1)

    centered = dynamic - float(np.mean(dynamic))
    frequencies = np.fft.rfftfreq(len(centered), median_dt)
    power = np.abs(np.fft.rfft(centered)) ** 2
    power[0] = 0
    locomotion_mask = (frequencies >= 0.6) & (frequencies <= 4.0)
    vehicle_mask = (frequencies > 4.0) & (frequencies <= min(15.0, sample_rate / 2.0))
    total_power = float(np.sum(power)) + 1e-12
    if np.any(locomotion_mask):
        locomotion_power = power[locomotion_mask]
        locomotion_frequencies = frequencies[locomotion_mask]
        dominant_index = int(np.argmax(locomotion_power))
        dominant_frequency = float(locomotion_frequencies[dominant_index])
        periodicity = float(locomotion_power[dominant_index] / total_power)
    else:
        dominant_frequency = 0.0
        periodicity = 0.0
    normalized_power = power[power > 0] / total_power
    entropy = float(-np.sum(normalized_power * np.log2(normalized_power + 1e-12)))
    entropy /= math.log2(max(2, len(normalized_power)))

    orientation_range = 0.0
    if series.pose_deg is not None and len(series.pose_deg):
        orientation_range = float(np.mean(np.ptp(series.pose_deg, axis=0)))

    return {
        "duration_s": float(series.time_s[-1] - series.time_s[0]),
        "sample_rate_hz": sample_rate,
        "dynamic_mean": float(np.mean(dynamic)),
        "dynamic_std": float(np.std(dynamic)),
        "dynamic_rms": float(np.sqrt(np.mean(dynamic**2))),
        "jerk_rms": float(np.sqrt(np.mean(jerk**2))) if len(jerk) else 0.0,
        "gyro_rms": float(np.sqrt(np.mean(gyro_norm**2))),
        "gyro_std": float(np.std(gyro_norm)),
        "orientation_range": orientation_range,
        "dominant_frequency_hz": dominant_frequency,
        "periodicity": periodicity,
        "vehicle_vibration_ratio": float(np.sum(power[vehicle_mask]) / total_power) if np.any(vehicle_mask) else 0.0,
        "spectral_entropy": max(0.0, min(1.0, entropy)),
    }


def classify_heuristic(features: dict[str, float]) -> MotionAssessment:
    dynamic = features["dynamic_rms"]
    gyro = features["gyro_rms"]
    frequency = features["dominant_frequency_hz"]
    periodicity = features["periodicity"]
    orientation = features["orientation_range"]
    vehicle_ratio = features["vehicle_vibration_ratio"]

    if dynamic < 0.035 and gyro < 5.0:
        activity, activity_confidence = "stationary", min(0.97, 0.82 + (0.035 - dynamic) * 3.0)
    elif 1.8 <= frequency <= 4.0 and dynamic >= 0.20 and periodicity >= 0.18:
        activity, activity_confidence = "running", min(0.92, 0.65 + periodicity)
    elif 0.7 <= frequency <= 2.8 and dynamic >= 0.055 and periodicity >= 0.12:
        activity, activity_confidence = "walking", min(0.93, 0.65 + periodicity)
    elif vehicle_ratio >= 0.35 and periodicity < 0.15 and dynamic >= 0.025:
        activity, activity_confidence = "vehicle_like", min(0.82, 0.55 + vehicle_ratio * 0.5)
    elif gyro >= 20.0 or orientation >= 20.0:
        activity, activity_confidence = "handling", min(0.88, 0.58 + max(gyro / 250.0, orientation / 180.0))
    else:
        activity, activity_confidence = "unknown", 0.45

    if activity == "stationary":
        placement, placement_confidence = "surface", 0.72
    elif gyro >= 35.0 or orientation >= 35.0:
        placement, placement_confidence = "handheld", min(0.85, 0.55 + gyro / 300.0)
    elif activity in {"walking", "running"} and periodicity >= 0.20:
        placement, placement_confidence = "chest_fixed", min(0.78, 0.55 + periodicity)
    elif dynamic >= 0.08:
        placement, placement_confidence = "loose_carried", 0.58
    else:
        placement, placement_confidence = "unknown", 0.4

    return MotionAssessment(
        placement=placement,
        placement_confidence=float(placement_confidence),
        activity=activity,
        activity_confidence=float(activity_confidence),
        duration_s=features["duration_s"],
        sample_rate_hz=features["sample_rate_hz"],
        features=features,
    )


def analyze_imu(path: Path, model_path: Path | None = None) -> MotionAssessment:
    features = extract_features(load_imu(path))
    if model_path:
        try:
            return _classify_model(features, model_path)
        except Exception:
            pass
    return classify_heuristic(features)


def _classify_model(features: dict[str, float], model_path: Path) -> MotionAssessment:
    import joblib

    bundle: dict[str, Any] = joblib.load(model_path)
    names = tuple(bundle.get("feature_names", FEATURE_NAMES))
    row = np.asarray([[features[name] for name in names]], dtype=np.float64)
    placement_model = bundle["placement_model"]
    activity_model = bundle["activity_model"]
    placement_probabilities = placement_model.predict_proba(row)[0]
    activity_probabilities = activity_model.predict_proba(row)[0]
    placement_index = int(np.argmax(placement_probabilities))
    activity_index = int(np.argmax(activity_probabilities))
    return MotionAssessment(
        placement=str(placement_model.classes_[placement_index]),
        placement_confidence=float(placement_probabilities[placement_index]),
        activity=str(activity_model.classes_[activity_index]),
        activity_confidence=float(activity_probabilities[activity_index]),
        duration_s=features["duration_s"],
        sample_rate_hz=features["sample_rate_hz"],
        features=features,
        classifier_version=str(bundle.get("version", "trained-v1")),
    )

