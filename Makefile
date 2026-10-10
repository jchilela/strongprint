.PHONY: env up down build migrate makemigrations superuser test cov lint shell

COMPOSE = docker compose
RUN = $(COMPOSE) run --rm web

env:
	@test -f .env || cp .env.example .env

build: env
	$(COMPOSE) build

up: env
	$(COMPOSE) up -d
	@echo "Admin: http://localhost:8000/admin/  S3: http://localhost:8333/"

down:
	$(COMPOSE) down

migrate: env
	$(RUN) python manage.py migrate

makemigrations: env
	$(RUN) python manage.py makemigrations

superuser: env
	$(RUN) python manage.py createsuperuser

test: env
	$(RUN) pytest

cov: env
	$(RUN) pytest --cov --cov-report=term-missing

lint: env
	$(RUN) ruff check .

shell: env
	$(RUN) python manage.py shell
