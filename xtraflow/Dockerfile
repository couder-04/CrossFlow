FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV SUMO_HOME=/usr/local/lib/python3.12/site-packages/sumo
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p results/tables results/figures results/networks results/demand

CMD ["make", "all"]
