"""`bin/consolidate-cases` is the migration, and the migration must lose nothing.

The corpus is 539 cases in 47 files, and the claim that made that move safe is
reversibility: `--merge` then `--split` returns the corpus it started from. That
claim was checked once, by hand, on the real corpus — which is exactly the kind of
check that stops being true in silence. This file makes it repeatable on a corpus
small enough to read, and pins the two decisions the real one depends on:

- a round that runs an area whole gets one reference back;
- a round that runs a **subset**, or the same cases in another order, keeps one
  selector per case, because collapsing it to the file would run cases it never ran.

The second is the one a naive implementation gets wrong: it shortens the round and
changes what the round does, and no test in the corpus would notice until a capture
was missing at run time.
"""

import subprocess
import sys
from pathlib import Path

import yaml

#: This file is `<repo>/tests/x.py`; the script is the one `bin/heimdall-qa` sits beside.
REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin" / "consolidate-cases"


def _case(status: int) -> str:
    return f"contract: contracts/ingest.yaml\nkind: H01\nexpect:\n  status: {status}\n"


#: Two areas: one a round runs whole, one it runs in part and out of the file's order.
CORPUS = {
    "cases/ingest/ingest-H01.yaml": "id: ingest-H01\n" + _case(202),
    "cases/ingest/ingest-I-replay.yaml": "id: ingest-I-replay\n" + _case(202),
    "cases/ingest/ingest-N-auth.yaml": "id: ingest-N-auth\n" + _case(401),
    "cases/health/health-H01.yaml": "id: health-H01\n" + _case(200),
    "rounds/full.yaml": (
        "id: full\n"
        "suite: suites/full.yaml\n"
        "mode: headless\n"
        "environment: sandbox\n"
        "include:\n"
        "  - cases/ingest/ingest-H01.yaml\n"
        "  - cases/ingest/ingest-I-replay.yaml\n"
        "  - cases/ingest/ingest-N-auth.yaml\n"
    ),
    "rounds/part.yaml": (
        "id: part\n"
        "suite: suites/part.yaml\n"
        "mode: headless\n"
        "environment: sandbox\n"
        "include:\n"
        "  - cases/health/health-H01.yaml\n"
        "  - cases/ingest/ingest-N-auth.yaml\n"
        "  - cases/ingest/ingest-H01.yaml\n"
    ),
    "suites/full.yaml": (
        "id: full\nsteps:\n  - loop:\n      times: 1\n"
        "      case: cases/ingest/ingest-I-replay.yaml\n"
    ),
    "suites/part.yaml": (
        "id: part\nsteps:\n  - loop:\n      times: 1\n"
        "      case: cases/health/health-H01.yaml\n"
    ),
}


def _corpus(tmp_path: Path) -> Path:
    for relative, text in CORPUS.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _run(root: Path, action: str) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), action, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _tree(root: Path) -> dict[str, object]:
    """Every file under `root`, parsed when it is YAML, so formatting cannot judge."""
    found: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            found[relative] = yaml.safe_load(text) if path.suffix == ".yaml" else text
    return found


def _merge(root: Path) -> None:
    _run(root, "--merge")


def _split(root: Path) -> None:
    _run(root, "--split")


def test_merge_writes_one_file_per_area_in_the_rounds_order(tmp_path: Path):
    root = _corpus(tmp_path)
    _merge(root)

    assert sorted(path.name for path in (root / "cases").glob("*.yaml")) == [
        "health.yaml",
        "ingest.yaml",
    ]
    assert list(yaml.safe_load((root / "cases" / "ingest.yaml").read_text())) == [
        "ingest-H01",
        "ingest-I-replay",
        "ingest-N-auth",
    ]
    assert not (root / "cases" / "ingest").exists()


def test_a_round_that_runs_an_area_whole_gets_one_reference_back(tmp_path: Path):
    root = _corpus(tmp_path)
    _merge(root)

    include = yaml.safe_load((root / "rounds" / "full.yaml").read_text())["include"]
    assert include == ["cases/ingest.yaml"]


def test_a_subset_round_keeps_one_selector_per_case(tmp_path: Path):
    """The round runs two of the three, and not in the file's order: it cannot shorten."""
    root = _corpus(tmp_path)
    _merge(root)

    include = yaml.safe_load((root / "rounds" / "part.yaml").read_text())["include"]
    assert include == [
        "cases/health.yaml",
        "cases/ingest.yaml#ingest-N-auth",
        "cases/ingest.yaml#ingest-H01",
    ]


def test_a_suite_loop_names_one_case_however_the_corpus_is_shaped(tmp_path: Path):
    root = _corpus(tmp_path)
    _merge(root)

    steps = yaml.safe_load((root / "suites" / "full.yaml").read_text())["steps"]
    assert steps[0]["loop"]["case"] == "cases/ingest.yaml#ingest-I-replay"


def test_merge_then_split_gives_back_the_corpus_it_started_from(tmp_path: Path):
    root = _corpus(tmp_path)
    before = _tree(root)

    _merge(root)
    assert len(list((root / "cases").glob("*.yaml"))) == 2

    _split(root)
    assert len(list((root / "cases").rglob("*.yaml"))) == 4

    assert _tree(root) == before
