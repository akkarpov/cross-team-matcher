.PHONY: docs test run
docs:
	python scripts/build_docs.py
test:
	python -m pytest tests/test_auth.py tests/test_architecture.py tests/test_freshness.py -q
run:
	python scripts/local.py start
