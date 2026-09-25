"""One case file per area, and the one selector that reads a case out of one.

The loader is the only place an `include` becomes cases — nothing else in the core
knows how to resolve `cases/ingest.yaml#ingest-H01` — so this file pins the four
questions every consumer delegates to it: which cases, in what order, and what
happens when the file or the fragment is wrong. Two of the four are silent bugs
when they regress: an order that gets sorted runs an `I-replay` before the `H01`
that created what it replays, and a fragment that is quietly ignored runs a whole
area where the round asked for one case.
"""

from pathlib import Path

import pytest

from heimdall_qa.schema.load import existing_case_ids
from heimdall_qa.schema.load import iter_cases
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import load_cases
from heimdall_qa.schema.load import load_yaml
from heimdall_qa.schema.load import split_selector

#: Two cases of one area, in the order a round has to run them: the replay spends
#: what the happy path created.
AREA = """
ingest-H01:
  contract: contracts/ingest.yaml
  kind: H01
  expect:
    status: 202
ingest-I-replay:
  contract: contracts/ingest.yaml
  kind: I-replay
  expect:
    status: 202
"""


def _corpus(tmp_path: Path) -> Path:
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "ingest.yaml").write_text(AREA, encoding="utf-8")
    (tmp_path / "cases" / "health.yaml").write_text(
        "health-H01:\n  contract: contracts/health.yaml\n  kind: H01\n  expect:\n    status: 200\n",
        encoding="utf-8",
    )
    return tmp_path


def test_split_selector_reads_the_file_and_the_id():
    assert split_selector("cases/ingest.yaml") == ("cases/ingest.yaml", "")
    assert split_selector("cases/ingest.yaml#ingest-H01") == (
        "cases/ingest.yaml",
        "ingest-H01",
    )


def test_a_whole_file_reference_runs_every_case_in_disk_order(tmp_path: Path):
    found = list(iter_cases(_corpus(tmp_path), ["cases/ingest.yaml"]))

    assert [case.id for _, case in found] == ["ingest-H01", "ingest-I-replay"]
    assert [reference for reference, _ in found] == [
        "cases/ingest.yaml#ingest-H01",
        "cases/ingest.yaml#ingest-I-replay",
    ]


def test_a_fragment_runs_one_case_and_the_reference_names_it(tmp_path: Path):
    found = list(iter_cases(_corpus(tmp_path), ["cases/ingest.yaml#ingest-I-replay"]))

    assert [case.id for _, case in found] == ["ingest-I-replay"]
    assert found[0][0] == "cases/ingest.yaml#ingest-I-replay"


def test_the_order_of_a_reference_list_is_the_order_of_the_round(tmp_path: Path):
    found = list(
        iter_cases(
            _corpus(tmp_path),
            ["cases/health.yaml", "cases/ingest.yaml#ingest-H01"],
        )
    )

    assert [case.id for _, case in found] == ["health-H01", "ingest-H01"]


def test_a_fragment_naming_no_case_is_an_error_not_a_skip(tmp_path: Path):
    """A round that quietly runs eight of its nine cases is the failure to refuse."""
    with pytest.raises(ValueError, match="has no such case"):
        list(iter_cases(_corpus(tmp_path), ["cases/ingest.yaml#ingest-gone"]))


def test_a_duplicated_case_id_is_refused(tmp_path: Path):
    """PyYAML keeps the last of a duplicated key: a case that never ran, counted."""
    root = _corpus(tmp_path)
    (root / "cases" / "ingest.yaml").write_text(
        AREA
        + "ingest-I-replay:\n"
        + "  contract: contracts/ingest.yaml\n"
        + "  kind: I-replay\n"
        + "  expect:\n"
        + "    status: 409\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key 'ingest-I-replay'"):
        load_cases(root / "cases" / "ingest.yaml")


def test_a_single_case_file_says_how_to_wrap_it(tmp_path: Path):
    once = tmp_path / "cases" / "echo.yaml"
    once.parent.mkdir()
    once.write_text("id: echo-H01\ncontract: contracts/echo.yaml\n", encoding="utf-8")

    with pytest.raises(ValueError, match="wrap it"):
        load_cases(once)


def test_load_case_with_no_id_wants_a_file_that_holds_one(tmp_path: Path):
    root = _corpus(tmp_path)

    assert load_case(root / "cases" / "health.yaml").id == "health-H01"
    with pytest.raises(ValueError, match="holds 2 cases; name one"):
        load_case(root / "cases" / "ingest.yaml")


def test_existing_case_ids_reads_the_disk_order(tmp_path: Path):
    assert existing_case_ids(_corpus(tmp_path) / "cases" / "ingest.yaml") == [
        "ingest-H01",
        "ingest-I-replay",
    ]
    assert existing_case_ids(tmp_path / "cases" / "absent.yaml") == []


def test_an_edited_file_is_re_read_and_not_served_from_the_cache(tmp_path: Path):
    """The cache is keyed on the file's stamp, so an edit is never a stale read.

    Indexing a project parses and validates the same file a dozen times — once per round
    that includes it and again for each round's validation — so the result is cached.
    The one way that becomes a bug is if the cache outlives the file: a reviewer who
    saves a case and does not see the tree change would have no way to tell a broken
    cache from a broken save.
    """
    path = tmp_path / "cases" / "thing.yaml"
    path.parent.mkdir()
    path.write_text(
        "thing-H01:\n"
        "  contract: contracts/thing.yaml\n"
        "  kind: H01\n"
        "  expect:\n"
        "    status: 202\n",
        encoding="utf-8",
    )
    assert [case.id for case in load_cases(path)] == ["thing-H01"]

    path.write_text(
        "thing-H01:\n"
        "  contract: contracts/thing.yaml\n"
        "  kind: H01\n"
        "  expect:\n"
        "    status: 202\n"
        "thing-N-omit:\n"
        "  contract: contracts/thing.yaml\n"
        "  kind: N-omit\n"
        "  expect:\n"
        "    status: 400\n",
        encoding="utf-8",
    )
    assert [case.id for case in load_cases(path)] == ["thing-H01", "thing-N-omit"]


def test_a_caller_cannot_poison_the_cache_by_mutating_what_it_loaded(tmp_path: Path):
    """What a load hands over is the caller's; the cached copy is nobody's.

    The parse is cached, and the cheapest way to serve it is to hand out the same dict
    — which would make one caller's edit visible to every later reader, across projects
    and across requests, with no file to point at. The copy on the way out is what makes
    that impossible, and this is the assertion that keeps it there.
    """
    path = tmp_path / "cases" / "thing.yaml"
    path.parent.mkdir()
    path.write_text("thing-H01:\n  contract: contracts/thing.yaml\n", encoding="utf-8")

    first = load_yaml(path)
    first["injected"] = True
    assert "injected" not in load_yaml(path)
