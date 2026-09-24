"""The descriptor is the destination of the decoupling, so it is checked first.

Two things are pinned here: the three fixtures from F2 §5 load (including the
six-line toy, which is what makes "any REST API" true), and every rule from F2
§6 fails with the code it promises — a rule with no code is a rule nobody can
assert on.
"""

from pathlib import Path

import pytest
import yaml

from heimdall_qa.descriptor import ORIGIN_EXPLICIT
from heimdall_qa.descriptor import ORIGIN_PROVIDER
from heimdall_qa.descriptor import ORIGIN_TARGET
from heimdall_qa.descriptor import load_descriptor
from heimdall_qa.descriptor import provider_descriptor_path
from heimdall_qa.descriptor import resolve_descriptor
from heimdall_qa.descriptor import target_descriptor_path
from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.load import resolve_path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "descriptors"
#: The descriptor of the stand-in target repo. It lives at `qa/project.yaml` —
#: the path a real repo uses — so `resolve_descriptor(FIXTURES)` finds it, which
#: is what makes the fixture tree behave like a target repo.
TARGET_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "qa" / "project.yaml"


def _minimal(**overrides: object) -> dict:
    base: dict = {
        "version": 1,
        "project": {"id": "demo"},
        "environments": {"local": {"base_url": "http://localhost:9000"}},
    }
    base.update(overrides)
    return base


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _code(tmp_path: Path, payload: dict) -> str:
    with pytest.raises(HarnessError) as raised:
        load_descriptor(_write(tmp_path, payload))
    return raised.value.code


# ── the three fixtures ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["toy.yaml", "express.yaml"])
def test_every_fixture_loads(name: str):
    descriptor = load_descriptor(FIXTURES / name)
    assert descriptor.project.id


def test_the_target_fixture_loads():
    """`tests/fixtures/qa/project.yaml` stands in for a target repo's own file."""
    descriptor = load_descriptor(TARGET_FIXTURE)
    assert descriptor.project.id == "demo"


def test_the_toys_descriptor_is_the_entry_ramp():
    """Nine lines, no auth, no logs. If this stops working, the promise is false."""
    descriptor = load_descriptor(FIXTURES / "toy.yaml")
    assert descriptor.project.id == "toy-provider"
    assert list(descriptor.environments) == ["local"]
    assert not descriptor.auth
    assert not descriptor.log_sources
    assert not descriptor.routes


def test_the_toys_descriptor_names_no_provider():
    """The entry ramp runs on the neutral oracle: nothing to install, nothing to
    resolve. A toy that required a provider would not be a ramp."""
    assert load_descriptor(FIXTURES / "toy.yaml").provider is None


def test_the_express_descriptor_carries_no_product_vocabulary():
    """The boundary proof: `scheme: raw` and `json-lines`, no product vocabulary."""
    descriptor = load_descriptor(FIXTURES / "express.yaml")
    assert descriptor.auth["api_key"].scheme == "raw"
    assert descriptor.log_sources[0].format == "json-lines"
    assert descriptor.trace.header == "x-request-id"


# ── where the content lives (fase 1.5a) ───────────────────────────────────────


def test_content_root_defaults_to_the_harness_root(tmp_path: Path):
    """A project with its cases at the top level declares nothing and pays nothing."""
    descriptor = load_descriptor(_write(tmp_path, _minimal()))
    project = ProjectView(descriptor=descriptor)
    assert project.content_root(tmp_path) == tmp_path


def test_content_root_prefixes_a_declared_directory(tmp_path: Path):
    payload = _minimal(content_root="providers/demo")
    project = ProjectView(descriptor=load_descriptor(_write(tmp_path, payload)))
    assert project.content_root(tmp_path) == tmp_path / "providers" / "demo"


def test_resolve_path_joins_under_the_declared_content_root(tmp_path: Path):
    payload = _minimal(content_root="providers/demo")
    project = ProjectView(descriptor=load_descriptor(_write(tmp_path, payload)))
    assert (
        resolve_path(tmp_path, "cases/h01.yaml", project)
        == tmp_path / "providers" / "demo" / "cases" / "h01.yaml"
    )


def test_resolve_path_passes_an_absolute_path_through(tmp_path: Path):
    payload = _minimal(content_root="providers/demo")
    project = ProjectView(descriptor=load_descriptor(_write(tmp_path, payload)))
    absolute = tmp_path / "elsewhere" / "case.yaml"
    assert resolve_path(tmp_path, str(absolute), project) == absolute


# ── one test per required field ───────────────────────────────────────────────


def test_project_id_is_required(tmp_path: Path):
    assert _code(tmp_path, _minimal(project={})) == "DESCRIPTOR_ID_INVALID"


def test_project_id_must_be_a_slug(tmp_path: Path):
    assert _code(tmp_path, _minimal(project={"id": "My API"})) == "DESCRIPTOR_ID_INVALID"


def test_an_environment_needs_a_base_url(tmp_path: Path):
    assert _code(tmp_path, _minimal(environments={"local": {}})) == "DESCRIPTOR_BASE_URL_MISSING"


def test_a_descriptor_without_environment_is_rejected(tmp_path: Path):
    assert _code(tmp_path, _minimal(environments={})) == "DESCRIPTOR_NO_ENVIRONMENT"


def test_a_route_must_declare_a_prefix(tmp_path: Path):
    payload = _minimal(routes=[{"prefix": "  ", "auth": "none"}])
    assert _code(tmp_path, payload) == "DESCRIPTOR_PREFIX_MISSING"


def test_a_route_may_not_name_an_undeclared_auth(tmp_path: Path):
    payload = _minimal(routes=[{"prefix": "/v1/", "auth": "ghost"}])
    assert _code(tmp_path, payload) == "DESCRIPTOR_UNKNOWN_AUTH"


def test_a_route_may_not_name_an_undeclared_budget(tmp_path: Path):
    payload = _minimal(routes=[{"prefix": "/v1/", "auth": "none", "budget": "fast"}])
    assert _code(tmp_path, payload) == "DESCRIPTOR_UNKNOWN_BUDGET"


def test_a_header_name_must_be_an_http_identifier(tmp_path: Path):
    payload = _minimal(auth={"api_key": {"header": "X Api Key"}})
    assert _code(tmp_path, payload) == "DESCRIPTOR_INVALID_HEADER"


def test_a_duplicate_prefix_may_not_answer_two_ways(tmp_path: Path):
    payload = _minimal(
        auth={"jwt": {"header": "Authorization"}, "admin": {"header": "X-Admin"}},
        routes=[
            {"prefix": "/platform/", "auth": "jwt"},
            {"prefix": "/platform/", "auth": "admin"},
        ],
    )
    assert _code(tmp_path, payload) == "DESCRIPTOR_DUPLICATE_ROUTE"


def test_a_duplicate_prefix_with_the_same_auth_is_fine(tmp_path: Path):
    """No false positive: repeating a prefix with the same answer is redundant, not broken."""
    payload = _minimal(
        routes=[
            {"prefix": "/v1/", "auth": "none"},
            {"prefix": "/v1/", "auth": "none"},
        ]
    )
    assert load_descriptor(_write(tmp_path, payload)).routes


def test_log_sources_require_a_trace_header(tmp_path: Path):
    payload = _minimal(log_sources=[{"id": "web", "path": "./app.log"}])
    assert _code(tmp_path, payload) == "DESCRIPTOR_TRACE_HEADER_MISSING"


def test_log_sources_are_fine_once_the_trace_header_is_declared(tmp_path: Path):
    payload = _minimal(
        trace={"header": "X-Trace-Id"},
        log_sources=[{"id": "web", "path": "./app.log"}],
    )
    assert load_descriptor(_write(tmp_path, payload)).log_sources


def test_a_source_defaults_to_sync_and_propagating(tmp_path: Path):
    """The two declarations a source does not have to make, and their defaults."""
    payload = _minimal(
        trace={"header": "X-Trace-Id"},
        log_sources=[{"id": "web", "path": "./app.log"}],
    )
    source = load_descriptor(_write(tmp_path, payload)).log_sources[0]

    assert source.role == "sync"
    assert source.propagate is True
    assert source.max_tail_bytes > 0


def test_a_dotted_marker_field_is_a_path_not_a_header_name(tmp_path: Path):
    """`data.trace.id` was refused by the HTTP-header rule that used to judge it."""
    payload = _minimal(
        trace={"header": "X-Trace-Id"},
        log_sources=[
            {
                "id": "billing",
                "path": "./billing.jsonl",
                "format": "json-lines",
                "marker_field": "data.trace.id",
            }
        ],
    )
    source = load_descriptor(_write(tmp_path, payload)).log_sources[0]

    assert source.marker_field == "data.trace.id"


def test_a_marker_field_that_is_not_a_path_is_refused(tmp_path: Path):
    payload = _minimal(
        trace={"header": "X-Trace-Id"},
        log_sources=[
            {"id": "billing", "path": "./b.jsonl", "format": "json-lines", "marker_field": "data..id"}
        ],
    )
    assert _code(tmp_path, payload) == "DESCRIPTOR_INVALID_FIELD_PATH"


def test_a_source_carries_the_id_one_way(tmp_path: Path):
    """Two ways to find the id in one file is a contradiction, not a preference."""
    payload = _minimal(
        trace={"header": "X-Trace-Id"},
        log_sources=[
            {
                "id": "billing",
                "path": "./b.jsonl",
                "format": "json-lines",
                "marker": "trace_id: \\[{trace_id}\\]",
                "marker_field": "traceId",
            }
        ],
    )
    assert _code(tmp_path, payload) == "DESCRIPTOR_LOG_SOURCE_AMBIGUOUS"


def test_json_lines_needs_a_field_to_look_for(tmp_path: Path):
    payload = _minimal(
        trace={"header": "X-Trace-Id"},
        log_sources=[
            {
                "id": "billing",
                "path": "./b.jsonl",
                "format": "json-lines",
                "marker": "trace_id: \\[{trace_id}\\]",
            }
        ],
    )
    assert _code(tmp_path, payload) == "DESCRIPTOR_LOG_SOURCE_AMBIGUOUS"


def test_max_tail_bytes_cannot_be_zero(tmp_path: Path):
    """A ceiling of zero is a source that can never be read, declared as if it can."""
    payload = _minimal(
        trace={"header": "X-Trace-Id"},
        log_sources=[{"id": "web", "path": "./app.log", "max_tail_bytes": 0}],
    )
    assert _code(tmp_path, payload) == "DESCRIPTOR_INVALID"


def test_the_retired_propagate_to_worker_key_is_refused(tmp_path: Path):
    """It was read by nobody; the per-source `propagate` replaced it.

    Refusing it is the point: a project that still declares the old key must be
    told, not silently keep a declaration that no longer does anything.
    """
    payload = _minimal(
        trace={"header": "X-Trace-Id", "propagate_to_worker": True},
        log_sources=[{"id": "web", "path": "./app.log"}],
    )
    assert _code(tmp_path, payload) == "DESCRIPTOR_INVALID"


def test_an_unknown_key_is_rejected(tmp_path: Path):
    """`extra="forbid"`: a key the author believed was doing something must not be ignored."""
    assert _code(tmp_path, _minimal(legacy_web="http://localhost:8080")) == "DESCRIPTOR_INVALID"


def test_a_secret_reference_takes_exactly_one_source(tmp_path: Path):
    payload = _minimal(secrets={"jwt": {}})
    assert _code(tmp_path, payload) == "DESCRIPTOR_SECRET_INVALID"


def test_a_secret_read_from_file_also_needs_a_key(tmp_path: Path):
    payload = _minimal(secrets={"jwt": {"from_file": "secrets.local.yaml"}})
    assert _code(tmp_path, payload) == "DESCRIPTOR_SECRET_INVALID"


def test_a_secret_never_holds_the_value(tmp_path: Path):
    payload = _minimal(secrets={"jwt": {"value": "test_key_real"}})
    assert _code(tmp_path, payload) == "DESCRIPTOR_INVALID"


# ── precedence (ADR-01) ───────────────────────────────────────────────────────


def test_the_target_descriptor_wins_over_the_provider_fallback(tmp_path: Path):
    root = tmp_path / "target"
    (root / "qa").mkdir(parents=True)
    (root / "qa" / "project.yaml").write_text(
        yaml.safe_dump(_minimal(project={"id": "from-target"})), encoding="utf-8"
    )
    harness = tmp_path / "harness"
    fallback = provider_descriptor_path(harness, "demo")
    fallback.parent.mkdir(parents=True)
    fallback.write_text(
        yaml.safe_dump(_minimal(project={"id": "from-provider"})), encoding="utf-8"
    )

    resolved = resolve_descriptor(root, provider_id="demo", harness_dir=harness)

    assert resolved is not None
    assert resolved.origin == ORIGIN_TARGET
    assert resolved.descriptor.project.id == "from-target"


def test_the_provider_fallback_is_used_when_the_target_declares_nothing(tmp_path: Path):
    root = tmp_path / "target"
    root.mkdir()
    harness = tmp_path / "harness"
    fallback = provider_descriptor_path(harness, "demo")
    fallback.parent.mkdir(parents=True)
    fallback.write_text(
        yaml.safe_dump(_minimal(project={"id": "from-provider"})), encoding="utf-8"
    )

    resolved = resolve_descriptor(root, provider_id="demo", harness_dir=harness)

    assert resolved is not None
    assert resolved.origin == ORIGIN_PROVIDER
    assert resolved.descriptor.project.id == "from-provider"


def test_an_explicit_descriptor_wins_over_the_target(tmp_path: Path):
    root = tmp_path / "target"
    (root / "qa").mkdir(parents=True)
    (root / "qa" / "project.yaml").write_text(
        yaml.safe_dump(_minimal(project={"id": "from-target"})), encoding="utf-8"
    )
    explicit = _write(tmp_path, _minimal(project={"id": "explicit"}))

    resolved = resolve_descriptor(root, explicit=str(explicit))

    assert resolved is not None
    assert resolved.origin == ORIGIN_EXPLICIT
    assert resolved.descriptor.project.id == "explicit"


def test_no_descriptor_anywhere_is_not_an_error(tmp_path: Path):
    """Optional until a case needs it: today's rounds must keep running without one."""
    root = tmp_path / "target"
    root.mkdir()
    assert resolve_descriptor(root) is None


def test_an_explicit_path_that_does_not_exist_is_an_error(tmp_path: Path):
    """Being asked for a specific file and silently using none is how a run lies."""
    with pytest.raises(HarnessError) as raised:
        resolve_descriptor(tmp_path, explicit=str(tmp_path / "missing.yaml"))

    assert raised.value.code == "DESCRIPTOR_NOT_FOUND"


def test_the_target_path_is_the_root_relative_convention(tmp_path: Path):
    assert target_descriptor_path(tmp_path) == tmp_path / "qa" / "project.yaml"


# ── the failure axes (fase 2.1a) ──────────────────────────────────────────────


def test_the_default_status_of_every_axis_matches_the_reference_corpus():
    """400 for a malformed body, 401 for auth, 404 for a missing id, 409 for replay.

    The scalar `validation_status` that used to live here declared 422 over a
    corpus that answers 400 — a field nobody read, contradicting the cases that
    were actually written. These defaults are what the corpus measures.
    """
    view = ProjectView()
    assert view.validation_status() == 400
    assert view.error_status("missing_header") == 400
    assert view.error_status("auth") == 401
    assert view.error_status("not_found") == 404
    assert view.error_status("idempotency_conflict") == 409
    assert view.error_status("environment_conflict") == 400
    assert view.error_status("environment_isolation") == 403


def test_the_envelope_defaults_to_none(tmp_path: Path):
    assert ProjectView().error_envelope() == "none"


def test_a_declared_axis_status_wins_over_the_default(tmp_path: Path):
    payload = _minimal(errors={"statuses": {"validation": 422}})
    view = ProjectView(descriptor=load_descriptor(_write(tmp_path, payload)))
    assert view.validation_status() == 422
    assert view.error_status("auth") == 401


def test_the_express_fixture_declares_its_envelope(tmp_path: Path):
    descriptor = load_descriptor(FIXTURES / "express.yaml")
    view = ProjectView(descriptor=descriptor)
    assert view.error_envelope() == "code_message"
    assert view.error_status("not_found") == 404


def test_an_axis_nobody_asks_about_is_refused(tmp_path: Path):
    """Answering 400 for a typo would hide it, so the caller gets a named failure."""
    with pytest.raises(HarnessError) as raised:
        ProjectView().error_status("va1idation")
    assert raised.value.code == "DESCRIPTOR_UNKNOWN_ERROR_AXIS"


def test_declaring_an_axis_nobody_asks_about_is_refused(tmp_path: Path):
    """A declaration nothing reads is dead configuration, and it is refused at load."""
    payload = _minimal(errors={"statuses": {"not_an_axis": 400}})
    assert _code(tmp_path, payload) == "DESCRIPTOR_UNKNOWN_ERROR_AXIS"


def test_a_declared_status_must_be_a_failure(tmp_path: Path):
    payload = _minimal(errors={"statuses": {"validation": 200}})
    assert _code(tmp_path, payload) == "DESCRIPTOR_ERROR_STATUS_INVALID"


def test_the_resolved_descriptor_reports_where_it_came_from(tmp_path: Path):
    _write(tmp_path, _minimal(project={"id": "demo"}))
    (tmp_path / "qa").mkdir()
    target = tmp_path / "qa" / "project.yaml"
    target.write_text(
        yaml.safe_dump(_minimal(project={"id": "demo"})), encoding="utf-8"
    )

    resolved = resolve_descriptor(tmp_path)

    assert resolved is not None
    assert resolved.as_summary() == {"origin": ORIGIN_TARGET, "path": str(target)}
