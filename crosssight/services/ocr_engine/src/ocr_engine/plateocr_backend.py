"""PlateOCR / FastALPR backend — YOLOv9 detector + CCT OCR via ONNX.

Adapted from https://github.com/Ajitesh-07/PlateOCR (synced 2026-09-30).

Supports:
  - Hub OCR models (cct-s-v2-global-model, …)
  - Published India fine-tune: ``india-v1`` (auto-download + SHA-256 check)
  - Format-constrained decoding via ``plate_format=india``
"""

from __future__ import annotations

import hashlib
import logging
import os
import statistics
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, cast

import numpy as np

from ocr_engine.enhance import tta_views
from ocr_engine.plate_format import decode_india
from ocr_engine.recognize import RecognitionResult, Recognizer

logger = logging.getLogger(__name__)

DEFAULT_DETECTOR = "yolo-v9-s-608-license-plate-end2end"
DEFAULT_OCR = "india-v1.1"


# Inference-only accuracy levers (no retrain / no new labels). Both are OFF by default: on the
# in_crops benchmark TTA gained +1.0 pt (within the +/-2.2 pt noise) for ~7x OCR latency, and
# together with bbox padding they cut a 4-plate frame from ~8.5 to ~1.2 fps on an RTX 4060.
# Enable per reader (Settings.plateocr_tta / plateocr_bbox_pad) or via env for offline eval.
def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "off", "no", "")


_TTA_DEFAULT = _env_flag("PLATEOCR_TTA", False)
_BBOX_PAD_DEFAULT = _env_flag("PLATEOCR_BBOX_PAD", False)

RELEASES_URL = "https://github.com/Ajitesh-07/PlateOCR/releases/download"
CUSTOM_OCR_MODELS: dict[str, dict[str, Any]] = {
    "india-v1": {
        "tag": "india-ocr-v1",
        "onnx": (
            "india_ocr_v1.onnx",
            "6fbb878e1bec7e318c9b43d58f4093d5bb7cb21bfe9d7d5fc18047f6c007a6f8",
        ),
        "config": (
            "india_ocr_v1_plate_config.yaml",
            "3f4718541abd7ba9e7fc050cfbda7463b64b9795d8c99689019c5b58a8e65b41",
        ),
        "plate_format": "india",
    },
    # Same weights and predictions as india-v1; Einsum ops rewritten as MatMul
    # (PlateOCR finetune/onnx_matmul.py): ~2x faster on CPU and steadier on GPU.
    "india-v1.1": {
        "tag": "india-ocr-v1.1",
        "onnx": (
            "india_ocr_v1_1.onnx",
            "88731e2db53ef7df9be26c710378cb6e0e9bc29fd5c59c9602cbfe0db4806ad4",
        ),
        "config": (
            "india_ocr_v1_1_plate_config.yaml",
            "3f4718541abd7ba9e7fc050cfbda7463b64b9795d8c99689019c5b58a8e65b41",
        ),
        "plate_format": "india",
    },
}

PLATEOCR_HELP = """
OCR_BACKEND=plateocr requires the fast-alpr package (ONNX Runtime).

Install (CPU):
  uv add --package anpr-ocr-engine "fast-alpr[onnx]==0.4.0"

On first run, detector + OCR ONNX weights download to:
  ~/.cache/open-image-models/          (YOLOv9 detector)
  ~/.cache/fast-plate-ocr/             (global CCT models)
  ~/.cache/plate-ocr/india-v1.1/       (India fine-tune, ~5 MB)
"""


@dataclass
class PlateHit:
    text: str
    ocr_confidence: float
    det_confidence: float
    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2
    region: str | None = None
    region_confidence: float | None = None
    char_probs: list[float] | None = None


def _pick_providers(device: str) -> list[str]:
    import onnxruntime as ort

    available = ort.get_available_providers()
    if device == "cpu":
        return ["CPUExecutionProvider"]
    if device == "cuda" and "CUDAExecutionProvider" not in available:
        raise RuntimeError(f"CUDA requested but not available. Providers: {available}")
    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    return [p for p in preferred if p in available]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_FETCH_LOCK = threading.Lock()


def fetch_custom_model(name: str) -> tuple[Path, Path]:
    """Return local (onnx, plate_config) paths for a CUSTOM_OCR_MODELS entry.

    Safe to call concurrently (fleet mode starts one reader per camera thread): downloads are
    serialised in-process, each writer uses its own temp file, and the verified file is moved
    into place atomically, so a reader never sees a partial model.
    """
    spec = CUSTOM_OCR_MODELS[name]
    cache = Path(os.environ.get("PLATE_OCR_CACHE", Path.home() / ".cache" / "plate-ocr")) / name
    base = os.environ.get("PLATE_OCR_MODEL_URL", RELEASES_URL).rstrip("/")
    cache.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with _FETCH_LOCK:
        for fname, sha in (spec["onnx"], spec["config"]):
            dest = cache / fname
            if not (dest.exists() and _sha256(dest) == sha):
                url = f"{base}/{spec['tag']}/{fname}"
                logger.info("Downloading %s", url)
                tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.part")
                try:
                    urllib.request.urlretrieve(url, tmp)
                    if _sha256(tmp) != sha:
                        raise RuntimeError(f"Checksum mismatch for {url}; refusing to use it")
                    os.replace(tmp, dest)
                finally:
                    tmp.unlink(missing_ok=True)
            paths.append(dest)
    return paths[0], paths[1]


class FormatOCR:
    """Wraps fast-alpr DefaultOCR; decodes slot probs under Indian plate grammar.

    When ``tta=True`` (default via PLATEOCR_TTA), averages softmax over cheap crop
    views (CLAHE / pad / scale / mild deblur) before format decode.
    """

    DECODERS: ClassVar[dict[str, Any]] = {"india": decode_india}

    def __init__(self, inner: Any, plate_format: str, *, tta: bool = _TTA_DEFAULT) -> None:
        self.inner = inner
        self.decode = self.DECODERS[plate_format]
        self.rec = inner.ocr_model  # fast_plate_ocr LicensePlateRecognizer
        self.tta = tta

    def _slot_probs(self, cropped_plate: np.ndarray) -> np.ndarray | None:
        import cv2
        from fast_plate_ocr.core.process import preprocess_image
        from fast_plate_ocr.inference.plate_recognizer import _load_image_from_source

        if cropped_plate is None or cropped_plate.size == 0:
            return None
        cfg = self.rec.config
        code = {"grayscale": cv2.COLOR_BGR2GRAY, "rgb": cv2.COLOR_BGR2RGB}[cfg.image_color_mode]
        x = preprocess_image(
            _load_image_from_source(cast(Any, cv2.cvtColor(cropped_plate, code)), cfg)
        )
        out = self.rec.model.run([self.rec.plate_output_name], {"input": x})[0]
        return out.reshape(cfg.max_plate_slots, len(cfg.alphabet))

    def predict(self, cropped_plate: np.ndarray, *, light_tta: bool = False) -> Any:
        from fast_alpr.base import OcrResult

        if cropped_plate is None or cropped_plate.size == 0:
            return None
        cfg = self.rec.config
        if self.tta:
            views = tta_views(cropped_plate, light=light_tta)
        else:
            views = [cropped_plate]
        stacked: list[np.ndarray] = []
        for view in views:
            probs = self._slot_probs(view)
            if probs is not None:
                stacked.append(probs)
        if not stacked:
            return None
        mean = np.mean(np.stack(stacked, axis=0), axis=0)
        text, confs = self.decode(mean, cfg.alphabet, cfg.pad_char)
        return OcrResult(text=text, confidence=confs)


_ORT_DTYPES = {
    "tensor(float)": np.float32,
    "tensor(float16)": np.float16,
    "tensor(uint8)": np.uint8,
}


def _find_sessions(obj: object, depth: int = 4, seen: set[int] | None = None) -> list[Any]:
    import onnxruntime as ort

    seen = seen if seen is not None else set()
    if isinstance(obj, ort.InferenceSession):
        return [obj]
    if depth == 0 or id(obj) in seen or not hasattr(obj, "__dict__"):
        return []
    seen.add(id(obj))
    return [s for v in vars(obj).values() for s in _find_sessions(v, depth - 1, seen)]


def _self_test(alpr: Any) -> None:
    """Run every ONNX session on dummy input so provider errors raise (also warms up)."""

    sessions = _find_sessions(alpr)
    if not sessions:
        raise RuntimeError("No ONNX sessions found inside ALPR; fast-alpr internals changed?")
    for sess in sessions:
        feeds = {
            i.name: np.zeros(
                [d if isinstance(d, int) else 1 for d in i.shape],
                _ORT_DTYPES.get(i.type, np.float32),
            )
            for i in sess.get_inputs()
        }
        sess.run(None, feeds)
    alpr.predict(np.zeros((640, 640, 3), dtype=np.uint8))


class PlateOCRReader:
    """Full-frame detect+read. Load once per process; call read() per frame."""

    def __init__(
        self,
        detector_model: str = DEFAULT_DETECTOR,
        ocr_model: str = DEFAULT_OCR,
        device: str = "auto",
        det_conf: float = 0.4,
        min_ocr_conf: float = 0.0,
        ocr_config: str | None = None,
        plate_format: str | None = None,
        tta: bool | None = None,
        bbox_pad: bool | None = None,
    ) -> None:
        try:
            import onnxruntime as ort
            from fast_alpr import ALPR
        except ImportError as exc:
            logger.error("fast-alpr / onnxruntime missing.%s", PLATEOCR_HELP)
            raise SystemExit(1) from exc

        if ocr_model in CUSTOM_OCR_MODELS:
            spec = CUSTOM_OCR_MODELS[ocr_model]
            onnx_path, cfg_path = fetch_custom_model(ocr_model)
            ocr_model, ocr_config = str(onnx_path), str(cfg_path)
            plate_format = plate_format or spec["plate_format"]
        if plate_format == "none":
            plate_format = None

        if device in ("auto", "cuda"):
            try:
                ort.preload_dlls()
            except Exception:  # noqa: BLE001, S110 - optional GPU DLL preload
                pass

        self.min_ocr_conf = min_ocr_conf
        self.plate_format = plate_format
        self.tta = _TTA_DEFAULT if tta is None else tta
        self.bbox_pad = _BBOX_PAD_DEFAULT if bbox_pad is None else bbox_pad
        providers = _pick_providers(device)
        self._alpr = self._build(ALPR, detector_model, ocr_model, ocr_config, det_conf, providers)
        try:
            _self_test(self._alpr)
        except Exception as exc:
            if providers == ["CPUExecutionProvider"] or device == "cuda":
                raise
            logger.warning(
                "GPU self-test failed, falling back to CPU: %s", str(exc).splitlines()[0]
            )
            providers = ["CPUExecutionProvider"]
            self._alpr = self._build(
                ALPR, detector_model, ocr_model, ocr_config, det_conf, providers
            )
            _self_test(self._alpr)
        if plate_format:
            self._alpr.ocr = FormatOCR(self._alpr.ocr, plate_format, tta=self.tta)
        self.providers = providers
        self.detector_model = detector_model
        self.ocr_model = ocr_model
        self.ocr_config = ocr_config
        logger.info(
            "PlateOCR ready (det=%s ocr=%s format=%s tta=%s pad=%s provider=%s)",
            detector_model,
            Path(ocr_model).name if ocr_model.endswith(".onnx") else ocr_model,
            plate_format or "none",
            self.tta,
            self.bbox_pad,
            providers[0],
        )

    @staticmethod
    def _build(
        alpr_cls: Any,
        detector_model: str,
        ocr_model: str,
        ocr_config: str | None,
        det_conf: float,
        providers: list[str],
    ) -> Any:
        ocr_kwargs: dict[str, Any] = {"ocr_model": ocr_model}
        if ocr_model.endswith(".onnx"):
            model_path = Path(ocr_model)
            if ocr_config is None:
                yamls = list(model_path.parent.glob("*.yaml"))
                if len(yamls) != 1:
                    raise ValueError(
                        f"Pass ocr_config: expected one *.yaml next to {model_path}, found {len(yamls)}"
                    )
                ocr_config = str(yamls[0])
            ocr_kwargs = {
                "ocr_model": None,
                "ocr_model_path": model_path,
                "ocr_config_path": ocr_config,
            }
        return alpr_cls(
            detector_model=detector_model,
            detector_conf_thresh=det_conf,
            detector_providers=providers,
            ocr_providers=providers,
            **ocr_kwargs,
        )

    def read(self, image: np.ndarray | str | Path) -> list[PlateHit]:
        import cv2

        if isinstance(image, (str, Path)):
            img = cv2.imread(str(image))
            if img is None:
                raise ValueError(f"Could not read image: {image}")
        else:
            img = image

        h, w = img.shape[:2]
        hits: list[PlateHit] = []
        # Detector-only then OCR tournament — avoids double full-TTA via ALPR.predict.
        detections = self._alpr.detector.predict(img)
        for detection in detections:
            b = detection.bounding_box
            x1, y1, x2, y2 = int(b.x1), int(b.y1), int(b.x2), int(b.y2)
            crop_specs: list[tuple[int, int, int, int]] = [(x1, y1, x2, y2)]
            if self.bbox_pad:
                bw, bh = max(1, x2 - x1), max(1, y2 - y1)
                for pct in (0.08, 0.15, -0.05):
                    dx, dy = int(bw * abs(pct)), int(bh * abs(pct))
                    if pct >= 0:
                        crop_specs.append((x1 - dx, y1 - dy, x2 + dx, y2 + dy))
                    else:
                        crop_specs.append((x1 + dx, y1 + dy, x2 - dx, y2 - dy))

            best_text, best_conf, best_probs = "", -1.0, []
            for i, (cx1, cy1, cx2, cy2) in enumerate(crop_specs):
                ax1, ay1 = max(0, cx1), max(0, cy1)
                ax2, ay2 = min(w, cx2), min(h, cy2)
                if ax2 - ax1 < 8 or ay2 - ay1 < 8:
                    continue
                crop = img[ay1:ay2, ax1:ax2]
                # Full TTA on tight crop; light TTA on padded variants.
                text, ocr_conf, char_probs = self.read_crop(crop, light_tta=(i > 0))
                if text and ocr_conf > best_conf:
                    best_text, best_conf, best_probs = text, ocr_conf, char_probs

            if not best_text or best_conf < self.min_ocr_conf:
                continue
            hits.append(
                PlateHit(
                    text=best_text,
                    ocr_confidence=round(best_conf, 4),
                    det_confidence=round(float(detection.confidence), 4),
                    bbox=(x1, y1, x2, y2),
                    char_probs=best_probs,
                )
            )
        return hits

    def read_crop(
        self, plate_img: np.ndarray, *, light_tta: bool = False
    ) -> tuple[str, float, list[float]]:
        """OCR-only for an already-cropped plate. Returns (text, mean_conf, char_probs)."""
        ocr = self._alpr.ocr
        if isinstance(ocr, FormatOCR):
            r = ocr.predict(plate_img, light_tta=light_tta)
        else:
            r = ocr.predict(plate_img)
        if r is None or not r.text:
            return "", 0.0, []
        if isinstance(r.confidence, list):
            char_probs = [float(c) for c in r.confidence]
            conf = float(statistics.mean(char_probs)) if char_probs else 0.0
        else:
            conf = float(r.confidence)
            char_probs = [conf]
        text = r.text.replace("_", "").strip().upper()
        text = "".join(ch for ch in text if ch.isalnum())
        return text, conf, char_probs


class PlateOCRRecognizer(Recognizer):
    """Recognizer adapter for **plate crops** (eval fixtures, legacy rectified plates).

    ``recognize`` runs OCR only and assumes the input is already a cropped plate. For full
    frames or photos use ``read_scene`` (detector + OCR). There is deliberately no automatic
    fallback between the two: with ``plate_format="india"`` the decoder always returns some
    valid-looking plate, so OCR on a whole photo "succeeds" with an invented plate and a
    crop-first fallback would never reach the detector.
    """

    def __init__(self, reader: PlateOCRReader | None = None, **kwargs: Any) -> None:
        self._reader = reader or PlateOCRReader(**kwargs)

    def recognize(self, image: np.ndarray) -> RecognitionResult:
        text, conf, probs = self._reader.read_crop(image)
        if not text:
            return RecognitionResult(text="UNKNOWN", char_probs=[], confidence=0.0)
        if len(probs) < len(text):
            probs = probs + [conf] * (len(text) - len(probs))
        return RecognitionResult(text=text, char_probs=probs[: len(text)], confidence=conf)

    def read_scene(self, image: np.ndarray) -> list[PlateHit]:
        """Detect and read every plate in a full frame/photo."""
        return self._reader.read(image)

    def recognize_path(self, image_path: str | Path) -> RecognitionResult:
        import cv2

        img = cv2.imread(str(image_path))
        if img is None:
            return RecognitionResult(text="UNKNOWN", char_probs=[], confidence=0.0)
        return self.recognize(img)


def ensure_plateocr_available() -> None:
    try:
        import fast_alpr  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        logger.error("PlateOCR backend unavailable.%s", PLATEOCR_HELP)
        sys.exit(1)
