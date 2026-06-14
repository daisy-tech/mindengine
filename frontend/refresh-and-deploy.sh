#!/usr/bin/env bash
# Build the SPA locally and sync static files into the *running* frontend
# container — no `docker compose build frontend` required.
#
# Use this on ECS (1–2 GB RAM) or whenever a full image rebuild is too slow.
# The nginx runtime image is unchanged; only /usr/share/nginx/html is replaced.
#
# Usage (from frontend/):
#
#   ./refresh-and-deploy.sh              # build + sync (needs Node on host)
#   ./refresh-and-deploy.sh --skip-build # sync existing dist/ only
#
# Prerequisites:
#   - Node 22+ and npm on THIS machine (Mac laptop is fine)
#   - docker compose stack up in ../backend (frontend container running)
#
# Mac → ECS workflow:
#   1. On Mac (in frontend/): ./refresh-and-deploy.sh
#      → only works if Mac docker points at ECS — usually you instead:
#   2. On Mac: DOCKER_BUILD=1 npm run build:docker
#   3. rsync/scp dist/ to ECS:~/Project/.../mindengine/frontend/dist/
#   4. On ECS (in frontend/): ./refresh-and-deploy.sh --skip-build

set -euo pipefail

cd "$(dirname "$0")"
FRONTEND_DIR="$(pwd)"
BACKEND_DIR="$(cd .. && pwd)/backend"

SKIP_BUILD=false
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    -h|--help)
      sed -n '2,22p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg (try --skip-build)" >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null; then
  echo "✗ docker not found in PATH" >&2
  exit 127
fi

if [ ! -f "$BACKEND_DIR/docker-compose.yml" ]; then
  echo "✗ docker-compose.yml not found at $BACKEND_DIR" >&2
  exit 1
fi

# Strip macOS AppleDouble before build/copy.
find ./src ./public -name '._*' -type f -delete 2>/dev/null || true

if [ "$SKIP_BUILD" = false ]; then
  echo "━━━ 1/3  npm run build:docker (host Node, not inside Docker)"
  if ! command -v npm >/dev/null; then
    echo "✗ npm not found — install Node 22+ or pass --skip-build after copying dist/" >&2
    exit 127
  fi
  if [ ! -d node_modules ]; then
    echo "  …npm install (first time)"
    npm config set registry "${NPM_REGISTRY:-https://registry.npmmirror.com}" 2>/dev/null || true
    npm install --no-audit --no-fund
  fi
  export DOCKER_BUILD=1
  export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=2048}"
  npm run build:docker
else
  echo "━━━ 1/3  skip build (--skip-build)"
  if [ ! -f dist/index.html ]; then
    echo "✗ dist/index.html missing — run without --skip-build first" >&2
    exit 1
  fi
fi

echo
echo "━━━ 2/3  cp dist → frontend container + drop AppleDouble in container"
cd "$BACKEND_DIR"
docker compose cp "$FRONTEND_DIR/dist/." frontend:/usr/share/nginx/html/
docker compose exec -T frontend find /usr/share/nginx/html -name '._*' -type f -delete 2>/dev/null || true

echo
echo "━━━ 3/3  restart frontend (reload nginx + re-resolve backend DNS)"
docker compose restart frontend >/dev/null

for i in $(seq 1 20); do
  s=$(docker compose ps --format '{{.Status}}' frontend 2>&1 | grep -oE 'healthy|starting|unhealthy' | head -1 || true)
  if [ "$s" = "healthy" ]; then
    echo "  ✓ frontend healthy after ~${i}s"
    echo
    echo "Done. Hard-refresh the browser (Cmd+Shift+R) and open http://<host>:5173"
    exit 0
  fi
  sleep 2
done

echo "  ⚠ frontend not healthy yet — check: docker compose logs --tail 30 frontend" >&2
exit 1
