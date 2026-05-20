FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY feast_repo/ ./feast_repo/
COPY models/ ./models/

ENV PYTHONPATH=/app \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

# Gunicorn with async Uvicorn workers — matches Makefile start-api target.
# API_WORKERS can be overridden via docker-compose environment variable.
CMD ["gunicorn", "app.main:app", \
     "-w", "4", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--worker-connections", "1000", \
     "--backlog", "2048", \
     "--timeout", "30", \
     "--access-logfile", "-"]
