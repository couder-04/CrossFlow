"""Annotated preview frames keep the input shape and paint the bbox."""

import numpy as np
from ocr_engine.pipeline import draw_annotations


def test_draw_annotations_marks_bbox_region():
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    bbox = (40, 50, 140, 130)
    tracks = [
        {
            "track_id": 3,
            "bbox": bbox,
            "plate_norm": "MH01AB1234",
            "vehicle_class": "car",
            "confidence": 0.91,
            "lane": 1,
            "direction": "N",
        }
    ]
    out = draw_annotations(frame, tracks)
    assert out.shape == frame.shape
    x1, y1, x2, y2 = bbox
    region = out[y1 : y2 + 1, x1 : x2 + 1]
    assert np.any(region != 0)
