# ── Stage 1: Build React frontend ─────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend + compiled frontend ────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install Python deps
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/app/ ./app/

# Copy built frontend into /app/static (served by FastAPI)
COPY --from=frontend-builder /build/frontend/dist ./static/

# Data directory (mounted volume in production)
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
