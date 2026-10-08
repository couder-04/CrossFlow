"""ANPR OCR engine: video pipeline, fusion, recognition, evaluation."""

from ocr_engine.fusion import fuse_track_reads
from ocr_engine.recognize import MockRecognizer, ParseqRecognizer, RecognitionResult

__all__ = [
    "MockRecognizer",
    "ParseqRecognizer",
    "RecognitionResult",
    "fuse_track_reads",
]

__version__ = "0.1.0"
