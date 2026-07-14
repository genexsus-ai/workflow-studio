# Workflow Studio backend image.
#
# Build context must contain:
#   backend/   — this repo's backend
#   genxai/    — the genxai framework package (from the genxai-framework repo)
# See deploy/README.md for the build/deploy procedure.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --upgrade pip && \
    pip install -r backend/requirements.txt && \
    # genxai runtime deps (the package itself resolves via PYTHONPATH)
    pip install click pyyaml

COPY genxai ./genxai
COPY backend ./backend

WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
