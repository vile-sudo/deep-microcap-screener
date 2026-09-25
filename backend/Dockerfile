# Build from the PROJECT ROOT (not this directory), because the image needs
# both backend/ and frontend/:
#   docker build -f backend/Dockerfile -t screener .
# docker-compose.yml at the project root already does this correctly.

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/app ./backend/app
COPY backend/data ./backend/data
# second copy, outside the volume mounted over data/: docker-entrypoint.sh syncs it in at start
COPY backend/data ./backend/data-dist
COPY backend/chart_data ./backend/chart_data
COPY backend/chart_data_us ./backend/chart_data_us
COPY backend/reports ./backend/reports
COPY backend/sectors ./backend/sectors
COPY frontend ./frontend
COPY backend/docker-entrypoint.sh backend/sync-data.sh /app/
RUN chmod +x /app/docker-entrypoint.sh /app/sync-data.sh

WORKDIR /app/backend

# Seed the database from the bundled JSON at image-build time. If you
# refresh data/companies_raw.json later, re-run this (or `python -m
# app.seed`) to update the DB without rebuilding the image.
RUN python -m app.seed

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -f http://localhost:8000/healthz || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "2"]
