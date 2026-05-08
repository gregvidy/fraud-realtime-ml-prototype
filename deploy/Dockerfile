# ==============================================================================
# Production Dockerfile — Fraud Scoring API
# Multi-stage build for minimal image size (~250MB vs ~1.2GB)
# ==============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install only production dependencies
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-prod.txt

# ==============================================================================
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ ./app/
COPY feast_repo/ ./feast_repo/
COPY models/ ./models/
COPY .env .env

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

# Non-root user for security
RUN useradd -r -s /bin/false apiuser && chown -R apiuser:apiuser /app
USER apiuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Gunicorn with uvicorn workers — production grade
CMD ["gunicorn", "app.main:app", \
     "-w", "4", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--worker-connections", "1000", \
     "--backlog", "2048", \
     "--timeout", "30", \
     "--graceful-timeout", "10", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info"]
