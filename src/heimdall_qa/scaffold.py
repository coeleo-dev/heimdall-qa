from pathlib import Path
import sys

import yaml

from heimdall_qa.autofill import mechanical_payload
from heimdall_qa.autofill import read_h01_status
from heimdall_qa.coverage import area_from_endpoint
from heimdall_qa.coverage import expand
from heimdall_qa.schema.load import load_contract


def scaffold_endpoint(
        contract_path: Path,
        out_dir: Path,
        *,
        force: bool = False,
        contract_ref: str | None = None,
        h01_status: int | None = None,
) -> list[str]:
    contract = load_contract(contract_path)
    items = expand(contract)
    out_dir.mkdir(parents=True, exist_ok=True)
    ref = (
        contract_ref
        if contract_ref is not None
        else _posix_under(contract_path, Path.cwd())
    )
    happy = _resolve_happy_status(out_dir, items[0].case_id if items else "", h01_status)
    case_ids: list[str] = []
    for item in items:
        case_ids.append(item.case_id)
        dest = out_dir / f"{item.case_id}.yaml"
        if dest.exists() and not force:
            print(f"skip existing {dest}", file=sys.stderr)
            continue
        extras = mechanical_payload(item.kind, contract, happy)
        payload = {
            "id": item.case_id,
            "contract": ref,
            "kind": item.kind,
            "gate": "auto",
            "tags": ["function"],
            **extras,
        }
        dest.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
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
) -> dict[str, object]:
    contract = load_contract(contract_path)
    area = area_from_endpoint(contract.endpoint)
    cases_out = cases_dir if cases_dir is not None else Path("cases") / area
    base = root if root is not None else Path.cwd()
    ids = scaffold_endpoint(
        contract_path,
        cases_out,
        force=force,
        contract_ref=_posix_under(contract_path, base) if contract_ref is None else contract_ref,
        h01_status=h01_status,
    )
    chosen_id = round_id or area
    rounds_dir.mkdir(parents=True, exist_ok=True)
    round_path = rounds_dir / f"{chosen_id}.yaml"
    include = [_posix_under(cases_out / f"{case_id}.yaml", base) for case_id in ids]
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
    round_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {"round_id": chosen_id, "round": str(round_path), "include": include, "case_ids": ids}


def _resolve_happy_status(
        out_dir: Path,
        h01_id: str,
        seeded: int | None,
) -> int | None:
    if seeded is not None:
        return seeded
    if not h01_id:
        return None
    return read_h01_status(out_dir / f"{h01_id}.yaml")


def _posix_under(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
