from __future__ import annotations

import base64
import io
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image


def clamp_normalized(value: int | float) -> int:
    return max(0, min(1000, int(round(value))))


def normalize_box(box: tuple[float, float, float, float], width: int, height: int) -> list[int]:
    x1, y1, x2, y2 = box
    return [
        clamp_normalized(x1 / max(1, width) * 1000),
        clamp_normalized(y1 / max(1, height) * 1000),
        clamp_normalized(x2 / max(1, width) * 1000),
        clamp_normalized(y2 / max(1, height) * 1000),
    ]


@dataclass
class UIElement:
    source: str
    bbox: list[int]
    text: str = ""
    role: str = ""
    confidence: float | None = None

    @property
    def center(self) -> list[int]:
        return [int((self.bbox[0] + self.bbox[2]) / 2), int((self.bbox[1] + self.bbox[3]) / 2)]

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "bbox": self.bbox,
            "center": self.center,
        }
        if self.text:
            result["text"] = self.text[:300]
        if self.role:
            result["role"] = self.role[:80]
        if self.confidence is not None:
            result["confidence"] = round(float(self.confidence), 4)
        return result


@dataclass
class Observation:
    target_id: str
    target_kind: str
    image: Image.Image
    elements: list[UIElement] = field(default_factory=list)
    window_id: int | None = None
    title: str = ""
    observation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def jpeg_bytes(self, max_dimension: int = 1366, quality: int = 72) -> bytes:
        image = self.image.convert("RGB")
        if max(image.size) > max_dimension:
            scale = max_dimension / max(image.size)
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        return output.getvalue()

    def frame_base64(self) -> str:
        return base64.b64encode(self.jpeg_bytes()).decode("ascii")

    def summary(self) -> dict[str, Any]:
        return {
            "observationId": self.observation_id,
            "targetId": self.target_id,
            "targetKind": self.target_kind,
            "windowId": self.window_id,
            "title": self.title,
            "width": self.image.width,
            "height": self.image.height,
            "elements": [element.public() for element in self.elements[:500]],
        }

    def tool_content(self) -> list[dict[str, Any]]:
        import json

        return [
            {"type": "text", "text": json.dumps(self.summary(), ensure_ascii=False, separators=(",", ":"))},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + self.frame_base64()},
            },
        ]


class OCRParser:
    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()

    def _get_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def parse(self, image: Image.Image) -> list[UIElement]:
        array = np.asarray(image.convert("RGB"))
        with self._lock:
            result, _elapsed = self._get_engine()(array)
        if not result:
            return []
        elements: list[UIElement] = []
        for item in result[:500]:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            points, text, confidence = item[:3]
            if not points or len(points) < 4:
                continue
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            bbox = normalize_box((min(xs), min(ys), max(xs), max(ys)), image.width, image.height)
            elements.append(
                UIElement(
                    source="ocr",
                    bbox=bbox,
                    text=str(text or "")[:300],
                    role="text",
                    confidence=float(confidence),
                )
            )
        return elements


class ObservationGuard:
    def __init__(self):
        self.current: Observation | None = None

    def replace(self, observation: Observation) -> Observation:
        self.current = observation
        return observation

    def require(self, observation_id: str) -> Observation:
        if not self.current or self.current.observation_id != observation_id:
            raise ValueError("observationId 已失效，请重新 observe 后再操作")
        return self.current

    def consume(self, observation_id: str) -> Observation:
        observation = self.require(observation_id)
        self.current = None
        return observation
