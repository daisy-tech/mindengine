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
# macOS / NFS AppleDouble files (._*) leak into docker cp and break
# JSON loaders downstream — strip them on host AND container side.
find ./eval ./app ./selftest ./scripts -name '._*' -type f -delete 2>/dev/null || true
# Web-only files
docker compose cp ./app/api/memory.py            backend:/app/app/api/memory.py
docker compose cp ./app/api/eval.py              backend:/app/app/api/eval.py
docker compose cp ./app/api/conversations.py     backend:/app/app/api/conversations.py
docker compose cp ./app/main.py                  backend:/app/app/main.py
docker compose cp ./app/config.py                backend:/app/app/config.py
docker compose cp ./app/config.py                celery:/app/app/config.py
# Chat orchestrator + protocols + repos: 会话首句自动填 title 涉及这几层。
docker compose cp ./app/services/chat                  backend:/app/app/services/
docker compose cp ./app/services/protocols.py          backend:/app/app/services/protocols.py
docker compose cp ./app/services/protocols.py          celery:/app/app/services/protocols.py
docker compose cp ./app/services/prompt_archive.py     backend:/app/app/services/prompt_archive.py
docker compose cp ./app/api/deps.py                    backend:/app/app/api/deps.py
docker compose cp ./app/infra/repositories             backend:/app/app/infra/
docker compose cp ./app/infra/repositories             celery:/app/app/infra/
# Synthetic eval runner (web-only — worker never imports it)
docker compose cp ./app/services/eval_synthetic  backend:/app/app/services/
# Synthetic eval case files (web-only)
docker compose cp ./eval/cases                   backend:/app/eval/
# Files used by both web and worker
docker compose cp ./app/infra/db/factory.py      backend:/app/app/infra/db/factory.py
docker compose cp ./app/infra/db/factory.py      celery:/app/app/infra/db/factory.py
# memory_extract is run by celery (worker) — backend doesn't import it,
# but cp 到 backend 也无害,保持两端一致省得 debug。
docker compose cp ./app/services/memory_extract  backend:/app/app/services/
docker compose cp ./app/services/memory_extract  celery:/app/app/services/
# workers/runners.py 跑在 celery 端;关系抽取的拓扑排序就在这里。
# backend 不直接 import workers,但和 memory_extract 一样保持两端一致,
# 后续 selftest 任何意外 import 都不会爆 ModuleNotFoundError。
docker compose cp ./app/workers                  celery:/app/app/
docker compose cp ./app/workers                  backend:/app/app/
# Self-test harness lives only on web (worker doesn't need it)
docker compose cp ./selftest                     backend:/app/selftest
# Admin CLIs — run via `python -m scripts.<name>` inside backend.
# NOTE: `docker compose cp ./scripts backend:/app/scripts` nests into
# `/app/scripts/scripts/` when the destination already exists (image
# COPY). Copy each file explicitly instead.
for _f in __init__.py dedup_memories.py reset_password.py smoke_m4.py; do
    docker compose cp "./scripts/${_f}" "backend:/app/scripts/${_f}"
done
# Belt-and-braces: hoist any nested copy from a prior bad cp.
docker compose exec -T backend sh -c '
    if [ -d /app/scripts/scripts ]; then
        cp -a /app/scripts/scripts/. /app/scripts/
        rm -rf /app/scripts/scripts
    fi
' 2>/dev/null || true
docker compose exec -T backend test -f /app/scripts/dedup_memories.py \
    || { echo "✗ scripts/dedup_memories.py missing in container — aborting" >&2; exit 1; }
# Belt-and-braces: drop any AppleDouble that already snuck into the
# container from a prior run (docker cp doesn't delete pre-existing files).
docker compose exec -T backend find /app/eval /app/selftest /app/scripts /app/app \
    -name '._*' -type f -delete 2>/dev/null || true

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
