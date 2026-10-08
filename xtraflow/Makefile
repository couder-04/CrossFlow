.PHONY: setup test smoke calibrate tune fixed_tuned train_rl sweep analyze robustness \
	sensitivity weight_sensitivity emission_xcheck safety perception grid extrapolate demo yolo_demo \
	yolo_dashboard dashboard deck report audit all freeze_config networks

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
export SUMO_HOME ?= $(shell $(PYTHON) -c "import sumo,os; print(os.path.dirname(sumo.__file__))" 2>/dev/null)

setup:
	python3.12 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	@echo "SUMO_HOME=$(SUMO_HOME)"
	$(PYTHON) -c "import sumolib,traci; print('sumolib/traci OK')"

networks:
	$(PYTHON) -m sim.build_network
	$(PYTHON) -m sim.build_grid

test:
	$(PYTHON) -m pytest tests/ -v --tb=short

smoke:
	$(PYTHON) -m experiments.sweep --smoke

calibrate: networks
	$(PYTHON) -m experiments.calibrate_weights
	$(PYTHON) -m experiments.calibrate_demand

tune: calibrate
	$(PYTHON) -m experiments.tune

train_rl: tune
	$(PYTHON) -m experiments.train_rl

freeze_config:
	$(PYTHON) -c "from sim.util import freeze_config; freeze_config()"

fixed_tuned:
	$(PYTHON) -m experiments.fixed_tuned

sweep:
	$(PYTHON) -m experiments.sweep

analyze:
	$(PYTHON) -m experiments.analyze

robustness:
	$(PYTHON) -m experiments.robustness

sensitivity:
	$(PYTHON) -m experiments.sensitivity

weight_sensitivity:
	$(PYTHON) -m experiments.weight_sensitivity

emission_xcheck:
	$(PYTHON) -m experiments.emission_xcheck

safety:
	$(PYTHON) -m experiments.safety

perception:
	$(PYTHON) -m perception.evaluate_detector --smoke

grid:
	$(PYTHON) -m experiments.grid_sweep

extrapolate:
	$(PYTHON) -m experiments.extrapolate

demo:
	$(PYTHON) -m demo.make_demo

yolo_demo:
	$(PYTHON) -m demo.run_yolo_demo

yolo_dashboard:
	$(PYTHON) -m streamlit run demo/yolo_dashboard.py --server.headless true

dashboard:
	$(PYTHON) -m streamlit run demo/dashboard.py --server.headless true

deck:
	$(PYTHON) -m deck.build_deck

report: analyze
	@echo "REPORT.md written by analyze/report pipeline"

audit:
	$(PYTHON) -m tools.audit

all: setup networks test smoke calibrate tune train_rl sweep analyze \
	robustness sensitivity emission_xcheck safety perception grid \
	extrapolate demo deck audit
	@echo "=== ALL PIPELINE COMPLETE ==="
