"""Tests for enhancement gating logic."""

import cv2
import numpy as np
from ocr_engine.enhance import (
    CONFIDENCE_THRESHOLD,
    QUALITY_THRESHOLD,
    NoopEnhancer,
    apply_clahe,
    enhance_plate,
    should_use_heavy_enhancement,
)


class CountingEnhancer(NoopEnhancer):
    def __init__(self) -> None:
        self.calls = 0

    def enhance(self, image: np.ndarray) -> np.ndarray:
        self.calls += 1
        return super().enhance(image)


def test_clahe_changes_image():
    img = np.full((40, 120, 3), 128, dtype=np.uint8)
    out = apply_clahe(img)
    assert out.shape == img.shape


def test_gating_low_quality():
    assert should_use_heavy_enhancement(QUALITY_THRESHOLD - 0.1, 0.99) is True


def test_gating_low_confidence():
    assert should_use_heavy_enhancement(0.9, CONFIDENCE_THRESHOLD - 0.1) is True


def test_gating_high_scores_skip_heavy():
    assert should_use_heavy_enhancement(0.9, 0.9) is False


def test_enhance_plate_invokes_heavy_when_gated():
    img = np.random.randint(0, 255, (40, 120, 3), dtype=np.uint8)
    enhancer = CountingEnhancer()
    enhance_plate(img, quality=0.2, confidence=0.9, enhancer=enhancer)
    assert enhancer.calls == 1


def test_enhance_plate_skips_heavy_when_good():
    img = np.random.randint(0, 255, (40, 120, 3), dtype=np.uint8)
    enhancer = CountingEnhancer()
    enhance_plate(img, quality=0.9, confidence=0.9, enhancer=enhancer)
    assert enhancer.calls == 0


def test_classical_deblur_changes_blurry_image():
    from ocr_engine.enhance import ClassicalDeblurEnhancer

    rng = np.random.default_rng(0)
    sharp = rng.integers(0, 255, (40, 120, 3), dtype=np.uint8)
    blurry = cv2.GaussianBlur(sharp, (9, 9), 0)
    out = ClassicalDeblurEnhancer().enhance(blurry)
    assert out.shape == blurry.shape
    assert not np.array_equal(out, blurry)


def test_estimate_lane_left_to_right():
    from ocr_engine.pipeline import estimate_lane

    assert estimate_lane((10, 0, 30, 20), frame_width=300, num_lanes=3) == 1
    assert estimate_lane((140, 0, 160, 20), frame_width=300, num_lanes=3) == 2
    assert estimate_lane((270, 0, 290, 20), frame_width=300, num_lanes=3) == 3
