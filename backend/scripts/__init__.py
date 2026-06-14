"""Standalone administrative CLIs (run inside the backend container).

Made into a package (vs a flat ``scripts/`` dir) so that helpers can be
imported by unit tests, e.g. ``from scripts.dedup_memories import
plan_event_dedup``. Each module remains independently runnable via
``python -m scripts.<name>``.
"""
