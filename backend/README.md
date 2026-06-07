## MindEngine backend (M1 scaffold)

This is the **M1 milestone** of the MindEngine rebuild — a runnable
scaffold with all domain types, infra adapters, and a healthcheck. M2–M5
(memory router, prompt composer, four-layer memory, correction, eval lab,
frontend adapter) come in subsequent passes.

See `../docs/rebuild/` for design (PRD, TDD, subsystem docs).

## Layout

    backend/
    ├── app/
    │   ├── api/             FastAPI routers (thin)
    │   ├── domain/          Pure Pydantic types (no IO)
    │   ├── services/        Business logic + Protocols
    │   ├── infra/           DB / LLM adapters
    │   └── workers/         Celery skeleton
    ├── alembic/             Postgres + pgvector migrations
    ├── tests/               pytest (unit; integration uses testcontainers in M2+)
    └── docker-compose.yml

Layer rules (`docs/rebuild/02-TDD.md` §1.2):

|       | imports                          | does NOT import                |
| ----- | -------------------------------- | ------------------------------ |
| api   | services, domain                 | infra (direct), workers        |
| services | domain, other services        | api, ORM, FastAPI Request      |
| domain | stdlib + Pydantic only          | any IO / ORM                   |
| infra | domain                           | services, api                  |
| workers | services, infra                 | api                            |

## Quick start

### Local (no Docker)

    cd backend
    python -m venv .venv && source .venv/bin/activate
    pip install -e '.[dev]'
    pytest          # M1 unit tests, no container needed

### With Docker (Postgres + Redis + backend + Celery)

    cd backend
    cp .env.example .env
    docker compose up -d postgres redis
    docker compose run --rm backend alembic upgrade head
    docker compose up -d backend celery celery-beat
    curl http://localhost:8000/readyz   # checks Postgres + pgvector

### M1 acceptance gates

* `pytest` ≥ 30 tests pass with no container running
* `docker compose up` brings up 5 services (backend / postgres+pgvector / redis / celery / celery-beat); all healthchecks green
* `GET /healthz` → 200; `GET /readyz` → 200 with `pgvector: ok` (proves `CREATE EXTENSION vector` succeeded)

## What's intentionally NOT here yet

This is M1 only. Per `docs/rebuild/11-Roadmap.md`:

* M2: memory router (rules + classifier + policy), prompt composer, chat orchestrator (with streaming-persistence decoupling, decision D2), personality post-enforcement (D3)
* M3: four-layer memory async tasks, repositories' concrete implementations, banned-entities write/read filtering
* M4: correction pipeline, eval synthetic + chat-review (with optional flagged-turn judge, D5)
* M5: frontend adaptation + 「小白」branding
