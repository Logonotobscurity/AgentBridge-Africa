.PHONY: test test-postgres eval install

install:
	pip install -r requirements.txt

eval:
	PYTHONPATH=. python evals/harness.py

test:
	PYTHONPATH=. python -m pytest -q
	$(MAKE) eval

test-postgres:
	PYTHON_BIN=python ./scripts/test-postgres.sh
