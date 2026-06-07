"""Service layer: pure business logic.

Per docs/rebuild/02-TDD.md §1.2:
  services may import: domain, other services
  services MUST NOT import: api, infra directly, SQLAlchemy models, FastAPI Request

The intended dependency injection is: api/workers wire concrete infra
implementations into Protocol-typed parameters defined in protocols.py.
"""
