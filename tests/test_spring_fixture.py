"""The Java parity target: `examples/spring-fixture/`, read and then talked to.

Fase 2.1d's gate. Two tiers, because the two claims are made of different things:

**Tier 1 is hermetic and always runs.** It reads the fixture's committed Java with
`heimdall-qa-spring` and asserts the IR — the three routes, the four mechanical
attributes of each field, the failure table the advice declares, the gaps nobody can
derive — and then asserts that the *committed* contracts and cases are exactly what a
fresh `discover` writes, plus the two edits a human had to make. That is the whole of
"zero human edit" as a claim CI can repeat: a generator is not tested by the files it
already wrote.

**Tier 2 is `-m slow` and needs a JDK.** It starts the built jar and runs the round
the harness generated, so the statuses are checked against a running Spring Boot
application instead of against our reading of one. A JDK is not a dependency of the
harness, so a machine without one skips rather than fails.

The fixture is not a product. It carries no price model and no domain: it is the
smallest Spring application that answers every axis the harness asks about, which is
what makes it usable as a parity target for a *reader*.
"""

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import yaml

from heimdall_qa.collection import find_step_dir
from heimdall_qa.contract_source import load_contract_source
from heimdall_qa.descriptor import load_descriptor
from heimdall_qa.discovery import discover
from heimdall_qa.project import ProjectView
from heimdall_qa.runner import execute_round
from heimdall_qa.schema.descriptor import ProjectDescriptor
from heimdall_qa.testing import config_for
from heimdall_qa.testing import project_at
from provider_marks import needs_spring_reader

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "examples" / "spring-fixture"
SOURCE = FIXTURE / "src" / "main" / "java"
DESCRIPTOR = FIXTURE / "qa" / "project.yaml"
ROUND = FIXTURE / "rounds" / "smoke.yaml"
JAR = FIXTURE / "target" / "spring-fixture-0.0.1-SNAPSHOT.jar"

#: The header value the fixture's filter accepts, and the one the descriptor names.
TOKEN = "fixture-token"

pytestmark = needs_spring_reader


def _schema():
    return load_contract_source("spring").read(str(SOURCE))


def _by_route(schema, route: str):
    return next(item for item in schema.endpoints if item.endpoint() == route)


def _source_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A fresh discovery of the committed fixture, in a directory of its own.

    The descriptor declares `contract.location` relative to itself, and the copy
    carries the original's directory layout, so `--location` is passed explicitly:
    the test asks about the fixture's source, not about a path that happens to
    resolve.
    """
    contracts = tmp_path / "contracts"
    cases = tmp_path / "cases"
    discover(
        project_at(DESCRIPTOR),
        contracts_dir=contracts,
        cases_dir=cases,
        report_path=tmp_path / "DISCOVERY.md",
        source="spring",
        location=str(SOURCE),
    )
    return contracts, cases


# ── tier 1: what the reader derives from the committed Java ──────────────────


def test_the_three_routes_are_read_with_their_methods_and_paths():
    schema = _schema()

    assert {item.endpoint() for item in schema.endpoints} == {
        "GET /fixture/health",
        "POST /fixture/items",
        "GET /fixture/items/{id}",
    }
    assert schema.source == "spring"


def test_a_bodyless_route_says_so_instead_of_an_empty_body():
    """`has_body` is the difference between "no body" and "a body nobody described"."""
    assert _by_route(_schema(), "GET /fixture/health").has_body is False
    assert _by_route(_schema(), "POST /fixture/items").has_body is True


def test_the_post_answers_201_and_the_reads_200():
    schema = _schema()

    assert _by_route(schema, "POST /fixture/items").success_status == 201
    assert _by_route(schema, "GET /fixture/health").success_status == 200
    assert _by_route(schema, "GET /fixture/items/{id}").success_status == 200


def test_the_four_mechanical_attributes_need_no_human_edit():
    """`required`, `json`, `max_length` and `pattern`, on one record, from source."""
    fields = {field.name: field for field in _by_route(_schema(), "POST /fixture/items").fields}

    assert set(fields) == {"name", "label", "code"}
    assert all(field.required for field in fields.values())

    assert fields["name"].max_length == 40
    assert fields["label"].json_name == "item_label"
    assert fields["label"].max_length == 8
    assert fields["code"].json_name == "item_code"
    assert fields["code"].pattern == "^[A-Z]{3}$"


def test_the_idempotency_header_and_its_mode_are_read_from_the_parameter():
    endpoint = _by_route(_schema(), "POST /fixture/items")

    assert endpoint.required_headers == ("X-Idempotency-Key",)
    assert endpoint.idempotency == "header_uuid_v4"


def test_the_path_parameter_is_read_and_marked_as_the_resource_id():
    endpoint = _by_route(_schema(), "GET /fixture/items/{id}")

    assert endpoint.path_params == ("id",)
    assert endpoint.resource_id_in_path is True


def test_the_advice_table_becomes_the_failure_table():
    table = _schema().errors

    assert table.statuses == {
        "idempotency_conflict": 409,
        "missing_header": 400,
        "validation": 400,
    }
    assert table.envelope == "spring"


def test_the_axes_no_source_can_answer_are_named_in_the_notes():
    notes = " ".join(_schema().errors.notes)

    assert "auth" in notes
    assert "not_found" in notes


def test_a_handler_no_axis_covers_is_named_rather_than_guessed():
    """`MethodArgumentTypeMismatchException` answers 400 and belongs to no family.

    The reader could map it — the number is right there in the advice — and it must
    not: which product rule a type mismatch belongs to is the product's, and a
    guessed axis would make `business.rule` pass for the wrong reason.
    """
    notes = " ".join(_schema().errors.notes)

    assert "MethodArgumentTypeMismatchException" in notes
    assert "no axis covers" in notes


def test_the_auth_gap_belongs_to_the_surface_and_the_rules_gap_to_every_route():
    schema = _schema()

    surface = {gap.axis for gap in schema.gaps if gap.endpoint == "*"}
    assert "N-auth" in surface
    for endpoint in schema.endpoints:
        axes = {gap.axis for gap in schema.gaps if gap.endpoint == endpoint.endpoint()}
        assert "N-rule-*" in axes


def test_no_route_of_the_fixture_is_hidden_behind_a_profile():
    """A `@Profile`-gated controller leaves the surface, and the fixture has none.

    Pinned because the failure is silent in the other direction: adding a profile to
    the fixture would drop a route from every generated round, and the report would
    still say the tree was reviewed clean.
    """
    assert all(item.conditional is None for item in _schema().endpoints)


# ── tier 1: the committed tree is the generated tree, plus two named edits ───


def test_the_committed_contracts_are_what_discovery_writes(tmp_path: Path):
    contracts, _ = _source_tree(tmp_path)

    for name in ("health.yaml", "fixture-items-id.yaml"):
        generated = (contracts / name).read_text(encoding="utf-8")
        committed = (FIXTURE / "contracts" / name).read_text(encoding="utf-8")
        assert generated == committed, name


def test_the_one_contract_a_human_touched_differs_in_one_line(tmp_path: Path):
    """The happy body is a value, and a value has no source.

    `POST /fixture/items` has three required fields and no example for any of them,
    so `discover` points it at the empty baseline and says so; the human's whole
    edit is the pointer. Anything else in the diff is a hand-edit nobody declared.
    """
    contracts, _ = _source_tree(tmp_path)
    generated = (contracts / "items.yaml").read_text(encoding="utf-8").splitlines()
    committed = (FIXTURE / "contracts" / "items.yaml").read_text(encoding="utf-8").splitlines()

    differing = [
        (line, other)
        for line, other in zip(generated, committed, strict=True)
        if line != other
    ]
    assert differing == [("baseline: baselines/empty.json", "baseline: baselines/items.json")]


def test_the_committed_cases_are_the_generated_ones_plus_one_hand_written(tmp_path: Path):
    """A case the families cannot express: the same key with a different body.

    `coverage.expand` derives `I-replay` (a replay answers what the first call
    answered) and nothing for the contradictory body, because whether a replayed key
    with new content is a conflict or an overwrite is the product's answer. So it is
    written by hand, and this assertion is what keeps that fact visible: the tree is
    not "what discovery wrote", it is what discovery wrote plus one named case.
    """
    _, cases = _source_tree(tmp_path)
    generated = set(_case_ids(cases))
    committed = set(_case_ids(FIXTURE / "cases"))

    assert committed - generated == {"items-I-replay-conflict"}
    assert generated - committed == set()


def test_the_cases_a_human_touched_differ_only_in_the_placeholder(tmp_path: Path):
    """`path_values.id` was `TODO`; a value is the one thing a source cannot state."""
    _, cases = _source_tree(tmp_path)
    generated = _case_map(cases, "fixture-items-id")
    committed = _case_map(FIXTURE / "cases", "fixture-items-id")

    for case_id, body in generated.items():
        assert body.pop("path_values") == {"id": "TODO"}
        assert committed[case_id].pop("path_values") == {"id": "1"}
        assert body == committed[case_id], case_id


def _case_ids(cases: Path) -> list[str]:
    """Every case id a tree holds, across the areas it spreads them over."""
    found: list[str] = []
    for path in sorted(cases.glob("*.yaml")):
        found.extend(yaml.safe_load(path.read_text(encoding="utf-8")))
    return found


def _case_map(cases: Path, area: str) -> dict:
    return yaml.safe_load((cases / f"{area}.yaml").read_text(encoding="utf-8"))


def test_the_happy_baseline_is_the_one_a_human_wrote():
    baseline = json.loads((FIXTURE / "baselines" / "items.json").read_text(encoding="utf-8"))

    assert set(baseline) == {"name", "item_label", "item_code"}
    assert len(baseline["name"]) <= 40
    assert 2 <= len(baseline["item_label"]) <= 8
    assert baseline["item_code"].isupper() and len(baseline["item_code"]) == 3


# ── tier 2: the same numbers, against a running Spring Boot application ──────


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _java() -> str | None:
    return shutil.which("java")


def _built() -> bool:
    """The jar, built on demand when Maven is there and skipped when it is not."""
    if JAR.is_file():
        return True
    wrapper = FIXTURE / "mvnw"
    if not wrapper.is_file():
        return False
    built = subprocess.run(
        [str(wrapper), "-q", "-DskipTests", "package"],
        cwd=FIXTURE,
        capture_output=True,
        text=True,
    )
    return built.returncode == 0 and JAR.is_file()


@pytest.fixture
def running_fixture(tmp_path: Path):
    """The jar on a free port, logging where the run will look for it."""
    if _java() is None:
        pytest.skip("no JDK on this machine; the harness does not depend on one")
    if not _built():
        pytest.skip("the fixture jar is absent and Maven could not build it")

    port = _free_port()
    log = tmp_path / "fixture.log"
    process = subprocess.Popen(
        [
            _java(),
            "-jar",
            str(JAR),
            f"--server.port={port}",
            f"--logging.file.name={log}",
        ],
        cwd=FIXTURE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(port)
        yield port, log
    finally:
        process.terminate()
        process.wait(timeout=30)


def _wait_for(port: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            httpx.get(
                f"http://127.0.0.1:{port}/fixture/health",
                headers={"X-Fixture-Token": TOKEN},
                timeout=1.0,
            )
            return
        except httpx.HTTPError:
            time.sleep(0.25)
    raise AssertionError(f"the fixture never answered on {port}")


def _run_against(port: int, log: Path, tmp_path: Path) -> Path:
    """The committed round, run against the live app, with the log redirected.

    The base URL and the log path are overridden rather than rewritten: the
    descriptor in the repository names 8120 and a log path relative to its own
    directory, and a test that edited it would be measuring its own edit.
    `test_the_declared_log_path_is_where_the_application_writes` is what keeps that
    declaration honest, because this override is exactly why a wrong one would
    otherwise go unnoticed behind a green run.
    """
    declared = yaml.safe_load(DESCRIPTOR.read_text(encoding="utf-8"))
    project = ProjectView(
        descriptor=ProjectDescriptor.model_validate(
            {
                **declared,
                "environments": {"local": {"base_url": f"http://127.0.0.1:{port}"}},
            }
        ),
        descriptor_dir=DESCRIPTOR.parent,
    ).with_log_paths(web=str(log))
    return execute_round(
        ROUND,
        root=FIXTURE,
        config=config_for(project),
        client=httpx.Client(timeout=10.0),
        runs_dir=tmp_path / "runs",
        secrets={"api_key": TOKEN},
        mode="headless",
        descriptor={"origin": "target", "path": str(DESCRIPTOR)},
    )


def _packs_of(step_dir: Path) -> dict[str, dict]:
    payload = json.loads((step_dir / "packs.json").read_text(encoding="utf-8"))
    return {item["pack_id"]: item for item in payload["results"]}


def _observed(run_dir: Path, case_id: str) -> int:
    """The status the application actually answered for one case."""
    step = find_step_dir(run_dir, case_id)
    assert step is not None, case_id
    return json.loads((step / "response.json").read_text(encoding="utf-8"))["status"]


def test_the_declared_log_path_is_where_the_application_writes():
    """The one descriptor path no generator writes, and the one tier 2 overrides.

    `_run_against` replaces the log path with a tmp file so the run reads a fresh
    log; that is the right thing for the test and the wrong thing for the
    declaration, because a descriptor naming a file the application never writes
    then stays green while a human's run reports `source_missing` on every step.
    The declared path is anchored to the descriptor's own directory, exactly like
    `contract.location`, so `logs/fixture.log` from `qa/` would name `qa/logs/` —
    a directory nothing creates.
    """
    project = ProjectView(descriptor=load_descriptor(DESCRIPTOR), descriptor_dir=DESCRIPTOR.parent)
    (source,) = project.log_sources()

    assert source.id == "web"
    assert source.path.resolve() == (FIXTURE / "logs" / "fixture.log").resolve()
    assert source.path.parent.resolve() == (FIXTURE / "logs").resolve()


@pytest.mark.slow
def test_the_generated_round_passes_against_the_running_application(tmp_path: Path, running_fixture):
    port, log = running_fixture
    run_dir = _run_against(port, log, tmp_path)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    assert summary["counts"]["fail"] == 0
    assert summary["counts"]["pass"] == 21
    assert summary["logs_incomplete"] == 0


@pytest.mark.slow
def test_the_run_says_that_nothing_priced_the_book(tmp_path: Path, running_fixture):
    """Value verification is the project's, and this project declares none.

    The fixture has no price model — it is deliberately dumb, like the toy provider —
    so its oracle is `NeutralOracle`, which asserts a 2xx charged what it says it
    charged and nothing else. The run has to say so out loud: a reader who took those
    numbers for a product's arithmetic would be reading a claim nobody made, and a
    second API cannot reach a product's level of value checking without writing its
    own oracle. This test is the fixture documenting that, in the form CI repeats.
    """
    port, log = running_fixture
    run_dir = _run_against(port, log, tmp_path)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))

    assert summary["oracle"] == "neutral"


@pytest.mark.slow
def test_no_pack_that_could_be_measured_came_back_unmeasured(tmp_path: Path, running_fixture):
    """The fixture declares a route table, an advice, a trace header and a log file.

    So the four packs that need those — HTTP, error, auth and observability — must
    all have run. A skip here would be a claim the fixture made and the harness
    could not check, which is the one thing "unmeasured" is for.
    """
    port, log = running_fixture
    run_dir = _run_against(port, log, tmp_path)
    steps = sorted((run_dir / "steps").iterdir())

    required = {"http.baseline", "auth.surface", "observability"}
    seen: set[str] = set()
    for step in steps:
        packs = _packs_of(step)
        seen |= set(packs)
        for pack in required:
            assert packs[pack]["status"] != "skipped", (step.name, pack)

    # `http.error` only judges a 4xx and `http.success` only a 2xx, so they are the
    # two packs whose presence depends on the case; both families exist here.
    assert "http.error" in seen
    assert "http.success" in seen


@pytest.mark.slow
def test_the_negative_statuses_are_the_ones_the_source_declared(tmp_path: Path, running_fixture):
    """The reader's table, the case's expectation and the app's answer, all equal."""
    port, log = running_fixture
    run_dir = _run_against(port, log, tmp_path)
    table = _schema().errors.statuses

    assert _observed(run_dir, "items-N-omit-name") == table["validation"]
    assert _observed(run_dir, "items-I-missing") == table["missing_header"]
    assert _observed(run_dir, "items-I-format") == table["validation"]
    assert _observed(run_dir, "items-I-replay-conflict") == table["idempotency_conflict"]


@pytest.mark.slow
def test_the_credential_is_refused_before_the_handler_and_still_measured(
    tmp_path: Path, running_fixture
):
    """`auth` is the axis no advice can declare, and the fixture still answers it.

    A servlet filter refuses the request, so there is no `@ExceptionHandler` to read
    — which is exactly why the descriptor declares this one — and the `auth.surface`
    pack checks the refusal on every authenticated route.
    """
    port, log = running_fixture
    run_dir = _run_against(port, log, tmp_path)

    assert _observed(run_dir, "items-N-auth") == 401
    assert _observed(run_dir, "health-N-auth") == 401
    packs = _packs_of(find_step_dir(run_dir, "items-N-auth"))
    assert packs["auth.surface"]["status"] == "pass"


@pytest.mark.slow
def test_the_trace_id_the_harness_stamps_is_the_one_the_log_carries(
    tmp_path: Path, running_fixture
):
    """The observability pack read a real logback file, with the real marker.

    `logs-web.txt` is the collected evidence, so asserting on it is asserting on what
    a reviewer sees rather than on a flag: an empty file with `logs_incomplete` false
    would be the harness lying to itself.
    """
    port, log = running_fixture
    run_dir = _run_against(port, log, tmp_path)
    request = json.loads((find_step_dir(run_dir, "items-H01") / "request.json").read_text())
    trace = request["headers"]["X-Trace-Id"]

    collected = (find_step_dir(run_dir, "items-H01") / "logs-web.txt").read_text(encoding="utf-8")

    assert trace in collected
    assert "trace_id: [" in collected
