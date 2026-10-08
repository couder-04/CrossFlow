"""Plate text recognition interface and implementations."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass
class RecognitionResult:
    text: str
    char_probs: list[float]
    confidence: float


class Recognizer(ABC):
    @abstractmethod
    def recognize(self, image: np.ndarray) -> RecognitionResult:
        """Recognize plate text from a rectified BGR or grayscale crop."""


class MockRecognizer(Recognizer):
    """Test recognizer that returns ground truth from an optional path→plate map."""

    def __init__(self, ground_truth: dict[str, str] | None = None) -> None:
        self.ground_truth = {self._norm_key(k): v.upper() for k, v in (ground_truth or {}).items()}

    @staticmethod
    def _norm_key(path: str) -> str:
        return str(Path(path).resolve())

    def recognize(self, image: np.ndarray, image_path: str | None = None) -> RecognitionResult:
        if image_path and self._norm_key(image_path) in self.ground_truth:
            text = self.ground_truth[self._norm_key(image_path)]
        elif image_path and Path(image_path).name in self.ground_truth:
            text = self.ground_truth[Path(image_path).name]
        else:
            text = "UNKNOWN"
        char_probs = [0.95] * len(text)
        return RecognitionResult(text=text, char_probs=char_probs, confidence=0.95)

    def recognize_path(self, image_path: str | Path) -> RecognitionResult:
        import cv2

        img = cv2.imread(str(image_path))
        if img is None:
            return RecognitionResult(text="UNKNOWN", char_probs=[], confidence=0.0)
        return self.recognize(img, image_path=str(image_path))


class ParseqRecognizer(Recognizer):
    """PARSeq via torch.hub (baudm/parseq). Lazy-loads weights on first use."""

    def __init__(self, weights_path: str | None = None, device: str | None = None) -> None:
        self.weights_path = weights_path
        self.device = device
        self._model: Any = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch

        device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._device = torch.device(device)
        logger.info("Loading PARSeq from torch.hub (device=%s)", device)
        self._model = torch.hub.load("baudm/parseq", "parseq", pretrained=True)
        if self.weights_path:
            state = torch.load(self.weights_path, map_location=self._device)
            self._model.load_state_dict(state)
        self._model = self._model.eval().to(self._device)
        self._tokenizer = self._model.tokenizer

    def recognize(self, image: np.ndarray) -> RecognitionResult:
        import cv2
        import torch
        from PIL import Image

        self._load()
        if image.ndim == 2:
            rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        else:
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        tensor = self._model.transform(pil).unsqueeze(0).to(self._device)
        with torch.inference_mode():
            logits = self._model(tensor)
            probs = logits.softmax(-1)
            pred, _ = self._model.tokenizer.decode(probs)
        raw = pred[0] if pred else ""
        text = re.sub(r"[^A-Z0-9]", "", raw.upper())
        char_probs = [
            float(probs[0, i].max().item()) for i in range(min(len(text), probs.shape[1]))
        ]
        if len(char_probs) < len(text):
            char_probs.extend([0.8] * (len(text) - len(char_probs)))
        confidence = sum(char_probs) / len(char_probs) if char_probs else 0.0
        return RecognitionResult(text=text, char_probs=char_probs, confidence=confidence)
