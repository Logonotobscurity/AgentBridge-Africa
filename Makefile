.PHONY: test eval install

install:
	pip install -r requirements.txt

eval:
	PYTHONPATH=. python evals/harness.py

test:
	PYTHONPATH=. python -m pytest -q
	$(MAKE) eval
