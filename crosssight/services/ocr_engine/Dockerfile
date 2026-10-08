FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY packages/anpr_common packages/anpr_common
COPY services/ocr_engine services/ocr_engine

RUN pip install --no-cache-dir uv && \
    uv sync --frozen --package anpr-ocr-engine --no-dev

ENV PATH=/app/.venv/bin:$PATH
ENV PYTHONPATH=/app/services/ocr_engine/src:/app/packages/anpr_common/src
ENV PLATE_DET_WEIGHTS=/models/plate_det.pt
# Detector + OCR weights download into /root/.cache on first start (open-image-models,
# fast-plate-ocr and plate-ocr all use ~/.cache); mount a volume there to keep them.

# Shell form so ${OCR_SOURCE} / ${OCR_CAMERA_ID} are expanded at run time (exec-form JSON
# passed the literal string "${OCR_SOURCE:-}" as the video source).
CMD ["sh", "-c", "if [ -z \"$OCR_SOURCE\" ]; then echo 'OCR_SOURCE is not set (video file path or RTSP URL)' >&2; exit 2; fi; exec ocr-engine run --source \"$OCR_SOURCE\" --camera-id \"${OCR_CAMERA_ID:-cam-demo}\""]
