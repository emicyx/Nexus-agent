.PHONY: up dev down logs psql backend-shell frontend-shell rebuild

# 生产形态：镜像不可变交付，无源码挂载，数据面仅 loopback
up:
	docker compose up --build -d

# 开发形态：源码热挂载 + uvicorn --reload + next dev（见 docker-compose.dev.yml）
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d

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
