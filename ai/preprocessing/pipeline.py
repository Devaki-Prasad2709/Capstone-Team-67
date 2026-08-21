"""Image validation, motion/quality filtering, pHash deduplication, and batching.

This is a portable adaptation of the Capstone-Team-67 preprocessing pipeline.
The producer's persistent deduplicator remains the primary deduplication layer;
the bounded in-memory pHash filter here can be enabled for legacy producers.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from common.deduplication import hamming_distance, perceptual_hash


@dataclass(frozen=True)
class PreprocessResult:
    accepted: bool
    image: np.ndarray | None
    reason: str
    metadata: dict[str, Any]


class FrameSampler:
    def __init__(self, interval: int = 2) -> None:
        self.interval = max(1, interval)
        self.index = 0

    def should_process(self) -> bool:
        selected = self.index % self.interval == 0
        self.index += 1
        return selected


class BatchManager:
    """Collect items into fixed-size batches and support a final partial flush."""

    def __init__(self, batch_size: int = 4) -> None:
        self.batch_size = max(1, batch_size)
        self.buffer: list[Any] = []

    def add(self, item: Any) -> list[Any] | None:
        self.buffer.append(item)
        if len(self.buffer) < self.batch_size:
            return None
        return self.flush()

    def flush(self) -> list[Any]:
        batch, self.buffer = self.buffer, []
        return batch


class PerceptualDeduplicator:
    def __init__(self, window_size: int = 20, threshold: int = 5) -> None:
        self.hashes: deque[str] = deque(maxlen=max(1, window_size))
        self.threshold = max(0, threshold)

    def is_duplicate(self, image: np.ndarray) -> tuple[bool, int | None]:
        fingerprint = perceptual_hash(image)
        if fingerprint is None:
            return False, None
        distances = [hamming_distance(fingerprint, previous) for previous in self.hashes]
        closest = min(distances) if distances else None
        if closest is not None and closest <= self.threshold:
            return True, closest
        self.hashes.append(fingerprint)
        return False, closest


class MotionDetector:
    def __init__(self, score_threshold: int = 5000, pixel_threshold: int = 25) -> None:
        self.score_threshold = max(0, score_threshold)
        self.pixel_threshold = max(0, pixel_threshold)
        self.previous: np.ndarray | None = None

    def detect(self, image: np.ndarray) -> tuple[bool, int]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (320, 320), interpolation=cv2.INTER_AREA)
        if self.previous is None:
            self.previous = gray
            return True, 0
        score = int(np.count_nonzero(cv2.absdiff(gray, self.previous) > self.pixel_threshold))
        self.previous = gray
        return score >= self.score_threshold, score


class QualityChecker:
    def __init__(self, blur_threshold: float = 100.0) -> None:
        self.blur_threshold = max(0.0, blur_threshold)

    def check(self, image: np.ndarray) -> tuple[bool, float]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return score >= self.blur_threshold, score


class PreprocessingPipeline:
    """Apply the upstream filters while exposing every decision as metadata."""

    def __init__(
        self,
        image_size: int = 640,
        enable_motion: bool = True,
        enable_quality: bool = True,
        enable_near_dedup: bool = False,
        motion_threshold: int = 5000,
        blur_threshold: float = 100.0,
        phash_threshold: int = 5,
        phash_window: int = 20,
    ) -> None:
        self.image_size = max(32, image_size)
        self.enable_motion = enable_motion
        self.enable_quality = enable_quality
        self.enable_near_dedup = enable_near_dedup
        self.motion = MotionDetector(motion_threshold)
        self.quality = QualityChecker(blur_threshold)
        self.deduplicator = PerceptualDeduplicator(phash_window, phash_threshold)

    @staticmethod
    def decode(data: bytes) -> np.ndarray:
        if not data:
            raise ValueError("Image payload is empty")
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise ValueError("Image payload could not be decoded")
        return image

    def process_bytes(self, data: bytes) -> PreprocessResult:
        return self.process(self.decode(data))

    def process(self, image: np.ndarray) -> PreprocessResult:
        if image is None or image.size == 0 or image.ndim != 3 or image.shape[2] != 3:
            return PreprocessResult(False, None, "invalid_image", {})
        original_height, original_width = image.shape[:2]
        metadata: dict[str, Any] = {
            "original_width": int(original_width),
            "original_height": int(original_height),
        }
        if self.enable_near_dedup:
            duplicate, distance = self.deduplicator.is_duplicate(image)
            metadata["phash_distance"] = distance
            if duplicate:
                return PreprocessResult(False, None, "near_duplicate", metadata)
        if self.enable_motion:
            moving, score = self.motion.detect(image)
            metadata["motion_score"] = score
            if not moving:
                return PreprocessResult(False, None, "low_motion", metadata)
        if self.enable_quality:
            sharp, score = self.quality.check(image)
            metadata["blur_score"] = round(score, 4)
            if not sharp:
                return PreprocessResult(False, None, "blurry", metadata)
        resized = cv2.resize(
            image,
            (self.image_size, self.image_size),
            interpolation=cv2.INTER_AREA if max(image.shape[:2]) > self.image_size else cv2.INTER_LINEAR,
        )
        metadata.update({"model_width": self.image_size, "model_height": self.image_size})
        return PreprocessResult(True, resized, "accepted", metadata)
