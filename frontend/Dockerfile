# syntax=docker/dockerfile:1.7
#
# MindEngine 前端镜像（小白）
#
# build 阶段在容器内 overlayfs 上跑 npm install（注意：本地 NFS 上跑 npm
# 等同于自找 IO 灾难，500MB+ 的 node_modules 全是小文件），最终只把
# 静态产物拷给 nginx 镜像，运行时镜像 < 30MB。
#
# usage:
#   docker compose build frontend
#   docker compose up -d frontend     # serves on :5173 by default
#
# Override registry mirror for poor connectivity:
#   docker compose build --build-arg NPM_REGISTRY=https://registry.npmmirror.com frontend

ARG NODE_VERSION=22
ARG NPM_REGISTRY=https://registry.npmmirror.com

# ─── deps stage ───────────────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine AS deps
WORKDIR /app
ARG NPM_REGISTRY
RUN npm config set registry "$NPM_REGISTRY"
COPY package.json ./
# 没有 lockfile 也能装；如果以后写了 package-lock.json，会更快地走 npm ci。
RUN if [ -f package-lock.json ]; then \
      npm ci --no-audit --no-fund; \
    else \
      npm install --no-audit --no-fund; \
    fi

# ─── build stage ──────────────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
# Small ECS instances (1–2 GB RAM) OOM or swap-thrash on vue-tsc + sourcemaps.
# Typecheck stays in local/CI `npm run build`; image build only runs vite.
ENV DOCKER_BUILD=1 \
    NODE_OPTIONS="--max-old-space-size=768"
COPY --from=deps /app/node_modules ./node_modules
COPY package.json tsconfig.json vite.config.ts index.html ./
COPY src ./src
COPY public ./public
RUN npm run build:docker

# ─── runtime stage ────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -q -O - http://127.0.0.1/healthz || exit 1
