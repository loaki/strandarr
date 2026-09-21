SRCS := strandarr alembic scripts
MYPY_SRCS := strandarr

# `make db-revision m='...'` is accepted as a shorthand for MSG=
MSG ?= $(m)

.PHONY: help
help: ## show this help
	@grep -E '^[a-zA-Z1-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | LC_ALL=C sort | awk 'BEGIN {FS = ":.*?## "}; {printf "%-30s - %s\n", $$1, $$2}'

.PHONY: lint
lint: ## check code violations using ruff
	@echo >&2 Linting with ruff...
	@uv run ruff check \
		$(SRCS)

.PHONY: lint-fix
lint-fix: ## fix code violations using ruff
	@echo >&2 Fix lint with ruff...
	@uv run ruff check --fix --unsafe-fixes \
		$(SRCS)

.PHONY: format
format: ## format source code using ruff
	@echo >&2 Formatting with ruff...
	@uv run ruff format \
		$(SRCS)

.PHONY: format-check
format-check: ## check source code formatting using ruff
	@echo >&2 Checking formatting with ruff...
	@uv run ruff format --check \
		$(SRCS)

.PHONY: check
check: ## uv checks
	@uv check

.PHONY: typing mypy
typing: ## check typing using mypy
	@echo >&2 Checking types with mypy...
	@uv run mypy $(MYPY_SRCS)
mypy: typing

.PHONY: check-schedule
check-schedule: ## prove every job kind eventually covers every day it claims
	@echo >&2 Checking schedule coverage...
	@uv run python scripts/check-schedule.py

.PHONY: all-checks
all-checks: lint format-check typing check-schedule  ## run all checks

.PHONY: db-revision
db-revision: ## autogenerate an alembic migration, usage: make db-revision MSG='describe change'
	@test -n "$(MSG)" || { echo >&2 "usage: make db-revision MSG='describe change'"; exit 1; }
	@uv run alembic revision --autogenerate -m "$(MSG)"

.PHONY: db-upgrade
db-upgrade: ## apply alembic migrations
	@uv run alembic upgrade head

.PHONY: db-current
db-current: ## show current alembic revision
	@uv run alembic current

.PHONY: db-check
db-check: ## fail if the models drifted from the migrations
	@uv run alembic check
