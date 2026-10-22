PYTHON ?= python
SIAVASH ?= siavash
ARGS ?=
DATE ?=
COMPOSE ?= docker compose
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

define not_implemented
	@echo "not implemented: $(1)"; exit 1
endef

.PHONY: install lint test test-live download-data validate-data train backtest walkforward \
	paper live report replay flatten pause resume status shadow-report export-submission \
	dashboard docker-build docker-up docker-logs

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
	$(call not_implemented,T-202)

train:
	$(call not_implemented,T-403)

backtest:
	$(call not_implemented,T-901)

walkforward:
	$(call not_implemented,T-904)

paper:
	$(call not_implemented,T-1104)

# Human only. The agent never runs this target.
live:
	@read -p "Type LIVE to confirm: " ans; [ "$$ans" = "LIVE" ] || { echo "aborted"; exit 1; }
	$(call not_implemented,T-1104)

report:
	$(call not_implemented,T-1201)

replay:
	$(call not_implemented,T-1305)

flatten:
	$(call not_implemented,T-1104)

pause:
	$(call not_implemented,T-1104)

resume:
	$(call not_implemented,T-1104)

status:
	$(call not_implemented,T-1104)

shadow-report:
	$(call not_implemented,T-1003)

export-submission:
	$(call not_implemented,T-1303)

dashboard:
	$(call not_implemented,T-1204)

docker-build:
	$(COMPOSE) build --build-arg GIT_SHA=$(GIT_SHA)

docker-up:
	$(COMPOSE) up -d

docker-logs:
	$(COMPOSE) logs -f --tail=200
