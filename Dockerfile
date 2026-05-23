# ── Stage 1: Build the React frontend ──────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime with built frontend ───────────────
FROM python:3.12-slim
WORKDIR /app

# System deps for healthcheck + Google Cloud SDK (grpc needs libstdc++).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (no dev extras in prod).
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Copy backend source.
COPY backend/app ./app
COPY backend/seed_users.py ./

# Copy built frontend assets.
COPY --from=frontend-builder /build/dist ./static

# Data directory for SQLite + audio + uploads (mount a volume here).
RUN mkdir -p /app/data

# In production, FastAPI serves the React build from /app/static.
ENV APP_DATA_DIR=/app/data
ENV APP_STATIC_DIR=/app/static

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
