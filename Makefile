.PHONY: verify verify-fast verify-postgres release-gate test lint docs-check portable-export platform-overview start up stop status logs doctor doctor-live upgrade

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

portable-export:
	./scripts/export_portable_state.sh

platform-overview:
	python3 scripts/platformctl.py overview


start:
	python3 scripts/easy_start.py

up:
	docker compose up -d postgres redis api worker bot-runtime

stop:
	docker compose stop api worker bot-runtime

status:
	docker compose ps

logs:
	docker compose logs -f --tail=200 api worker bot-runtime

doctor:
	python3 scripts/doctor.py

doctor-live:
	python3 scripts/doctor.py --require-running

upgrade:
	docker compose run --rm migrate
	docker compose up -d --build api worker bot-runtime
