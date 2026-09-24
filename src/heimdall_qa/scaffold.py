import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from heimdall_qa.autofill import mechanical_payload
from heimdall_qa.coverage import area_from_endpoint
from heimdall_qa.coverage import expand
from heimdall_qa.document import dump
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.load import load_contract


def scaffold_endpoint(
        contract_path: Path,
        cases_dir: Path,
        *,
        project: ProjectView | None = None,
        content_base: Path | None = None,
        force: bool = False,
        contract_ref: str | None = None,
        h01_status: int | None = None,
        extra: Mapping[str, Any] | None = None,
) -> list[str]:
    """Write the cases `coverage.expand` derives into the area's case file.

    One file per area — `cases/ingest.yaml`, a map of case id to case — because a
    reader looking for the `N-omit` cases of one endpoint should open one file and
    not forty-four. Existing entries are kept unless `force`: a scaffold that runs
    again over a corpus a human has filled in must not undo that work, and a case
    added later lands next to the ones already there.

    `extra` is merged into every case, last, and is how a caller that knows
    something the contract does not — `discover` writing the path parameters of a
    route — adds it without the contract schema growing a field for it.
    """
    contract = load_contract(contract_path)
    area = contract.area or area_from_endpoint(contract.endpoint)
    items = expand(contract)
    cases_dir.mkdir(parents=True, exist_ok=True)
    dest = cases_dir / f"{area}.yaml"
    #: The reference a case writes is relative to the content root, so the same
    #: case reads the same whether it is checked out at the harness root or under
    #: a provider directory.
    base = content_base if content_base is not None else Path.cwd()
    ref = (
        contract_ref
        if contract_ref is not None
        else _posix_under(contract_path, base)
    )
    written = _read_case_map(dest)
    happy = _resolve_happy_status(written, items[0].case_id if items else "", h01_status)
    case_ids: list[str] = []
    for item in items:
        case_ids.append(item.case_id)
        if item.case_id in written and not force:
            print(f"skip existing {dest}#{item.case_id}", file=sys.stderr)
            continue
        extras = mechanical_payload(
            item.kind, contract, happy, project if project is not None else ProjectView()
        )
        written[item.case_id] = {
            "contract": ref,
            "kind": item.kind,
            "gate": "auto",
            "tags": ["function"],
            **extras,
            **(extra or {}),
        }
    dest.write_text(dump(written), encoding="utf-8")
    return case_ids


def scaffold_round(
        contract_path: Path,
        rounds_dir: Path,
        *,
        cases_dir: Path | None = None,
        force: bool = False,
        contract_ref: str | None = None,
        h01_status: int | None = None,
        round_id: str | None = None,
        root: Path | None = None,
        environment: str = "sandbox",
        project: ProjectView | None = None,
) -> dict[str, object]:
    contract = load_contract(contract_path)
    # The declared area wins, because `expand` already names every case id after it:
    # deriving it again from the endpoint writes the cases into a file whose name
    # disagrees with the case ids inside it.
    area = contract.area or area_from_endpoint(contract.endpoint)
    base = root if root is not None else Path.cwd()
    cases_out = cases_dir if cases_dir is not None else base / "cases"
    ids = scaffold_endpoint(
        contract_path,
        cases_out,
        project=project if project is not None else ProjectView(),
        content_base=base,
        force=force,
        contract_ref=_posix_under(contract_path, base) if contract_ref is None else contract_ref,
        h01_status=h01_status,
    )
    chosen_id = round_id or area
    rounds_dir.mkdir(parents=True, exist_ok=True)
    round_path = rounds_dir / f"{chosen_id}.yaml"
    #: One line, because the scaffold wrote one file: a round generated from a
    #: contract covers exactly the cases of that contract's area, and listing them
    #: one by one is how a round ends up disagreeing with the file it includes.
    include = [_posix_under(cases_out / f"{area}.yaml", base)]
    if round_path.exists() and not force:
        print(f"skip existing {round_path}", file=sys.stderr)
        return {"round_id": chosen_id, "round": str(round_path), "include": include, "case_ids": ids}
    suite_rel = f"suites/{chosen_id}.yaml"
    payload = {
        "id": chosen_id,
        "suite": suite_rel,
        "mode": "review",
        "dimensions": ["function"],
        "environment": environment,
        "include": include,
    }
    round_path.write_text(dump(payload), encoding="utf-8")
    return {"round_id": chosen_id, "round": str(round_path), "include": include, "case_ids": ids}


def _read_case_map(dest: Path) -> dict[str, Any]:
    """The cases already in the area's file, so a re-run adds instead of replacing."""
    if not dest.is_file():
        return {}
    data = yaml.safe_load(dest.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _resolve_happy_status(
        written: dict[str, Any],
        h01_id: str,
        seeded: int | None,
    ) -> int | None:
    """The 2xx the `B-*` and `I-*` cases clone, from the H01 already in the file.

    Read from what is about to be written rather than from disk: with one file per
    area, the H01 a human filled in and the H01 the scaffold just rebuilt are the
    same entry, and a second read would answer with the older one.
    """
    if seeded is not None:
        return seeded
    if not h01_id:
        return None
    entry = written.get(h01_id)
    if not isinstance(entry, dict):
        return None
    expect = entry.get("expect")
    status = expect.get("status") if isinstance(expect, dict) else None
    return status if isinstance(status, int) else None


def _posix_under(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
