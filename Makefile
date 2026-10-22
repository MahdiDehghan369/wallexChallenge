PYTHON ?= python

.PHONY: install lint test test-live

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	ruff check src tests scripts
	mypy src

test:
	pytest tests/unit -q

test-live:
	pytest tests/integration -m live
