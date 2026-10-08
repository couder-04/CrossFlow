.PHONY: up down seed simulate backfill test types eval eval-plateocr fleet-ocr video-city prove-scenarios city-load ops-check prod-up lint install workers workers-alerts api

UV ?= uv
COMPOSE ?= docker compose
export UV_PROJECT_ENVIRONMENT ?= $(CURDIR)/.venv311
export PYTHONPATH := ../crossflow/src:packages/anpr_common/src:services/simulator/src:services/workers/src:services/api/src:services/ocr_engine/src:$(PYTHONPATH)

install:
	$(UV) sync --all-packages --python 3.11 --extra dev
	cd apps/dashboard && npx --yes pnpm@9 install

up:
	cp -n .env.example .env 2>/dev/null || true
	$(COMPOSE) up -d redpanda clickhouse postgres redis minio redpanda-init minio-init
	@echo "Waiting for healthchecks..."
	@$(COMPOSE) up -d --wait redpanda clickhouse postgres redis minio || true
	@sleep 3
	@echo "Infra is up."

down:
	$(COMPOSE) down -v

types:
	$(UV) run python scripts/generate_types.py

seed:
	$(UV) run python -m simulator.cli seed --synthetic

simulate:
	$(UV) run python -m simulator.cli simulate --speed $${SIM_SPEED:-60} --synthetic

backfill:
	$(UV) run python -m simulator.cli simulate --backfill-days $${BACKFILL_DAYS:-7} --synthetic

test:
	$(UV) run pytest packages/anpr_common/tests services/simulator/tests services/workers/tests services/api/tests services/ocr_engine/tests -q --tb=short
	@if [ "$${RUN_INTEGRATION}" = "1" ]; then $(UV) run pytest tests/integration -q --tb=short; fi

eval:
	mkdir -p reports
	$(UV) run python -m ocr_engine.eval --fixture services/ocr_engine/fixtures/eval_set

eval-plateocr:
	mkdir -p reports
	$(UV) run python -m ocr_engine.eval --fixture services/ocr_engine/fixtures/eval_set --recognizer plateocr

benchmark-ocr:
	mkdir -p reports
	$(UV) run python scripts/run_ocr_benchmarks.py
	$(UV) run python scripts/render_reports.py

prove-scenarios:
	mkdir -p reports
	$(UV) run python scripts/prove_scenario_pipeline.py

city-load:
	mkdir -p reports
	@$(COMPOSE) exec -T redpanda rpk group seek anpr-alerts --to end --topics anpr.reads.v1 2>/dev/null || true
	$(UV) run python scripts/city_load_latency.py --duration $${DURATION:-40} --rate $${RATE:-0.5} --drain $${DRAIN:-30}

ops-check:
	$(UV) run python scripts/ops_check.py

fleet-ocr:
	$(UV) run python -m ocr_engine.cli fleet --config services/ocr_engine/config/cameras.example.json --dry-run

video-city:
	$(UV) run python -m ocr_engine.cli video-city --config data/drive_cameras/fleet.json

lint:
	$(UV) run ruff check packages services scripts tests

app-up:
	$(COMPOSE) --profile app up -d --build

# City-scale: split workers + N alert replicas (Kafka group anpr-alerts)
prod-up:
	$(COMPOSE) --profile prod up -d --build --scale workers-alerts=$${ALERTS_REPLICAS:-3}
	@echo "Prod workers up. Scale alerts with: ALERTS_REPLICAS=5 make prod-up"
	@echo "Check: make ops-check"

ocr-up:
	$(COMPOSE) --profile ocr up -d --build

workers:
	$(UV) run python -m workers.cli run --worker all

# Local multi-process alerts (ports 8083+). Example: ALERTS_REPLICAS=3 make workers-alerts
workers-alerts:
	@n=$${ALERTS_REPLICAS:-2}; \
	i=0; \
	while [ $$i -lt $$n ]; do \
	  port=$$((8083 + i)); \
	  echo "starting alerts replica $$i on :$$port"; \
	  HEALTH_PORT=$$port $(UV) run python -m workers.cli run --worker alerts & \
	  i=$$((i + 1)); \
	done; \
	wait

api:
	$(UV) run anpr-api
