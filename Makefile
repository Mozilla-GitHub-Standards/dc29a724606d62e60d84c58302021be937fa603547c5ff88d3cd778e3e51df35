.PHONY: build clean migrate redis-cache-cli redis-store-cli revision shell currentshell stop test run django-shell docs psql build-frontend

help:
	@echo "Welcome to the tecken\n"
	@echo "The list of commands for local development:\n"
	@echo "  build            Builds the docker images for the docker-compose setup"
	@echo "  clean            Stops and removes all docker containers"
	@echo "  migrate          Runs the Django database migrations"
	@echo "  redis-cache-cli  Opens a Redis CLI to the cache Redis server"
	@echo "  redis-store-cli  Opens a Redis CLI to the store Redis server"
	@echo "  shell            Opens a Bash shell"
	@echo "  currentshell     Opens a Bash shell into existing running 'web' container"
	@echo "  test             Runs the Python test suite"
	@echo "  run              Runs the whole stack, served on http://localhost:8000/"
	@echo "  gunicorn         Runs the whole stack using gunicorn on http://localhost:8000/"
	@echo "  stop             Stops the docker containers"
	@echo "  django-shell     Django integrative shell"
	@echo "  psql             Open the psql cli"
	@echo "  lintcheck        Check that the code is well formatted"
	@echo "  lintfix          Fix all the possible linting errors"
	@echo "  build-frontend   Builds the frontend static files\n"

# Dev configuration steps
.docker-build:
	make build

.env:
	./bin/cp-env-file.sh

build: .env
	docker-compose build base
	touch .docker-build

clean: .env stop
	docker-compose rm -f
	rm -rf coverage/ .coverage
	rm -fr .docker-build

migrate: .env
	docker-compose run web python manage.py migrate --run-syncdb

shell: .env .docker-build
	# Use `-u 0` to automatically become root in the shell
	docker-compose run --user 0 web bash

currentshell: .env .docker-build
	# Use `-u 0` to automatically become root in the shell
	docker-compose exec --user 0 web bash

redis-cache-cli: .env .docker-build
	docker-compose run redis-cache redis-cli -h redis-cache

redis-store-cli: .env .docker-build
	docker-compose run redis-store redis-cli -h redis-store

psql: .env .docker-build
	docker-compose run db psql -h db -U postgres

stop: .env
	docker-compose stop

test: .env .docker-build
	@bin/test.sh

run: .env .docker-build
	docker-compose up web worker frontend

gunicorn: .env .docker-build
	docker-compose run --service-ports web web

django-shell: .env .docker-build
	docker-compose run web python manage.py shell

docs:
	@bin/build-docs-locally.sh

tag:
	@bin/make-tag.py

#lint-frontend:
#	docker-compose run frontend lint

#lint-frontend-ci:
#	docker-compose run frontend-ci lint

build-frontend:
	docker-compose run -u 0 -e CI base ./bin/build_frontend.sh

lintcheck: .env .docker-build
	docker-compose run linting lintcheck
	docker-compose run frontend lint

lintfix: .env .docker-build
	docker-compose run linting blackfix
	docker-compose run frontend lintfix
