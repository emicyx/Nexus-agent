.PHONY: up down logs psq backend-shell frontend-shell rebuild

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f backend frontend

rebuild:
	docker compose up --build -d --force-recreate backend frontend

psql:
	docker compose exec postgres psql -U nexus -d nexus

backend-shell:
	docker compose exec backend bash

frontend-shell:
	docker compose exec frontend sh
