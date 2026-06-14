"""Tests for the synthetic case loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.eval_synthetic.case_loader import (
    CaseLoadError,
    iter_case_ids,
    list_case_files,
    load_cases,
    load_named_set,
)


def _write_case(p: Path, *, count: int = 2):
    cases = []
    for i in range(count):
        cases.append(
            {
                "id": f"case-{i}",
                "personality": "balanced",
                "history": [],
                "user": "你好",
                "expected": {"intents": ["casual"]},
                "tags": [],
            }
        )
    p.write_text(json.dumps(cases, ensure_ascii=False))


def test_load_cases_simple_list(tmp_path):
    f = tmp_path / "smoke_cases.json"
    _write_case(f, count=3)
    cases = load_cases(f)
    assert len(cases) == 3
    assert iter_case_ids(cases) == ["case-0", "case-1", "case-2"]


def test_load_cases_object_form(tmp_path):
    f = tmp_path / "x.json"
    f.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "obj-form",
                        "personality": "balanced",
                        "history": [],
                        "user": "x",
                        "expected": {},
                        "tags": [],
                    }
                ]
            }
        )
    )
    cases = load_cases(f)
    assert cases[0].id == "obj-form"


def test_load_cases_missing_file_raises(tmp_path):
    with pytest.raises(CaseLoadError):
        load_cases(tmp_path / "nope.json")


def test_load_cases_invalid_json_raises(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{")
    with pytest.raises(CaseLoadError):
        load_cases(f)


def test_load_cases_invalid_case_raises(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text(json.dumps([{"id": 1}]))
    with pytest.raises(CaseLoadError):
        load_cases(f)


def test_list_case_files_returns_sorted(tmp_path):
    (tmp_path / "b.json").write_text("[]")
    (tmp_path / "a.json").write_text("[]")
    out = [p.name for p in list_case_files(tmp_path)]
    assert out == ["a.json", "b.json"]


def test_list_case_files_skips_appledouble(tmp_path):
    """macOS / NFS produces ``._foo.json`` AppleDouble metadata files
    that match ``*.json`` but are binary blobs. They must not surface."""
    (tmp_path / "real.json").write_text("[]")
    (tmp_path / "._real.json").write_bytes(b"\x00\x05\x16\x07AppleDouble")
    out = [p.name for p in list_case_files(tmp_path)]
    assert out == ["real.json"]


def test_load_cases_non_utf8_raises_caseloaderror(tmp_path):
    """Reading an AppleDouble blob via ``load_cases`` must surface as
    ``CaseLoadError`` (not a bare ``UnicodeDecodeError``) so the API
    layer can convert it to 404 / skip instead of 500."""
    f = tmp_path / "._sneaky.json"
    f.write_bytes(b"\x00\x05\x16\x07\xff\xfe\xfd\xfc")
    with pytest.raises(CaseLoadError):
        load_cases(f)


def test_load_named_set_short_name(tmp_path):
    _write_case(tmp_path / "smoke_cases.json")
    cases = load_named_set(tmp_path, "smoke")
    assert len(cases) == 2


def test_load_named_set_full_name_passthrough(tmp_path):
    _write_case(tmp_path / "custom.json")
    cases = load_named_set(tmp_path, "custom.json")
    assert len(cases) == 2


def test_smoke_cases_ship_in_repo():
    """The default smoke set must always parse cleanly.

    Walks up until we find ``pyproject.toml`` (the backend root); this
    works both in the source checkout (``backend/``) and inside the
    docker image (``/app``) where pytest runs.
    """
    here = Path(__file__).resolve()
    backend_root = next(
        (p for p in here.parents if (p / "pyproject.toml").exists()),
        None,
    )
    assert backend_root is not None, "could not locate backend root"
    smoke = backend_root / "eval" / "cases" / "smoke_cases.json"
    cases = load_cases(smoke)
    assert len(cases) >= 5
    assert all(c.id for c in cases)
