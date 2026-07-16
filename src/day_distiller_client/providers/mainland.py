from __future__ import annotations

import base64
import json
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from ..domain import MotionAssessment
from .base import BatchAnalysis, DailySynthesis, SceneAnalysis


T = TypeVar("T")


@dataclass(frozen=True)
class QwenSettings:
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    keyframe_model: str = "qwen3.7-plus"
    omni_model: str = "qwen3.5-omni-plus"


@dataclass(frozen=True)
class DeepSeekSettings:
    base_url: str = "https://api.deepseek.com"
    daily_model: str = "deepseek-v4-pro"


@dataclass(frozen=True)
class SeedreamSettings:
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    image_model: str = "doubao-seedream-5-0-pro"
    image_size: str = "2048x1365"
    response_format: str = "b64_json"


class QwenEvidenceProvider:
    """Two-stage evidence extraction: Omni batch pass, then Qwen 3.7 review."""

    def __init__(
        self,
        api_key: str | None = None,
        settings: QwenSettings | None = None,
        client: Any | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.settings = settings or QwenSettings()
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=self.settings.base_url)
        self.client = client
        self.max_attempts = max(1, max_attempts)

    def analyze_batch(self, frame_paths: list[Path], audio_path: Path | None) -> BatchAnalysis:
        content: list[dict[str, Any]] = [{"type": "text", "text": _OMNI_PROMPT}]
        for frame in frame_paths:
            content.append({"type": "image_url", "image_url": {"url": _data_url(frame)}})
        if audio_path:
            content.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": base64.b64encode(audio_path.read_bytes()).decode("ascii"),
                        "format": audio_path.suffix.lower().lstrip(".") or "wav",
                    },
                }
            )
        payload = self._json_chat(self.settings.omni_model, content)
        return BatchAnalysis.model_validate(payload)

    def analyze_scene(
        self,
        record_id: str,
        captured_at: datetime,
        frame_paths: list[Path],
        batch: BatchAnalysis,
        motion: MotionAssessment | None,
    ) -> SceneAnalysis:
        context = {
            "record_id": record_id,
            "captured_at": captured_at.isoformat(),
            "omni_evidence": batch.model_dump(),
            "local_imu": asdict(motion) if motion else None,
        }
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": _KEYFRAME_PROMPT + "\nINPUT JSON:\n" + json.dumps(context, ensure_ascii=False),
            }
        ]
        for frame in frame_paths:
            content.append({"type": "image_url", "image_url": {"url": _data_url(frame)}})
        payload = self._json_chat(self.settings.keyframe_model, content)
        return SceneAnalysis.model_validate(payload)

    def _json_chat(self, model: str, content: list[dict[str, Any]]) -> dict[str, Any]:
        def request() -> Any:
            return self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_object"},
            )

        response = _retry(request, self.max_attempts)
        raw = response.choices[0].message.content
        if not raw:
            raise RuntimeError(f"{model} returned an empty structured response")
        return json.loads(raw)


class DeepSeekStoryProvider:
    def __init__(
        self,
        api_key: str | None = None,
        settings: DeepSeekSettings | None = None,
        client: Any | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.settings = settings or DeepSeekSettings()
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=self.settings.base_url)
        self.client = client
        self.max_attempts = max(1, max_attempts)

    def synthesize_day(self, report_date: date, scenes: list[dict[str, object]]) -> DailySynthesis:
        payload = json.dumps(
            {
                "date": report_date.isoformat(),
                "constraints": {
                    "panel_count": "4-8, default 6",
                    "do_not_infer_between_capture_intervals": True,
                    "no_face_identification": True,
                    "omit_claims_below_confidence": 0.6,
                    "use_cautious_language_below_confidence": 0.8,
                    "image_prompts_must_not_request_text": True,
                },
                "scene_evidence": scenes,
            },
            ensure_ascii=False,
        )

        def request() -> Any:
            return self.client.chat.completions.create(
                model=self.settings.daily_model,
                messages=[
                    {"role": "system", "content": _DAILY_PROMPT},
                    {"role": "user", "content": payload},
                ],
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "enabled"}},
            )

        response = _retry(request, self.max_attempts)
        raw = response.choices[0].message.content
        if not raw:
            raise RuntimeError("DeepSeek returned an empty daily synthesis")
        return DailySynthesis.model_validate_json(raw)


class SeedreamImageProvider:
    def __init__(
        self,
        api_key: str | None = None,
        settings: SeedreamSettings | None = None,
        client: Any | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.settings = settings or SeedreamSettings()
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=self.settings.base_url)
        self.client = client
        self.max_attempts = max(1, max_attempts)

    def generate_panel(
        self,
        prompt: str,
        destination: Path,
        reference_images: list[Path] | None = None,
    ) -> Path:
        # Ark image endpoints differ by enabled model snapshot. Keep the endpoint/model
        # configurable and use the common Images API. Reference images are represented
        # in the prompt until the account's Seedream edit endpoint is configured.
        reference_note = " Keep the same recurring protagonist and visual identity."
        final_prompt = (
            "Modern cinematic diary comic, warm restrained colors, one coherent panel. "
            "No text, letters, subtitles, speech bubbles, watermark, or legible signage. "
            + prompt
            + (reference_note if reference_images else "")
        )

        def request() -> Any:
            return self.client.images.generate(
                model=self.settings.image_model,
                prompt=final_prompt,
                size=self.settings.image_size,
                response_format=self.settings.response_format,
            )

        result = _retry(request, self.max_attempts)
        item = result.data[0] if result.data else None
        if item is None:
            raise RuntimeError("Seedream response contained no image")
        if getattr(item, "b64_json", None):
            data = base64.b64decode(item.b64_json)
        elif getattr(item, "url", None):
            from urllib.request import urlopen

            with urlopen(item.url, timeout=60) as response:
                data = response.read()
        else:
            raise RuntimeError("Seedream response contained neither b64_json nor URL")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination


_OMNI_PROMPT = """You analyze one short wearable-camera record using all supplied frames and audio.
Return JSON only with the BatchAnalysis fields: transcript, ambient_sounds, ocr_text,
visual_observations, frame_scores, media_quality, privacy_flags. Transcribe speech verbatim;
describe non-speech sound events separately. OCR all useful visible Chinese or Latin text.
frame_scores must contain one 0..1 score for every supplied image in input order. Record only
direct observations. Do not identify people, invent place names, or infer activity between clips."""

_KEYFRAME_PROMPT = """You are the high-precision key-frame reviewer. Recheck the supplied frames
against Omni evidence and local IMU evidence. Return JSON only matching SceneAnalysis. Select the
indices of frames that best represent the event. OCR is primarily Omni's responsibility: preserve
confirmed OCR and only correct clear mistakes. Separate observation from inference, express evidence
strength as confidence, never identify a person, and never infer continuity outside this clip."""

_DAILY_PROMPT = """You are the evidence editor for a private Chinese daily diary. Resolve conflicts
between visual, audio, OCR and IMU evidence; build an ordered event narrative and 4-8 comic panels.
Return JSON only matching DailySynthesis. Use warm but restrained Chinese. State only supported facts,
use cautious wording for uncertain claims, omit weak guesses, and never request text inside images."""


def _data_url(path: Path) -> str:
    mime = {".png": "image/png", ".webp": "image/webp", ".wav": "audio/wav"}.get(
        path.suffix.lower(), "image/jpeg"
    )
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _retry(operation: Callable[[], T], attempts: int) -> T:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    assert error is not None
    raise error
