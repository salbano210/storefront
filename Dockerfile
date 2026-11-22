# ---------------------------------------------------------------------------
# Stage 1 -- build the React/Vite frontend
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend

WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 -- Python runtime serving the API + the built SPA
# ---------------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STATIC_DIR=/app/static \
    DATA_DIR=/app/data \
    PORT=10000

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend /fe/dist ./static

RUN mkdir -p /app/data

EXPOSE 10000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
