# syntax=docker/dockerfile:1.6
#
# Reference asterion image. Builds an installable wheel of the
# package and a thin runtime that runs uvicorn against `app:app`.
#
# Usage:
#     docker build -t asterion .
#     docker run --rm -p 8000:8000 \
#         -e ASTERION_DATABASE_URL=postgresql+asyncpg://... \
#         -e ASTERION_SECRET_KEY=$(openssl rand -hex 32) \
#         asterion
#
# Override the entrypoint to run CLI commands:
#     docker run --rm asterion asterion doctor
# ----------------------------------------------------------------------

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1


# --- build stage --------------------------------------------------------
FROM base AS build
WORKDIR /src
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY asterion ./asterion
COPY alembic_shared.ini alembic_tenant.ini ./
COPY migrations ./migrations

RUN pip install build && python -m build --wheel --outdir /dist


# --- runtime stage ------------------------------------------------------
FROM base AS runtime
WORKDIR /app

# libpq for asyncpg; tini as a sane PID 1.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=build /dist/*.whl /tmp/
RUN pip install /tmp/*.whl[postgres] "uvicorn[standard]>=0.29.0" \
 && rm /tmp/*.whl

# Application code (the wheel ships the framework; this is your app).
# Mount or COPY your app.py + models as a separate layer.
COPY app.py ./
COPY alembic_shared.ini alembic_tenant.ini ./
COPY migrations ./migrations

# Non-root user
RUN useradd --create-home --uid 10001 asterion \
 && chown -R asterion:asterion /app
USER asterion

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" \
      || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
