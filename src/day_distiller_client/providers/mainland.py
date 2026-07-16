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
    # Exact 3:4, 1K-class output (995,328 pixels). This is deliberately well
    # below the 2.2M ceiling so small provider-side rounding cannot cross tiers.
    image_size: str = "864x1152"
    response_format: str = "b64_json"
    character_description: str = ""
    art_style_name: str = "扁平色块波普"
    art_style_prompt: str = (
        "Minimal flat-color pop illustration using large saturated color blocks, clean rounded "
        "contours, simplified facial features and material details, with a lively premium palette."
    )


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
                    "output_type": "one vertical daily poster",
                    "selected_scene_count": "2-3 when at least two usable scenes exist",
                    "do_not_infer_between_capture_intervals": True,
                    "no_face_identification": True,
                    "omit_claims_below_confidence": 0.6,
                    "use_cautious_language_below_confidence": 0.8,
                    "image_prompts_must_not_request_text": True,
                    "poster_has_no_text": True,
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
        _validate_poster_image_size(self.settings.image_size)
        references = [Path(path) for path in (reference_images or []) if Path(path).is_file()][:3]
        reference_note = (
            f" Use all {len(references)} supplied source frames as factual visual references."
            if references
            else ""
        )
        if self.settings.character_description.strip():
            reference_note += (
                " When the recurring protagonist is actually visible, use this non-biometric style note: "
                + self.settings.character_description.strip()
                + "."
            )
        final_prompt = (
            "Create ONE finished vertical 3:4 daily-memory poster, not separate outputs. "
            "Selectively fuse the referenced real moments into one cohesive editorial composition "
            "with a clear visual hierarchy and seamless transitions between two or three scenes. "
            f"The selected art direction is '{self.settings.art_style_name}'. Follow this art direction "
            f"precisely: {self.settings.art_style_prompt.strip()} "
            "Preserve the recognizable actions, environment and personal details supported by the "
            "references, while artistically simplifying them. When the same protagonist appears in "
            "multiple references, keep their visual identity consistent and make any repeated depiction "
            "read clearly as a montage across moments, not as invented extra people. Do not invent extra events. "
            "The poster must be pure image: absolutely no text, letters, numbers, captions, subtitles, "
            "speech bubbles, logos, watermark, frames with written labels, or legible signage. "
            + prompt
            + reference_note
        )

        def request() -> Any:
            extra_body: dict[str, Any] = {
                "watermark": False,
            }
            if references:
                extra_body["image"] = [_data_url(path) for path in references]
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

_DAILY_PROMPT = """You are the evidence editor and art director for a private Chinese daily diary.
Review ALL supplied visual, audio, OCR and IMU evidence. First mentally cluster adjacent or similar
captures into meaningful Moments instead of treating every frame as an independent event. Select only
the 2-3 Moments that are most valuable, special, emotionally meaningful, visually distinctive, or
representative of the day's central theme. Prioritize supported signs of intentional capture, meaningful
interactions, a surprising change, personal milestones, and small moments that would be worth remembering.
Do not choose a scene merely because it is technically sharp. Avoid near-duplicates and routine filler.
If only one usable Moment exists, select it. Make the chosen Moments form a coherent emotional arc.
Create one concise theme title that can serve unchanged as both the report title and email subject.
Create one warm, specific, caring sentence addressed to the user, grounded in the selected events.
Plan ONE vertical 3:4 poster that artistically fuses the selected moments into a cohesive image.
Return exactly one JSON object without Markdown, using precisely this shape:
{"title":"", "one_sentence_summary":"", "warm_message":"", "narrative":"",
"panels":[{"record_ids":["record UUID"], "time_label":"HH:MM",
"caption":"brief Chinese description of the selected moments",
"image_prompt":"English prompt describing one fused vertical poster without any text"}]}.
Do not rename, omit, or add fields. record_ids must be a JSON array containing only record_id values
present in the supplied scene evidence, containing at most three distinct IDs. Return exactly one
panels item. Use warm but restrained Chinese. State only supported facts, use cautious wording for
uncertain claims, omit weak guesses, and never request text inside images."""


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
    title = str(value.get("title") or f"{report_date.isoformat()} 的一天").strip()
    summary = str(
        value.get("one_sentence_summary")
        or value.get("summary")
        or (narrative[:80] if narrative else "今天留下了几段值得回看的记录。")
    ).strip()
    warm_message = str(
        value.get("warm_message")
        or value.get("caring_message")
        or value.get("message_to_user")
        or "辛苦了，愿这些被记住的小片段，也能给今天画上一个温柔的句号。"
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
    raw_items = raw_panels if isinstance(raw_panels, list) else []
    selected_ids: list[str] = []
    time_label = ""
    captions: list[str] = []
    image_prompts: list[str] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_time = str(item.get("time_label") or item.get("time") or "").strip()
        time_label = time_label or item_time
        raw_ids = item.get("record_ids")
        if raw_ids is None:
            raw_ids = item.get("record_id") or item.get("evidence_ids")
        item_ids = [record_id for record_id in _string_list(raw_ids) if record_id in scene_by_id]
        if not item_ids and item_time:
            item_ids = scene_by_time.get(item_time, [])
        for record_id in item_ids:
            if record_id not in selected_ids and len(selected_ids) < 3:
                selected_ids.append(record_id)
        caption = str(item.get("caption") or item.get("title") or item.get("description") or "").strip()
        if caption:
            captions.append(caption)
        image_prompt = str(
            item.get("image_prompt") or item.get("visual_prompt") or item.get("description") or ""
        ).strip()
        if image_prompt:
            image_prompts.append(image_prompt)

    # Repair missing or invalid model selections deterministically from the full
    # evidence set. Importance is computed locally from multimodal evidence.
    ranked_scenes = sorted(
        scene_by_id.items(),
        key=lambda pair: (-_score(pair[1].get("importance"), 0.0), str(pair[1].get("captured_at", ""))),
    )
    target_count = min(3, len(ranked_scenes))
    if target_count > 1:
        target_count = max(2, target_count)
    for record_id, _scene in ranked_scenes:
        if len(selected_ids) >= target_count:
            break
        if record_id not in selected_ids:
            selected_ids.append(record_id)
    selected_ids = selected_ids[:3]

    selected_summaries = [
        str(scene_by_id[record_id].get("summary") or "").strip()
        for record_id in selected_ids
        if record_id in scene_by_id
    ]
    caption = "；".join(captions[:3]) or "；".join(item for item in selected_summaries if item) or summary
    image_prompt = " ".join(image_prompts[:3]).strip()
    if not image_prompt:
        image_prompt = (
            "Fuse these selected real-life moments into one premium illustrated daily-memory poster: "
            + "; ".join(item for item in selected_summaries if item)
        )
    panels = [
        {
            "record_ids": selected_ids,
            "time_label": time_label or "今日",
            "caption": caption,
            "image_prompt": image_prompt,
        }
    ]
    return {
        "title": title,
        "one_sentence_summary": summary,
        "warm_message": warm_message,
        "narrative": narrative or summary,
        "panels": panels,
    }


def _validate_poster_image_size(value: str) -> tuple[int, int]:
    candidate = value.strip().lower()
    parts = candidate.split("x")
    if len(parts) != 2:
        raise ValueError("Poster size must be an explicit width x height pixel value")
    try:
        width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("Poster size must be an explicit width x height pixel value") from exc
    if width < 512 or height < 512 or width >= height:
        raise ValueError("Poster size must be vertical")
    if abs(width / height - 0.75) > 0.02:
        raise ValueError("Poster size must use a 3:4 aspect ratio")
    if width * height >= 2_200_000:
        raise ValueError("Poster size must contain fewer than 2.2 million pixels")
    return width, height


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
