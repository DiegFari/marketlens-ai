PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test check run-api run-ui docker-build docker-up docker-down clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(BIN)/activate:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

install: $(BIN)/activate ## Create venv, install all extras and git hooks
	$(BIN)/pip install -e ".[api,ui,dev]"
	$(BIN)/pre-commit install

lint: ## Lint and check formatting
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Auto-fix lint issues and format
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck: ## Static type checking
	$(BIN)/mypy

test: ## Run tests with coverage
	$(BIN)/pytest --cov --cov-report=term-missing

check: lint typecheck test ## Everything CI runs

run-api: ## Run the API with auto-reload on :8000
	$(BIN)/uvicorn marketlens.api.main:create_app --factory --reload --port 8000

run-ui: ## Run the Streamlit UI on :8501
	$(BIN)/python -m marketlens.ui --server.port 8501

docker-build: ## Build both images
	docker compose build

docker-up: ## Start the stack
	docker compose up --build

docker-down: ## Stop the stack
	docker compose down

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov build dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
