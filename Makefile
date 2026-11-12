PYTHON := .venv/bin/python

.PHONY: venv demo test bench clean

venv:
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -m pip install --quiet duckdb pytest

demo:
	$(PYTHON) scripts/demo.py

test:
	$(PYTHON) -m pytest tests/ -v

bench:
	$(PYTHON) bench/bench.py

clean:
	rm -rf .venv .pytest_cache **/__pycache__
