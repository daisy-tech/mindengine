## syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.12
# Default to Aliyun mirrors for fast apt + pip on ECS. Override when building
# outside China:
#   docker build --build-arg APT_MIRROR=deb.debian.org \
#     --build-arg PIP_INDEX_URL=https://pypi.org/simple \
#     --build-arg PIP_TRUSTED_HOST=pypi.org .
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

FROM python:${PYTHON_VERSION}-slim AS base
# Re-declare ARGs that we need inside this stage; ARGs declared before the
# first FROM are only visible to FROM lines, not to RUN steps.
ARG APT_MIRROR
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN set -eux; \
    # python:3.12-slim (bookworm/trixie) may use deb822 or legacy sources.list
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i \
        -e "s|deb.debian.org|${APT_MIRROR}|g" \
        -e "s|security.debian.org|${APT_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
      sed -i \
        -e "s|deb.debian.org|${APT_MIRROR}|g" \
        -e "s|security.debian.org|${APT_MIRROR}|g" \
        /etc/apt/sources.list; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

FROM base AS deps
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}
COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md
RUN pip install --upgrade pip \
    && pip install -e .

FROM deps AS runtime
COPY app /app/app
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
# Synthetic eval cases live next to the backend (not under app/) so they
# are easy to swap in CI without touching the Python package. Ship the
# default smoke set inside the image so /api/eval/synthetic works
# out-of-the-box.
COPY eval /app/eval
# Operational CLI helpers (e.g. reset_password.py for locked-out users).
# Trust model: invoked via `docker compose exec backend …`, so caller already
# has shell access to the host.
COPY scripts /app/scripts
# End-to-end self-test harness. Same trust model as scripts/.
COPY selftest /app/selftest
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
