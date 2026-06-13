"""Manual self-test harness — see ``selftest/README.md``.

Lives outside ``app/`` and ``tests/`` on purpose:

- it is **not** a pytest target (would couple to a live stack)
- it is **not** an admin script (no production side-effects)

It is a single-file end-to-end probe you run by hand inside the
backend container after a deploy / when something looks off.
"""
