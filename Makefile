.PHONY: up down migrate seed test lint typecheck build benchmark reset backup restore
up:
	docker compose up --build -d
	docker compose exec -T api alembic upgrade head
	docker compose exec -T api python -m reliefgrid.seed

down:
	docker compose down
migrate:
	docker compose exec -T api alembic upgrade head
seed:
	docker compose exec -T api python -m reliefgrid.seed
test:
	docker compose -f compose.yaml -f compose.test.yaml run --build --rm test-api
	docker compose -f compose.yaml -f compose.test.yaml run --build --rm test-web
lint:
	cd apps/api && ruff check .
	cd apps/web && npm run lint
typecheck:
	cd apps/api && mypy reliefgrid
	cd apps/web && npm run typecheck
build:
	docker compose build api web
benchmark:
	docker compose exec -T api python -m reliefgrid.benchmark
reset:
	docker compose down -v
	$(MAKE) up
backup:
	@mkdir -p backups
	docker compose exec -T db pg_dump -U "$${POSTGRES_USER:-reliefgrid}" -Fc "$${POSTGRES_DB:-reliefgrid}" > backups/reliefgrid.dump
restore:
	@test -n "$(FILE)" && test -f "$(FILE)"
	cat "$(FILE)" | docker compose exec -T db pg_restore -U "$${POSTGRES_USER:-reliefgrid}" -d "$${POSTGRES_DB:-reliefgrid}" --clean --if-exists
