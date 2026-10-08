FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgeos-c1v5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock* ./
COPY packages/anpr_common packages/anpr_common
COPY services/api services/api

RUN pip install --no-cache-dir uv \
    && uv sync --package anpr-api --no-dev \
    && uv pip install --system -e packages/anpr_common \
    && uv pip install --system -e services/api

ENV PYTHONPATH=/app/services/api/src:/app/packages/anpr_common/src

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
