# ── Stage 1: Builder ────────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock* ./

# Install dependencies into a virtual environment
RUN uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv sync --no-dev --frozen 2>/dev/null || \
    VIRTUAL_ENV=/opt/venv uv sync --no-dev

# Copy application code
COPY . .

# ── Stage 2: Runtime ───────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r norm && useradd -r -g norm -s /bin/false norm

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
WORKDIR /app
COPY --from=builder /build/app ./app
COPY --from=builder /build/alembic ./alembic
COPY --from=builder /build/alembic.ini ./alembic.ini
COPY --from=builder /build/pyproject.toml ./pyproject.toml

# Set PATH so the venv's Python and packages are found
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER norm

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# gunicorn with uvicorn workers for production ASGI serving
CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "4", \
     "-b", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
