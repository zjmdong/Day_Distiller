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
    image_size: str = "2048x1536"
    response_format: str = "b64_json"
    character_description: str = ""


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
                        # Qwen Omni's OpenAI-compatible endpoint treats a naked
                        # Base64 string as a URL.  Local audio must use a data URI.
                        "data": "data:;base64,"
                        + base64.b64encode(audio_path.read_bytes()).decode("ascii"),
                        "format": audio_path.suffix.lower().lstrip(".") or "wav",
                    },
                }
            )
        payload = self._json_chat(self.settings.omni_model, content, omni=True)
        return BatchAnalysis.model_validate(_normalize_batch_payload(payload, len(frame_paths)))

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
        payload = self._json_chat(self.settings.keyframe_model, content, omni=False)
        return SceneAnalysis.model_validate(_normalize_scene_payload(payload, len(frame_paths)))

    def _json_chat(
        self, model: str, content: list[dict[str, Any]], omni: bool
    ) -> dict[str, Any]:
        def request() -> Any:
            arguments: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "response_format": {"type": "json_object"},
                "extra_body": {"enable_thinking": False},
            }
            if omni:
                arguments.update(
                    {
                        "stream": True,
                        "stream_options": {"include_usage": True},
                        "modalities": ["text"],
                    }
                )
            return self.client.chat.completions.create(**arguments)

        response = _retry(request, self.max_attempts)
        if omni:
            parts: list[str] = []
            for chunk in response:
                if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
                    parts.append(chunk.choices[0].delta.content)
            raw = "".join(parts)
        else:
            raw = response.choices[0].message.content
        if not raw:
            raise RuntimeError(f"{model} returned an empty structured response")
        return _parse_json_payload(raw, model)


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
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
            )

        response = _retry(request, self.max_attempts)
        raw = response.choices[0].message.content
        if not raw:
            raise RuntimeError("DeepSeek returned an empty daily synthesis")
        parsed = _parse_json_payload(raw, self.settings.daily_model)
        return DailySynthesis.model_validate(_normalize_daily_payload(parsed, scenes, report_date))


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
        if self.settings.character_description.strip():
            reference_note += " Character description: " + self.settings.character_description.strip()
        final_prompt = (
            "Modern cinematic diary comic, warm restrained colors, one coherent panel. "
            "No text, letters, subtitles, speech bubbles, watermark, or legible signage. "
            + prompt
            + (reference_note if reference_images else "")
        )

        def request() -> Any:
            extra_body: dict[str, Any] = {
                "watermark": False,
            }
            if reference_images:
                extra_body["image"] = [_data_url(Path(path)) for path in reference_images]
            return self.client.images.generate(
                model=self.settings.image_model,
                prompt=final_prompt,
                size=self.settings.image_size,
                response_format=self.settings.response_format,
                extra_body=extra_body,
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
Return exactly one JSON object, without Markdown fences, using this shape:
{"transcript":"", "ambient_sounds":[""], "ocr_text":[""],
"visual_observations":[""], "frame_scores":[0.0], "media_quality":0.0,
"privacy_flags":[""]}.
All list fields must always be JSON arrays. frame_scores and media_quality must contain only
numbers from 0.0 to 1.0; frame_scores must have exactly one item per supplied image.
Transcribe speech verbatim; describe non-speech sound events separately. OCR all useful visible
Chinese or Latin text. Record only direct observations. Do not identify people, invent place names,
or infer activity between clips."""

_KEYFRAME_PROMPT = """You are the high-precision key-frame reviewer. Recheck the supplied frames
against Omni evidence and local IMU evidence. Return exactly one JSON object without Markdown.
Required fields and types: summary:string, location_candidate:string|null,
location_confidence:number, visual_activity:string, visual_activity_confidence:number,
semantic_significance:number, novelty:number, audio_value:number, memory_relevance:number,
media_quality:number, claims:array, privacy_flags:array, ocr_text:array,
ambient_sounds:array, selected_frame_indices:array of integers. Every confidence and score must
be a number from 0.0 to 1.0. Select the
indices of frames that best represent the event. OCR is primarily Omni's responsibility: preserve
confirmed OCR and only correct clear mistakes. Separate observation from inference, express evidence
strength as confidence, never identify a person, and never infer continuity outside this clip."""

_DAILY_PROMPT = """You are the evidence editor for a private Chinese daily diary. Resolve conflicts
between visual, audio, OCR and IMU evidence; build an ordered event narrative and 4-8 comic panels.
Return exactly one JSON object without Markdown, using precisely this shape:
{"title":"", "one_sentence_summary":"", "narrative":"",
"panels":[{"record_ids":["record UUID"], "time_label":"HH:MM",
"caption":"Chinese caption", "image_prompt":"English visual prompt without any text"}]}.
Do not rename, omit, or add fields. record_ids must be a JSON array containing only record_id values
present in the supplied scene evidence. Use warm but restrained Chinese. State only supported facts,
use cautious wording for uncertain claims, omit weak guesses, and never request text inside images."""


def _data_url(path: Path) -> str:
    mime = {".png": "image/png", ".webp": "image/webp", ".wav": "audio/wav"}.get(
        path.suffix.lower(), "image/jpeg"
    )
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _parse_json_payload(raw: str, model: str) -> dict[str, Any]:
    """Extract one JSON object while tolerating common Markdown wrappers.

    Some compatible endpoints append a closing Markdown fence even when
    ``response_format=json_object`` is requested.  ``raw_decode`` preserves
    strict parsing of the first complete JSON value without relying on a
    fragile regular expression or altering string contents inside the JSON.
    """
    candidate = raw.strip()
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        if first_newline < 0:
            raise RuntimeError(f"{model} returned a JSON code fence without content")
        candidate = candidate[first_newline + 1 :].lstrip()
    start = min((index for index in (candidate.find("{"), candidate.find("[")) if index >= 0), default=-1)
    if start < 0:
        raise RuntimeError(f"{model} returned no JSON value")
    try:
        value, _end = json.JSONDecoder().raw_decode(candidate[start:])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{model} returned invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{model} returned a JSON value that is not an object")
    return value


def _string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _score(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _normalize_batch_payload(payload: dict[str, Any], frame_count: int) -> dict[str, Any]:
    value = dict(payload)
    value["transcript"] = str(value.get("transcript") or "")
    for field in ("ambient_sounds", "ocr_text", "visual_observations", "privacy_flags"):
        value[field] = _string_list(value.get(field))
    raw_scores = value.get("frame_scores")
    if not isinstance(raw_scores, list):
        raw_scores = []
    scores = [_score(item) for item in raw_scores[:frame_count]]
    scores.extend([0.5] * max(0, frame_count - len(scores)))
    value["frame_scores"] = scores
    value["media_quality"] = _score(value.get("media_quality"))
    return value


def _normalize_scene_payload(payload: dict[str, Any], frame_count: int) -> dict[str, Any]:
    value = dict(payload)
    value["summary"] = str(value.get("summary") or "未获得可靠的场景描述")
    value["visual_activity"] = str(value.get("visual_activity") or "unknown")
    location = value.get("location_candidate")
    value["location_candidate"] = str(location).strip() if location not in (None, "") else None
    for field, default in (
        ("location_confidence", 0.0),
        ("visual_activity_confidence", 0.0),
        ("semantic_significance", 0.5),
        ("novelty", 0.5),
        ("audio_value", 0.5),
        ("memory_relevance", 0.0),
        ("media_quality", 0.5),
    ):
        value[field] = _score(value.get(field), default)
    for field in ("privacy_flags", "ocr_text", "ambient_sounds"):
        value[field] = _string_list(value.get(field))
    raw_indices = value.get("selected_frame_indices")
    if not isinstance(raw_indices, list):
        raw_indices = []
    indices: list[int] = []
    for item in raw_indices:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < frame_count and index not in indices:
            indices.append(index)
    value["selected_frame_indices"] = indices
    raw_claims = value.get("claims")
    if isinstance(raw_claims, dict):
        raw_claims = [raw_claims]
    claims: list[dict[str, Any]] = []
    for item in raw_claims if isinstance(raw_claims, list) else []:
        if not isinstance(item, dict) or not item.get("claim"):
            continue
        claims.append(
            {
                "claim": str(item["claim"]),
                "evidence_type": str(item.get("evidence_type") or "multimodal"),
                "evidence_ids": _string_list(item.get("evidence_ids")),
                "confidence": _score(item.get("confidence")),
            }
        )
    value["claims"] = claims
    return value


def _normalize_daily_payload(
    payload: dict[str, Any], scenes: list[dict[str, object]], report_date: date
) -> dict[str, Any]:
    value = dict(payload)
    narrative = str(value.get("narrative") or value.get("description") or "").strip()
    title = str(value.get("title") or f"{report_date.isoformat()} 每日蒸馏").strip()
    summary = str(
        value.get("one_sentence_summary")
        or value.get("summary")
        or (narrative[:80] if narrative else "今天留下了几段值得回看的记录。")
    ).strip()
    scene_by_id = {
        str(item.get("record_id")): item for item in scenes if item.get("record_id")
    }
    scene_by_time: dict[str, list[str]] = {}
    for record_id, item in scene_by_id.items():
        time_label = str(item.get("time_label") or "")
        if not time_label and item.get("captured_at"):
            try:
                time_label = datetime.fromisoformat(str(item["captured_at"])).strftime("%H:%M")
            except ValueError:
                time_label = ""
        if time_label:
            scene_by_time.setdefault(time_label, []).append(record_id)

    raw_panels = value.get("panels")
    panels: list[dict[str, Any]] = []
    for index, item in enumerate(raw_panels if isinstance(raw_panels, list) else []):
        if not isinstance(item, dict):
            continue
        time_label = str(item.get("time_label") or item.get("time") or "").strip()
        raw_ids = item.get("record_ids")
        if raw_ids is None:
            raw_ids = item.get("record_id") or item.get("evidence_ids")
        record_ids = [record_id for record_id in _string_list(raw_ids) if record_id in scene_by_id]
        if not record_ids and time_label:
            record_ids = scene_by_time.get(time_label, [])
        if not record_ids and scene_by_id:
            record_ids = [list(scene_by_id)[min(index, len(scene_by_id) - 1)]]
        caption = str(
            item.get("caption") or item.get("title") or item.get("description") or "这一刻"
        ).strip()
        image_prompt = str(
            item.get("image_prompt")
            or item.get("visual_prompt")
            or item.get("description")
            or caption
        ).strip()
        panels.append(
            {
                "record_ids": record_ids,
                "time_label": time_label or "--:--",
                "caption": caption,
                "image_prompt": image_prompt,
            }
        )
        if len(panels) >= 8:
            break
    if not panels and scene_by_id:
        for record_id, scene in list(scene_by_id.items())[:6]:
            panels.append(
                {
                    "record_ids": [record_id],
                    "time_label": str(scene.get("time_label") or "--:--"),
                    "caption": str(scene.get("summary") or "这一刻"),
                    "image_prompt": str(scene.get("summary") or "A quiet diary moment"),
                }
            )
    return {
        "title": title,
        "one_sentence_summary": summary,
        "narrative": narrative or summary,
        "panels": panels,
    }


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
