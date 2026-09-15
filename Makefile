.PHONY: verify verify-fast verify-postgres release-gate test lint docs-check

verify:
	./scripts/verify.sh all

verify-fast:
	./scripts/verify.sh fast

verify-postgres:
	./scripts/verify.sh postgres

release-gate:
	./scripts/release_gate.sh

test:
	python -m pytest -m "not postgres" -q

lint:
	python -m ruff check .

docs-check:
	python scripts/check_handoff_consistency.py
