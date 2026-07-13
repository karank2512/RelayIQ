# syntax=docker/dockerfile:1
# RelayIQ backend image. One image serves BOTH compose services:
#   api    — compose overrides CMD with: alembic upgrade head && seed --if-empty && uvicorn ...
#   worker — compose overrides CMD with: celery -A relayiq.workers.celery_app worker ...
# Everything both commands need (alembic.ini + alembic/, the installed package, celery/uvicorn
# entrypoints) lives in this image; only production deps are installed (no [dev] extras).

# ---------- build stage: install prod dependencies into an isolated venv ----------
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml ./
COPY relayiq ./relayiq
# `pip install .` — production dependencies only; the [dev] extra (pytest, ruff, mypy,
# locust, ...) is deliberately NOT installed in the image.
RUN pip install --upgrade pip && pip install .

# ---------- runtime stage ----------
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system relayiq && useradd --system --gid relayiq --home-dir /app relayiq

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# alembic.ini + migration scripts must be present at the WORKDIR because the compose api
# service runs `alembic upgrade head` before starting uvicorn (alembic.ini has
# script_location = alembic, relative to cwd).
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY relayiq ./relayiq

# Deterministic synthetic world. docker-compose mounts a named volume at /app/data;
# on first mount Docker populates the empty volume from this image content. The seed CLI
# regenerates the file deterministically (seeded PRNG) if it is ever missing.
COPY data ./data
RUN chown -R relayiq:relayiq /app/data

USER relayiq

EXPOSE 8000

# Default command runs the API; docker-compose overrides this per service
# (api adds migrate+seed, worker runs celery).
CMD ["uvicorn", "relayiq.main:app", "--host", "0.0.0.0", "--port", "8000"]
