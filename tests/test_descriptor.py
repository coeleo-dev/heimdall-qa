"""The descriptor is the destination of the decoupling, so it is checked first.

Two things are pinned here: the three fixtures from F2 §5 load (including the
nine-line toy, which is what makes "any REST API" true), and every rule from F2
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

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "descriptors"


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


@pytest.mark.parametrize("name", ["toy.yaml", "express.yaml", "nokr.yaml"])
def test_every_fixture_loads(name: str):
    descriptor = load_descriptor(FIXTURES / name)
    assert descriptor.project.id


def test_the_toys_descriptor_is_the_entry_ramp():
    """Nine lines, no auth, no logs. If this stops working, the promise is false."""
    descriptor = load_descriptor(FIXTURES / "toy.yaml")
    assert descriptor.project.id == "toy-provider"
    assert list(descriptor.environments) == ["local"]
    assert not descriptor.auth
    assert not descriptor.log_sources
    assert not descriptor.routes


def test_the_express_descriptor_carries_no_nokr_at_all():
    """The boundary proof: `scheme: raw` and `json-lines`, no Nokr vocabulary."""
    descriptor = load_descriptor(FIXTURES / "express.yaml")
    assert descriptor.auth["api_key"].scheme == "raw"
    assert descriptor.log_sources[0].format == "json-lines"
    assert descriptor.trace.header == "x-request-id"


def test_the_repo_descriptor_loads():
    """`qa/project.yaml` is the Nokr descriptor; it must stay valid as 1.4 wires it."""
    descriptor = load_descriptor(REPO / "qa" / "project.yaml")
    assert descriptor.project.id == "nokr"
    assert set(descriptor.environments) == {"sandbox", "production"}


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


def test_an_unknown_key_is_rejected(tmp_path: Path):
    """`extra="forbid"`: a key the author believed was doing something must not be ignored."""
    assert _code(tmp_path, _minimal(nokr_web="http://localhost:8080")) == "DESCRIPTOR_INVALID"


def test_a_secret_reference_takes_exactly_one_source(tmp_path: Path):
    payload = _minimal(secrets={"jwt": {}})
    assert _code(tmp_path, payload) == "DESCRIPTOR_SECRET_INVALID"


def test_a_secret_read_from_file_also_needs_a_key(tmp_path: Path):
    payload = _minimal(secrets={"jwt": {"from_file": "secrets.local.yaml"}})
    assert _code(tmp_path, payload) == "DESCRIPTOR_SECRET_INVALID"


def test_a_secret_never_holds_the_value(tmp_path: Path):
    payload = _minimal(secrets={"jwt": {"value": "nk_test_real"}})
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
