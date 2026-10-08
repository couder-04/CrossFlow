"""Plate image enhancement: CLAHE always, classical deblur when gated."""

from __future__ import annotations

from abc import ABC, abstractmethod

import cv2
import numpy as np

# Tunable thresholds for heavy enhancement gating.
QUALITY_THRESHOLD = 0.45
CONFIDENCE_THRESHOLD = 0.65


class Enhancer(ABC):
    """Heavy restoration interface (deblur / super-resolution)."""

    @abstractmethod
    def enhance(self, image: np.ndarray) -> np.ndarray:
        """Return restored BGR image."""


class NoopEnhancer(Enhancer):
    """Identity heavy enhancer — used when classical deblur is disabled."""

    def enhance(self, image: np.ndarray) -> np.ndarray:
        return image


class ClassicalDeblurEnhancer(Enhancer):
    """OpenCV classical restoration: unsharp mask + optional Wiener-like denoise.

    Not a learned SR model, but a real non-identity path for motion-blurred /
    soft plate crops when the quality/confidence gate fires.
    """

    def __init__(self, amount: float = 1.4, radius: float = 1.2, denoise: bool = True) -> None:
        self.amount = amount
        self.radius = radius
        self.denoise = denoise

    def enhance(self, image: np.ndarray) -> np.ndarray:
        if image.size == 0:
            return image
        work = image
        if self.denoise and min(image.shape[:2]) >= 16:
            work = cv2.bilateralFilter(work, d=5, sigmaColor=40, sigmaSpace=40)
        blurred = cv2.GaussianBlur(work, (0, 0), self.radius)
        sharpened = cv2.addWeighted(work, 1.0 + self.amount, blurred, -self.amount, 0)
        return np.clip(sharpened, 0, 255).astype(np.uint8)


def apply_clahe(image: np.ndarray, clip_limit: float = 2.0, tile_size: int = 8) -> np.ndarray:
    """Apply CLAHE on the L channel in LAB color space."""
    if image.ndim == 2:
        gray = image
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
        return clahe.apply(gray)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile_size, tile_size))
    l2 = clahe.apply(l)
    merged = cv2.merge([l2, a, b])
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def should_use_heavy_enhancement(quality: float, confidence: float | None) -> bool:
    """Gate heavy restoration on low frame quality or low recognition confidence."""
    return quality < QUALITY_THRESHOLD or (
        confidence is not None and confidence < CONFIDENCE_THRESHOLD
    )


def enhance_plate(
    image: np.ndarray,
    quality: float,
    confidence: float | None,
    enhancer: Enhancer | None = None,
) -> np.ndarray:
    """Always CLAHE; optionally apply heavy enhancer when gated."""
    out = apply_clahe(image)
    if should_use_heavy_enhancement(quality, confidence):
        heavy = enhancer or ClassicalDeblurEnhancer()
        out = heavy.enhance(out)
    return out


def _pad_percent(image: np.ndarray, pct: float) -> np.ndarray:
    """Pad (pct>0) or center-crop (pct<0) by a fraction of height/width."""
    if image.size == 0 or pct == 0:
        return image
    h, w = image.shape[:2]
    if pct > 0:
        ph, pw = max(1, int(h * pct)), max(1, int(w * pct))
        return cv2.copyMakeBorder(image, ph, ph, pw, pw, cv2.BORDER_REPLICATE)
    # shrink
    sh, sw = max(1, int(h * (-pct))), max(1, int(w * (-pct)))
    if sh * 2 >= h or sw * 2 >= w:
        return image
    return image[sh : h - sh, sw : w - sw]


def _scale(image: np.ndarray, factor: float) -> np.ndarray:
    if image.size == 0 or abs(factor - 1.0) < 1e-3:
        return image
    h, w = image.shape[:2]
    nh, nw = max(8, int(h * factor)), max(8, int(w * factor))
    return cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)


def tta_views(image: np.ndarray, *, heavy: bool = True, light: bool = False) -> list[np.ndarray]:
    """Cheap test-time views for plate OCR (no new labels / no retrain).

    Default views: identity, CLAHE, pad, shrink, ±5% scale, optional mild deblur.
    ``light=True`` keeps identity + CLAHE + pad only (faster pad tournaments).
    Set env PLATEOCR_TTA=0 to disable at the FormatOCR layer.
    """
    if image is None or image.size == 0:
        return []
    views: list[np.ndarray] = [image, apply_clahe(image), _pad_percent(image, 0.06)]
    if not light:
        views.append(_pad_percent(image, -0.04))
        views.append(_scale(image, 0.95))
        views.append(_scale(image, 1.05))
        if heavy:
            deblur = ClassicalDeblurEnhancer(amount=1.1, radius=1.0, denoise=True)
            views.append(deblur.enhance(apply_clahe(image)))
    return [v for v in views if v is not None and v.size > 0 and min(v.shape[:2]) >= 8]
