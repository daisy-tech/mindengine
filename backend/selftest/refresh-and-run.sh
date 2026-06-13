#!/usr/bin/env bash
# Sync the latest backend changes into the running compose stack and run
# the self-test, all in one shot. Idempotent — safe to re-run any time.
#
# Use this *instead of* a full `docker compose build` when you're iterating
# on Python code: it's ~30s end-to-end vs several minutes for a rebuild
# (especially on the 2GB ECS box).
#
# Usage (from `backend/`):
#
#   ./selftest/refresh-and-run.sh                 # default
#   ./selftest/refresh-and-run.sh --keep-data     # keep test user
#   ./selftest/refresh-and-run.sh --skip-extraction --skip-recall
#
# Any extra args are forwarded verbatim to `python -m selftest.run`.

set -euo pipefail

# Ensure we run from `backend/` (where docker-compose.yml lives).
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null; then
    echo "✗ docker not found in PATH" >&2
    exit 127
fi

if [ ! -f docker-compose.yml ]; then
    echo "✗ docker-compose.yml not found in $(pwd)" >&2
    exit 1
fi

echo "━━━ 1/4  把最新 .env 注入容器 (force-recreate)"
docker compose up -d --force-recreate --no-build backend celery celery-beat

echo
echo "━━━ 2/4  等 backend healthy"
for i in $(seq 1 30); do
    s=$(docker compose ps --format '{{.Status}}' backend 2>&1 | grep -oE 'healthy|starting|unhealthy' | head -1 || true)
    if [ "$s" = "healthy" ]; then
        echo "  ✓ healthy after ~${i}s"
        break
    fi
    sleep 2
done

echo
echo "━━━ 3/4  把今天的改动 cp 进容器并 restart"
# Web-only files
docker compose cp ./app/api/memory.py    backend:/app/app/api/memory.py
# Files used by both web and worker
docker compose cp ./app/infra/db/factory.py backend:/app/app/infra/db/factory.py
docker compose cp ./app/infra/db/factory.py celery:/app/app/infra/db/factory.py
# Self-test harness lives only on web (worker doesn't need it)
docker compose cp ./selftest backend:/app/selftest

docker compose restart backend celery >/dev/null

# Wait again — restart resets the healthcheck.
for i in $(seq 1 30); do
    s=$(docker compose ps --format '{{.Status}}' backend 2>&1 | grep -oE 'healthy|starting|unhealthy' | head -1 || true)
    if [ "$s" = "healthy" ]; then
        echo "  ✓ backend healthy after restart (~${i}s)"
        break
    fi
    sleep 2
done

echo
echo "━━━ 4/4  跑 selftest"
docker compose exec -T backend python -m selftest.run "$@"
