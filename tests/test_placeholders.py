"""A `TODO` is refused anywhere in a case or a contract, not only in `expect.status`.

The sentinel is what `scaffold-endpoint` writes when it cannot derive a value. It
was only ever checked in `expect.status`, which meant a `TODO` in `diff` travelled
as the literal request body and the run reported a result for a request nobody
wrote. The walk is the whole structure now, and every hit is named by file, case
and path so a reader knows which line to open.
"""

from pathlib import Path

import pytest
import yaml

from heimdall_qa.errors import HarnessError
from heimdall_qa.runner import _reject_placeholders
from heimdall_qa.schema.load import load_case
from heimdall_qa.validate import todo_paths
from heimdall_qa.validate import validate_round

_CONTRACT = {
    "endpoint": "POST /qa/echo",
    "auth": "none",
    "baseline": "baselines/empty.json",
    "fields": {},
}


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _tree(tmp_path: Path, *, case: dict, contract: dict | None = None) -> Path:
    """A minimal target tree: one contract, one case, one round that includes it."""
    _write(tmp_path / "contracts" / "echo.yaml", contract or _CONTRACT)
    _write(
        tmp_path / "cases" / "echo.yaml",
        {"echo-H01": {"contract": "contracts/echo.yaml", **case}},
    )
    return _write(
        tmp_path / "round.yaml",
        {
            "id": "todo-round",
            "suite": "suites/unused.yaml",
            "mode": "review",
            "environment": "sandbox",
            "include": ["cases/echo.yaml"],
        },
    )


def _case(case_id: str = "echo-H01", **extra: object) -> dict:
    return {"id": case_id, "kind": "H01", "expect": {"status": 200}, **extra}


# ── the walk itself ───────────────────────────────────────────────────────────


def test_todo_paths_names_a_nested_field():
    assert todo_paths({"diff": {"set": {"name": "TODO"}}}) == ["diff.set.name"]


def test_todo_paths_names_a_list_position():
    assert todo_paths({"headers": ["ok", "TODO"]}) == ["headers[1]"]


def test_todo_paths_finds_the_bare_string():
    assert todo_paths("TODO") == ["value"]


def test_todo_paths_is_case_insensitive_and_trims():
    assert todo_paths({"a": " todo "}) == ["a"]


def test_todo_paths_leaves_prose_alone():
    """Only the bare sentinel counts: a note that mentions TODO is not one."""
    assert todo_paths({"notes": "TODO: derive this from the DTO"}) == []


def test_todo_paths_finds_every_hit():
    assert todo_paths({"a": "TODO", "b": {"c": "TODO"}}) == ["a", "b.c"]


# ── validate refuses it wherever it hides ─────────────────────────────────────


def test_a_todo_in_a_diff_is_named_by_file_and_case(tmp_path: Path):
    round_path = _tree(tmp_path, case=_case(diff={"set": {"amount": "TODO"}}))
    errors = validate_round(round_path, tmp_path)
    assert [item.code for item in errors] == ["TODO_SENTINEL"]
    assert errors[0].where == "cases/echo.yaml#echo-H01"
    assert "diff.set.amount is TODO" in errors[0]


def test_a_todo_in_a_header_is_caught(tmp_path: Path):
    round_path = _tree(tmp_path, case=_case(headers={"X-Idempotency-Key": "TODO"}))
    errors = validate_round(round_path, tmp_path)
    assert any("headers.X-Idempotency-Key is TODO" in error for error in errors)


def test_a_todo_in_a_path_value_is_caught(tmp_path: Path):
    round_path = _tree(tmp_path, case=_case(path_values={"id": "TODO"}))
    errors = validate_round(round_path, tmp_path)
    assert any("path_values.id is TODO" in error for error in errors)


def test_the_todo_in_expect_status_is_still_caught(tmp_path: Path):
    round_path = _tree(tmp_path, case=_case(expect={"status": "TODO"}))
    errors = validate_round(round_path, tmp_path)
    assert any("expect.status is TODO" in error for error in errors)


def test_a_case_without_a_todo_raises_nothing(tmp_path: Path):
    round_path = _tree(tmp_path, case=_case())
    assert not any("TODO" in error for error in validate_round(round_path, tmp_path))


def test_a_todo_in_a_contract_example_is_caught(tmp_path: Path):
    contract = {**_CONTRACT, "fields": {"name": {"required": True, "json": "name", "example": "TODO"}}}
    round_path = _tree(tmp_path, case=_case(), contract=contract)
    errors = validate_round(round_path, tmp_path)
    assert any("fields.name.example is TODO" in error for error in errors)


def test_a_contract_todo_names_the_contract_file(tmp_path: Path):
    contract = {**_CONTRACT, "fields": {"name": {"required": True, "json": "name", "example": "TODO"}}}
    round_path = _tree(tmp_path, case=_case(), contract=contract)
    errors = validate_round(round_path, tmp_path)
    assert any(str(tmp_path / "contracts" / "echo.yaml") in error for error in errors)


# ── and the run refuses it too, without a prior validate ─────────────────────


def test_the_runner_refuses_a_nested_todo(tmp_path: Path):
    path = _write(
        tmp_path / "cases" / "c.yaml",
        {"c": {"contract": "contracts/echo.yaml", "kind": "H01", "diff": {"set": {"a": "TODO"}}, "expect": {"status": 200}}},
    )
    with pytest.raises(HarnessError) as raised:
        _reject_placeholders([load_case(path, "c")])
    assert raised.value.code == "ROUND_INVALID"
    assert "diff.set.a is TODO" in raised.value.details[0]


def test_the_runner_accepts_a_case_without_a_todo(tmp_path: Path):
    path = _write(
        tmp_path / "cases" / "c.yaml",
        {"c": {"contract": "contracts/echo.yaml", "kind": "H01", "expect": {"status": 200}}},
    )
    _reject_placeholders([load_case(path, "c")])
