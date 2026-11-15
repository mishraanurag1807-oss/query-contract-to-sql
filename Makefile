PYTHON := .venv/bin/python
SYSTEM_PYTHON := $(shell for p in python3.13 python3.12 python3.11 python3; do \
	command -v $$p >/dev/null 2>&1 && $$p -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null && echo $$p && break; done)

.PHONY: venv demo test bench clean

$(PYTHON):
	@test -n "$(SYSTEM_PYTHON)" || (echo "Python 3.11+ is required" && exit 1)
	$(SYSTEM_PYTHON) -m venv .venv
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -m pip install --quiet duckdb pytest

venv: $(PYTHON)

demo: $(PYTHON)
	$(PYTHON) scripts/demo.py

test: $(PYTHON)
	$(PYTHON) -m pytest tests/ -v

bench: $(PYTHON)
	$(PYTHON) bench/bench.py

clean:
	rm -rf .venv .pytest_cache **/__pycache__
