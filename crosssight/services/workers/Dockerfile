FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock* ./
COPY packages/anpr_common packages/anpr_common
COPY services/workers services/workers

RUN uv sync --package anpr-workers --no-dev

ENV PYTHONPATH=/app/packages/anpr_common/src:/app/services/workers/src
ENV PATH="/app/.venv/bin:$PATH"

WORKDIR /app/services/workers

EXPOSE 8081 8082 8083

CMD ["python", "-m", "workers.cli", "run", "--worker", "all"]
