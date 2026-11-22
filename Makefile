.PHONY: help install test smoke fmt dev-backend dev-frontend build clean

PY := backend/.venv/bin/python

help:
	@echo "make install       create the venv and install backend + frontend deps"
	@echo "make test          run the backend test suite (no network)"
	@echo "make smoke         run the live end-to-end smoke test (needs OPENROUTER_API_KEY)"
	@echo "make dev-backend   run the API on :8000"
	@echo "make dev-frontend  run the Vite dev server on :5173"
	@echo "make build         build the frontend into frontend/dist"
	@echo "make clean         remove caches and local sqlite files"

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install --quiet --upgrade pip && .venv/bin/pip install --quiet -r requirements-dev.txt
	cd frontend && npm install --no-fund --no-audit

test:
	cd backend && .venv/bin/python -m pytest -q

smoke:
	cd backend && .venv/bin/python ../scripts/smoke_demo.py

dev-backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache frontend/dist data backend/data
