PYTHON ?= python
SIAVASH ?= siavash
ARGS ?=
DATE ?=
COMPOSE ?= docker compose
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

define not_implemented
	@echo "not implemented: $(1)"; exit 1
endef

.PHONY: install lint test test-live download-data validate-data train backtest walkforward diagnostics \
	paper live report replay flatten pause resume status shadow-report export-submission \
	dashboard dashboard-serve dashboard-demo docker-build docker-up docker-logs

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	ruff check src tests scripts
	mypy src

test:
	pytest tests/unit -q

test-live:
	pytest tests/integration -m live

download-data:
	$(PYTHON) scripts/download_data.py $(ARGS)

validate-data:
	$(PYTHON) scripts/validate_data.py $(ARGS)

train:
	$(PYTHON) scripts/train_model.py $(ARGS)

backtest:
	$(PYTHON) scripts/run_backtest.py $(ARGS)

walkforward:
	$(PYTHON) scripts/run_walkforward.py $(ARGS)

# research only: reports/diagnostics/ (the hold-out runs only with ARGS=--holdout, once)
diagnostics:
	$(PYTHON) scripts/run_diagnostics.py $(ARGS)

paper:
	$(SIAVASH) run --mode paper

# Human only. The agent never runs this target.
live:
	@read -p "Type LIVE to confirm: " ans; [ "$$ans" = "LIVE" ] || { echo "aborted"; exit 1; }
	CONFIRM_LIVE=YES $(SIAVASH) run --mode live

report:
	$(SIAVASH) report $(ARGS)

replay:
	$(SIAVASH) replay $(ARGS)

flatten:
	$(SIAVASH) flatten --confirm

pause:
	$(SIAVASH) pause

resume:
	$(SIAVASH) resume $(ARGS)

status:
	$(SIAVASH) status

shadow-report:
	$(PYTHON) scripts/consistency_report.py $(ARGS)

export-submission:
	$(PYTHON) scripts/export_submission.py $(ARGS)

dashboard:
	$(PYTHON) -m siavash.reporting.dashboard $(ARGS)

# read-only web dashboard + /health on 127.0.0.1:8080 (no login)
dashboard-serve:
	$(SIAVASH) dashboard $(ARGS)

# the same dashboard on a synthetic dataset in a temp dir: http://127.0.0.1:8090
dashboard-demo:
	$(PYTHON) -m siavash.dashboard.demo $(ARGS)

docker-build:
	$(COMPOSE) build --build-arg GIT_SHA=$(GIT_SHA)

docker-up:
	$(COMPOSE) up -d

docker-logs:
	$(COMPOSE) logs -f --tail=200
