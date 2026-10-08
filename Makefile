.PHONY: help setup test test-crossflow test-crosssight test-xtraflow demo demo-full city status \
	bridge-demo up serve down dashboard-check lint clean

PY      ?= $(CURDIR)/.venv/bin/python
export SUMO_HOME ?= $(shell $(PY) -c "import sumo,os; print(os.path.dirname(sumo.__file__))" 2>/dev/null)

help:
	@echo "CrossFlow - one product: sense the city (CrossSight), then decide who gets green (XtraFlow)"
	@echo ""
	@echo "  make setup       one Python 3.12 environment for everything, plus SUMO networks"
	@echo "  make test        every test suite, in that one environment"
	@echo "  make demo        city cameras -> measured demand -> signal-control study (about 1.5 min)"
	@echo "  make demo-full   the same with 5 seeds (about 3 min)"
	@echo "  make status      published XtraFlow results and the latest demo run"
	@echo "  make serve       platform + dashboard (Docker): http://localhost:3000, page /signals"
	@echo "  make lint        ruff + mypy"

setup:
	uv sync --python 3.12
	cd xtraflow && $(PY) -m sim.build_network && $(PY) -m sim.build_grid

test: test-crossflow test-xtraflow test-crosssight

test-crossflow:
	$(PY) -m pytest crossflow/tests -q

test-xtraflow:
	cd xtraflow && $(PY) -m sim.build_network && $(PY) -m sim.build_grid
	cd xtraflow && $(PY) -m pytest tests -q

# Integration tests need the Docker stack and run only with RUN_INTEGRATION=1.
test-crosssight:
	cd crosssight && $(PY) -m pytest -q

demo:
	$(PY) -m crossflow run

demo-full:
	$(PY) -m crossflow run --full

city:
	$(PY) -m crossflow city

status:
	$(PY) -m crossflow status

bridge-demo:
	bash scripts/bridge_demo.sh

up:
	mkdir -p runs
	$(MAKE) -C crosssight up

serve: up
	docker compose -f crosssight/docker-compose.yml --profile app up -d --build

down:
	$(MAKE) -C crosssight down

dashboard-check:
	cd crosssight/apps/dashboard && npx --yes pnpm@9 install --frozen-lockfile && npx --yes pnpm@9 typecheck && npx --yes pnpm@9 build

lint:
	$(PY) -m ruff check crossflow
	$(PY) -m ruff format --check crossflow
	cd crosssight && $(PY) -m ruff check . && $(PY) -m ruff format --check .
	cd crosssight && $(PY) -m mypy packages services --ignore-missing-imports --python-version 3.11

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache */.pytest_cache */.ruff_cache */.mypy_cache runs
