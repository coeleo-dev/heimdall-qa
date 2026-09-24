"""`discover` read the API, and everything it could not read is written down.

The gate this file exists for is the one the plan calls "zero human editing on the
mechanical attributes": from an OpenAPI document alone, the contracts, the cases and
the baseline come out runnable, and the only thing a human has to touch is what the
source could not say. So the tests come in three groups.

The derived half: an area, a contract with the right `idempotency`, fields carrying
`max_length`/`pattern`/`example`, and cases whose generated `diff` is already the
right shape.

The refused half: a path parameter with no value and an operation with no declared
2xx become `TODO`, and `validate` refuses the round that contains them, which is what
makes the hole loud instead of green.

The repeatable half: a second `discover` writes nothing, because a generator that
overwrites a human's work is a generator nobody runs twice.
"""

import json
from pathlib import Path

import pytest
import yaml

from heimdall_qa.contract_source import ApiSchema
from heimdall_qa.contract_source import EndpointSchema
from heimdall_qa.contract_source import FieldSchema
from heimdall_qa.contract_source import RuleSchema
from heimdall_qa.discovery import area_for
from heimdall_qa.discovery import contract_payload
from heimdall_qa.discovery import discover
from heimdall_qa.discovery import harness_endpoint
from heimdall_qa.discovery import render_report
from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.load import load_case
from heimdall_qa.schema.load import load_contract
from heimdall_qa.schema.models import CaseFile
from heimdall_qa.testing import project_at
from heimdall_qa.validate import validate_round
from provider_marks import needs_spring_reader

_TOY = Path(__file__).resolve().parent / "fixtures" / "openapi" / "toy.json"

_DESCRIPTOR = """
version: 1
project:
  id: toy
environments:
  local:
    base_url: http://127.0.0.1:8099
contract:
  source: openapi
  location: ../openapi.json
"""


def _tree(tmp_path: Path, document: Path | None = None) -> Path:
    """A minimal target repo: a descriptor in `qa/` and the document beside it."""
    (tmp_path / "qa").mkdir(parents=True, exist_ok=True)
    (tmp_path / "qa" / "project.yaml").write_text(_DESCRIPTOR, encoding="utf-8")
    source = document if document is not None else _TOY
    (tmp_path / "openapi.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def _discover(tmp_path: Path, **kwargs):
    project = project_at(tmp_path / "qa" / "project.yaml")
    return discover(
        project,
        contracts_dir=tmp_path / "contracts",
        cases_dir=tmp_path / "cases",
        report_path=tmp_path / "DISCOVERY.md",
        **kwargs,
    )


def _by_endpoint(report):
    return {endpoint.endpoint: endpoint for endpoint in report.endpoints}


# ── the mechanical attributes come out right ──────────────────────────────────


def test_every_route_in_the_document_becomes_a_contract(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    assert [endpoint.endpoint for endpoint in report.endpoints] == [
        "GET /toy/health",
        "POST /toy/items",
        "GET /toy/items/{id}",
        "DELETE /toy/items/{id}",
        "POST /toy/convert",
    ]
    assert all(endpoint.contract_path.is_file() for endpoint in report.endpoints)


def test_the_contract_names_the_endpoint_and_its_area(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    contract = load_contract(_by_endpoint(report)["POST /toy/items"].contract_path)
    assert contract.endpoint == "POST /toy/items"
    assert contract.area == "items"


def test_the_declared_idempotency_header_becomes_the_contracts_idempotency(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    items = load_contract(_by_endpoint(report)["POST /toy/items"].contract_path)
    convert = load_contract(_by_endpoint(report)["POST /toy/convert"].contract_path)
    assert items.idempotency == "header_uuid_v4"
    assert convert.idempotency == "none"


def test_a_document_that_declares_no_auth_yields_a_contract_that_claims_none(tmp_path: Path):
    """`auth: none` is what the document implies: no security scheme was declared."""
    report = _discover(_tree(tmp_path))
    assert all(
        load_contract(endpoint.contract_path).auth == "none"
        for endpoint in report.endpoints
    )


def test_bounds_and_patterns_survive_the_round_trip(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    contract = load_contract(_by_endpoint(report)["POST /toy/items"].contract_path)
    assert contract.fields["name"].max_length == 40
    assert contract.fields["name"].example == "widget"
    assert contract.fields["label"].pattern == "^[A-Z]{3}$"
    assert contract.fields["metadata"].max_keys == 5


def test_a_body_behind_a_ref_becomes_fields(tmp_path: Path):
    """The `$ref` case is the ordinary case: without it there are no fields at all."""
    report = _discover(_tree(tmp_path))
    contract = load_contract(_by_endpoint(report)["POST /toy/items"].contract_path)
    assert list(contract.fields) == [
        "name",
        "amount",
        "currency",
        "label",
        "tax_id",
        "metadata",
    ]
    assert contract.fields["name"].required is True


def test_all_of_is_merged_into_one_field_set(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    contract = load_contract(_by_endpoint(report)["POST /toy/convert"].contract_path)
    assert "scheduled_for" in contract.fields
    assert contract.fields["scheduled_for"].required is True


def test_a_path_parameter_makes_the_contract_resource_shaped(tmp_path: Path):
    """Which is what generates `N-notfound` and `S-bola` without being asked."""
    report = _discover(_tree(tmp_path))
    endpoint = _by_endpoint(report)["GET /toy/items/{id}"]
    assert load_contract(endpoint.contract_path).resource_id_in_path is True
    assert "get-toy-items-id-N-notfound" in endpoint.case_ids
    assert "get-toy-items-id-S-bola" in endpoint.case_ids


def test_the_cases_match_what_coverage_would_derive(tmp_path: Path):
    """Nothing is filtered: the contract on disk is the only input to expansion."""
    from heimdall_qa.coverage import expand

    report = _discover(_tree(tmp_path))
    for endpoint in report.endpoints:
        contract = load_contract(endpoint.contract_path)
        assert list(endpoint.case_ids) == [item.case_id for item in expand(contract)]


def test_a_generated_case_carries_the_mechanical_expect(tmp_path: Path):
    _discover(_tree(tmp_path))
    case = _case(tmp_path, "items", "items-H01")
    assert case.expect.status == 201


def test_a_generated_boundary_case_is_already_the_right_length(tmp_path: Path):
    """Zero human editing on the mechanical attribute: the seed fills to the bound."""
    _discover(_tree(tmp_path))
    case = _case(tmp_path, "items", "items-B-max-name")
    assert case.diff["set"]["name"] == ("widget" * 7)[:40]
    assert len(case.diff["set"]["name"]) == 40


def test_a_bound_with_no_example_still_fills_to_the_bound(tmp_path: Path):
    _discover(_tree(tmp_path))
    case = _case(tmp_path, "items", "items-B-max-tax_id")
    assert len(case.diff["set"]["tax_id"]) == 14


def test_the_baseline_the_contracts_point_at_is_written(tmp_path: Path):
    """Without it every generated case fails at load, which reads as a harness bug."""
    _discover(_tree(tmp_path))
    assert (tmp_path / "baselines" / "empty.json").read_text(encoding="utf-8").strip() == "{}"


def test_the_baseline_is_not_overwritten_when_it_already_exists(tmp_path: Path):
    (tmp_path / "baselines").mkdir(parents=True)
    (tmp_path / "baselines" / "empty.json").write_text('{"amount": 1000}', encoding="utf-8")
    _discover(_tree(tmp_path))
    assert json.loads((tmp_path / "baselines" / "empty.json").read_text()) == {"amount": 1000}


# ── what the source could not say is a TODO and a report line ─────────────────


def test_an_operation_without_a_2xx_gets_a_status_todo(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    endpoint = _by_endpoint(report)["DELETE /toy/items/{id}"]
    case = _case(tmp_path, endpoint.area, f"{endpoint.area}-H01")
    assert case.expect.status == "TODO"


def test_a_path_parameter_without_a_value_gets_a_path_todo(tmp_path: Path):
    """A silent `{id}` in the URL would be a case testing a route that does not exist."""
    report = _discover(_tree(tmp_path))
    endpoint = _by_endpoint(report)["GET /toy/items/{id}"]
    case = _case(tmp_path, endpoint.area, f"{endpoint.area}-H01")
    assert case.path_values == {"id": "TODO"}


def test_the_missing_status_is_reported_as_a_gap(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    endpoint = _by_endpoint(report)["DELETE /toy/items/{id}"]
    assert [gap.axis for gap in endpoint.gaps] == ["H01"]


def test_the_body_axes_the_format_cannot_spell_are_reported(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    endpoint = _by_endpoint(report)["POST /toy/items"]
    assert {gap.axis for gap in endpoint.gaps} == {
        "O-alias-*",
        "window / B-max-*-future / B-min-*-past",
        "N-denylist-*",
        "N-rule-*",
    }


def test_a_clean_endpoint_reports_nothing(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    assert _by_endpoint(report)["GET /toy/health"].gaps == ()


def test_the_placeholders_are_listed_separately_from_the_gaps(tmp_path: Path):
    """A gap is coverage nobody wrote; a placeholder blocks the cases that exist."""
    report = _discover(_tree(tmp_path))
    endpoint = _by_endpoint(report)["GET /toy/items/{id}"]
    assert endpoint.gaps == ()
    assert endpoint.placeholders == (
        (
            "path_values.id",
            "the source says a path parameter exists but not what value it takes",
        ),
    )


def test_the_report_names_the_source_and_the_counts(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    text = report.report_path.read_text(encoding="utf-8")
    assert text.startswith("# Contract discovery")
    assert "source: `openapi` (provided by `heimdall-qa`)" in text
    assert "gaps: 9 across 3 of 5 endpoints" in text


def test_the_report_has_a_section_per_endpoint(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    text = report.report_path.read_text(encoding="utf-8")
    for endpoint in report.endpoints:
        assert f"## {endpoint.endpoint}" in text


def test_the_report_says_which_cases_cannot_run(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    text = report.report_path.read_text(encoding="utf-8")
    assert "## Cases that cannot run yet" in text
    assert "| `GET /toy/items/{id}` | `path_values.id` |" in text


def test_the_report_has_no_blocked_section_when_nothing_is_blocked(tmp_path: Path):
    document = json.loads(_TOY.read_text(encoding="utf-8"))
    del document["paths"]["/toy/items/{id}"]
    document["paths"]["/toy/items"]["post"]["responses"] = {"201": {"description": "ok"}}
    document["paths"]["/toy/convert"]["post"]["responses"] = {"202": {"description": "ok"}}
    (tmp_path / "doc.json").write_text(json.dumps(document), encoding="utf-8")
    report = _discover(_tree(tmp_path, tmp_path / "doc.json"))
    assert "## Cases that cannot run yet" not in report.report_path.read_text(
        encoding="utf-8"
    )


def test_a_required_field_with_no_example_names_the_baseline_as_the_hole(tmp_path: Path):
    """The happy body of a POST is a value, and the report has to say it is missing.

    Every other generated case is honest about it — they omit the field, overrun it,
    break its pattern — and the one case that must send it *correctly* has nothing to
    send, because `baselines/empty.json` is `{}`. Without this line a discovered POST
    looks runnable and fails on a request nobody wrote, which is exactly the silent
    green the module exists to refuse.
    """
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/toy/widgets": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {"name": {"type": "string"}},
                                }
                            }
                        }
                    },
                    "responses": {"201": {"description": "created"}},
                }
            }
        },
    }
    (tmp_path / "doc.json").write_text(json.dumps(document), encoding="utf-8")
    report = _discover(_tree(tmp_path, tmp_path / "doc.json"))

    endpoint = _by_endpoint(report)["POST /toy/widgets"]
    assert [where for where, _ in endpoint.placeholders] == ["baseline"]
    assert "| `POST /toy/widgets` | `baseline` |" in report.report_path.read_text(
        encoding="utf-8"
    )


def test_an_example_in_the_document_closes_the_baseline_hole(tmp_path: Path):
    """The line is about a missing fact, not about bodies: an example is the fact."""
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/toy/widgets": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["name"],
                                    "properties": {
                                        "name": {"type": "string", "example": "widget"}
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"201": {"description": "created"}},
                }
            }
        },
    }
    (tmp_path / "doc.json").write_text(json.dumps(document), encoding="utf-8")
    report = _discover(_tree(tmp_path, tmp_path / "doc.json"))
    assert _by_endpoint(report)["POST /toy/widgets"].placeholders == ()


def test_the_report_says_so_when_an_endpoint_leaves_nothing_open(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    text = report.report_path.read_text(encoding="utf-8")
    assert "Nothing left open" in text


# ── a discovered tree is refused by `validate`, loudly ────────────────────────


def _case(tmp_path: Path, area: str, case_id: str) -> CaseFile:
    """One case out of the area file `discover` writes for an endpoint."""
    return load_case(tmp_path / "cases" / f"{area}.yaml", case_id)


def _round_for(tmp_path: Path, area: str) -> Path:
    """A one-line round over every case of one discovered area."""
    rounds = tmp_path / "rounds"
    rounds.mkdir(parents=True, exist_ok=True)
    path = rounds / f"{area}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": area,
                "suite": f"suites/{area}.yaml",
                "mode": "review",
                "environment": "local",
                "include": [f"cases/{area}.yaml"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_validate_refuses_the_path_placeholder(tmp_path: Path):
    _discover(_tree(tmp_path))
    errors = validate_round(_round_for(tmp_path, "get-toy-items-id"), tmp_path)
    assert any("path_values.id is TODO" in error for error in errors)


def test_validate_refuses_the_status_placeholder(tmp_path: Path):
    _discover(_tree(tmp_path))
    errors = validate_round(_round_for(tmp_path, "delete-toy-items-id"), tmp_path)
    assert any("expect.status is TODO" in error for error in errors)


def test_validate_accepts_an_area_the_document_fully_described(tmp_path: Path):
    """The other half of the gate: the derived cases pass review untouched."""
    _discover(_tree(tmp_path))
    assert validate_round(_round_for(tmp_path, "items"), tmp_path) == []


# ── a second run changes nothing ──────────────────────────────────────────────


def test_a_second_run_writes_no_case(tmp_path: Path):
    _discover(_tree(tmp_path))
    report = _discover(_tree(tmp_path))
    assert report.written_case_count() == 0
    assert report.kept_case_count() == 48


def test_a_second_run_leaves_the_report_honest_about_it(tmp_path: Path):
    _discover(_tree(tmp_path))
    report = _discover(_tree(tmp_path))
    assert "cases: 0 written, 48 kept" in report.report_path.read_text(encoding="utf-8")


def test_a_human_edit_survives_a_second_run(tmp_path: Path):
    """A generator that overwrites a person's work is one nobody runs twice."""
    _discover(_tree(tmp_path))
    case_path = tmp_path / "cases" / "items.yaml"
    case_path.write_text(
        case_path.read_text(encoding="utf-8").replace("status: 201", "status: 200"),
        encoding="utf-8",
    )
    _discover(_tree(tmp_path))
    assert "status: 200" in case_path.read_text(encoding="utf-8")


def test_a_hand_written_contract_survives_and_still_expands(tmp_path: Path):
    _discover(_tree(tmp_path))
    contract_path = tmp_path / "contracts" / "items.yaml"
    contract_path.write_text(
        contract_path.read_text(encoding="utf-8").replace("max_length: 40", "max_length: 12"),
        encoding="utf-8",
    )
    report = _discover(_tree(tmp_path))
    assert load_contract(contract_path).fields["name"].max_length == 12
    assert "items-B-max-name" in _by_endpoint(report)["POST /toy/items"].case_ids


def test_force_replaces_the_contract(tmp_path: Path):
    _discover(_tree(tmp_path))
    contract_path = tmp_path / "contracts" / "items.yaml"
    contract_path.write_text("endpoint: nope\n", encoding="utf-8")
    _discover(_tree(tmp_path), force=True)
    assert load_contract(contract_path).endpoint == "POST /toy/items"


# ── the declaration, and the override ─────────────────────────────────────────


def test_a_source_override_beats_the_declaration(tmp_path: Path):
    """`--source` is how a project tries the next reader without editing a file."""
    _tree(tmp_path)
    report = _discover(tmp_path, source="openapi")
    assert report.source == "openapi"


def test_an_override_that_nobody_serves_fails_by_name(tmp_path: Path):
    _tree(tmp_path)
    with pytest.raises(HarnessError) as raised:
        _discover(tmp_path, source="nobody-serves-this")
    assert raised.value.code == "CONTRACT_SOURCE_UNAVAILABLE"


@needs_spring_reader
def test_an_installed_reader_pointed_at_the_wrong_tree_fails_as_a_reader(tmp_path: Path):
    """`spring` is installed and the tree is not Java, so the failure names the reader.

    It is a different failure from "no reader serves that name": the name resolved,
    and what it was pointed at is what did not work. Both have to be loud, and they
    have to be different, or a project cannot tell a missing plugin from a wrong path.

    Skipped where the reader is not installed, because the claim *is* that an
    installed reader fails on its own terms — uninstalled, the same call fails
    earlier and for a reason that has nothing to do with the tree.
    """
    _tree(tmp_path)
    with pytest.raises(HarnessError) as raised:
        _discover(tmp_path, source="spring", location=str(tmp_path))
    assert raised.value.code == "CONTRACT_SOURCE_FAILED"


def test_a_project_without_a_contract_block_is_told_what_to_declare(tmp_path: Path):
    (tmp_path / "qa").mkdir(parents=True)
    (tmp_path / "qa" / "project.yaml").write_text(
        "version: 1\nproject: {id: toy}\nenvironments: {local: {base_url: http://x}}\n",
        encoding="utf-8",
    )
    with pytest.raises(HarnessError) as raised:
        project = project_at(tmp_path / "qa" / "project.yaml")
        discover(
            project,
            contracts_dir=tmp_path / "contracts",
            cases_dir=tmp_path / "cases",
            report_path=tmp_path / "DISCOVERY.md",
        )
    assert raised.value.code == "DESCRIPTOR_CONTRACT_SOURCE_MISSING"
    assert "contract:" in raised.value.hint


def test_a_relative_location_is_read_against_the_descriptor(tmp_path: Path):
    """`../openapi.json` is written by someone standing in `qa/`, not in the cwd."""
    _tree(tmp_path)
    (tmp_path / "qa" / "project.yaml").write_text(
        _DESCRIPTOR.replace("../openapi.json", "openapi.json"), encoding="utf-8"
    )
    (tmp_path / "qa" / "openapi.json").write_text(_TOY.read_text(encoding="utf-8"), encoding="utf-8")
    report = _discover(tmp_path)
    assert report.location == str((tmp_path / "qa" / "openapi.json").resolve())


def test_an_unreadable_document_fails_before_anything_is_written(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": "world"}', encoding="utf-8")
    with pytest.raises(HarnessError) as raised:
        _discover(_tree(tmp_path, bad))
    assert raised.value.code == "CONTRACT_SOURCE_FAILED"
    assert not (tmp_path / "contracts").exists()


# ── the area names ────────────────────────────────────────────────────────────


def test_a_plain_path_keeps_the_last_segment_as_its_area():
    assert area_for("POST /v1/payments") == "payments"


def test_a_path_parameter_becomes_part_of_the_area():
    """`{id}` as a file name would produce a case called `{id}-H01.yaml`."""
    assert area_for("GET /toy/items/{id}") == "toy-items-id"


def test_two_methods_on_one_path_get_the_method_prefixed(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    areas = {endpoint.endpoint: endpoint.area for endpoint in report.endpoints}
    assert areas["GET /toy/items/{id}"] == "get-toy-items-id"
    assert areas["DELETE /toy/items/{id}"] == "delete-toy-items-id"


def test_a_path_with_one_method_keeps_the_bare_area(tmp_path: Path):
    report = _discover(_tree(tmp_path))
    areas = {endpoint.endpoint: endpoint.area for endpoint in report.endpoints}
    assert areas["POST /toy/items"] == "items"
    assert areas["GET /toy/health"] == "health"


def test_two_routes_that_would_share_a_file_are_refused(tmp_path: Path):
    """`:id` and `id` flatten to one area, so neither file may win.

    The fallback for a last-segment collision is the whole path, and a literal `id`
    segment is what is left once it has run: refusing is the answer, because choosing
    between two routes whose names collide would make the generated tree depend on the
    order the document was walked.
    """
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/toy/{id}/x": {"get": {"responses": {"200": {}}}},
            "/toy/id/x": {"get": {"responses": {"200": {}}}},
        },
    }
    (tmp_path / "doc.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(HarnessError) as raised:
        _discover(_tree(tmp_path, tmp_path / "doc.json"))
    assert raised.value.code == "DISCOVERY_AREA_COLLISION"
    assert "toy-id-x" in raised.value.message


def test_a_last_segment_collision_is_resolved_by_the_whole_path(tmp_path: Path):
    """Two `GET`s whose last segment is `items` both want the area `items`."""
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/toy/items": {"get": {"responses": {"200": {}}}},
            "/toy/dashboard/items": {"get": {"responses": {"200": {}}}},
        },
    }
    (tmp_path / "doc.json").write_text(json.dumps(document), encoding="utf-8")
    report = _discover(_tree(tmp_path, tmp_path / "doc.json"))

    assert sorted(endpoint.area for endpoint in report.endpoints) == [
        "toy-dashboard-items",
        "toy-items",
    ]


# ── the contract a source with more to say produces ───────────────────────────


def _endpoint(**overrides) -> EndpointSchema:
    return EndpointSchema(method="POST", path="/v1/things", **overrides)


def test_a_declared_rule_is_written_into_the_contract():
    payload = contract_payload(
        _endpoint(rules=(RuleSchema(id="TIERED", status=422, code="TIERED"),)),
        "things",
        ProjectView(),
    )
    assert payload["rules"] == [{"id": "TIERED", "status": 422, "code": "TIERED"}]


def test_a_rule_without_a_code_carries_only_its_status():
    payload = contract_payload(_endpoint(rules=(RuleSchema(id="X", status=400),)), "things", ProjectView())
    assert payload["rules"] == [{"id": "X", "status": 400}]


def test_a_declared_denylist_and_alias_are_carried_into_the_fields():
    payload = contract_payload(
        _endpoint(
            fields=(
                FieldSchema(
                    name="tax_id",
                    json_name="taxId",
                    json_alias=True,
                    denylist=("tax_id",),
                    invalid="00000000000",
                ),
            )
        ),
        "things",
        ProjectView(),
    )
    assert payload["fields"]["tax_id"] == {
        "required": False,
        "json": "taxId",
        "denylist": ["tax_id"],
        "json_alias": True,
        "invalid": "00000000000",
    }


def test_a_declared_async_mode_is_carried_as_the_contracts_literal():
    assert contract_payload(_endpoint(async_mode=True), "things", ProjectView())["async"] == "worker"
    assert contract_payload(_endpoint(async_mode=False), "things", ProjectView())["async"] == "sync"


def test_no_async_mode_means_no_key_at_all():
    assert "async" not in contract_payload(_endpoint(), "things", ProjectView())


def test_a_declared_dedup_is_carried():
    assert contract_payload(_endpoint(dedup="invoice"), "things", ProjectView())["dedup"] == "invoice"


def test_the_json_name_defaults_to_the_field_name():
    """An OpenAPI property key *is* the wire name, so a source need not repeat it."""
    payload = contract_payload(_endpoint(fields=(FieldSchema(name="amount"),)), "things", ProjectView())
    assert payload["fields"]["amount"]["json"] == "amount"


# ── the spelling a source uses is not the spelling a case needs ───────────────


def test_a_path_parameter_becomes_the_harness_template():
    """`/items/{id}` is the URI template; `{{id}}` is what a case interpolates.

    A contract written with a single brace produces a request for a literal
    `/items/{id}` — the run then fails on a URL the source never promised, and no
    test in the corpus caught it because the parity file normalizes both spellings
    to compare routes.
    """
    payload = contract_payload(
        EndpointSchema(
            method="GET", path="/v1/things/{thingId}", path_params=("thingId",)
        ),
        "things",
        ProjectView(),
    )
    assert payload["endpoint"] == "GET /v1/things/{{thingId}}"


def test_every_parameter_in_a_segment_becomes_a_template():
    endpoint = EndpointSchema(method="GET", path="/v1/odd/{a}/{b}/x")
    assert harness_endpoint(endpoint) == "GET /v1/odd/{{a}}/{{b}}/x"


def test_the_endpoint_a_reader_returned_is_untouched():
    """The seam stays clean: the IR describes the API, only the YAML is the harness's."""
    endpoint = EndpointSchema(method="GET", path="/v1/things/{id}")
    assert endpoint.endpoint() == "GET /v1/things/{id}"
    assert harness_endpoint(endpoint) == "GET /v1/things/{{id}}"


# ── a route the reader gated ──────────────────────────────────────────────────


def _gated(tmp_path: Path):
    """A target whose reader reports one ordinary route and one it gated.

    A reader marks a route conditional when the deployment may not serve it — a
    `@Profile`-gated controller. No document format can express that, so the stub
    reader stands in for the one that can, and the walk is tested as the walk.
    """
    from heimdall_qa import discovery as module

    schema = ApiSchema(
        source="stub",
        endpoints=(
            EndpointSchema(method="GET", path="/v1/health", success_status=200),
            EndpointSchema(
                method="POST",
                path="/admin/v1/reverse",
                success_status=200,
                conditional="admin",
            ),
        ),
    )

    class Reader:
        name = "stub"

        def read(self, location):
            return schema

    (tmp_path / "qa").mkdir(parents=True, exist_ok=True)
    (tmp_path / "qa" / "project.yaml").write_text(_DESCRIPTOR, encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        module, "_reader", lambda project, source, location: (Reader(), "stub")
    )
    return monkeypatch


def test_a_gated_route_is_reported_and_not_generated(tmp_path: Path):
    """A contract for a route the deployment does not serve fails for no reason."""
    patch = _gated(tmp_path)
    try:
        report = _discover(tmp_path, source="stub")
    finally:
        patch.undo()

    assert [endpoint.endpoint for endpoint in report.endpoints] == ["GET /v1/health"]
    assert report.off_surface == (("POST /admin/v1/reverse", "admin"),)
    assert not (tmp_path / "contracts" / "reverse.yaml").exists()

    rendered = render_report(report)
    assert "## Routes read but not generated" in rendered
    assert "| `POST /admin/v1/reverse` | admin |" in rendered


def test_naming_a_gated_route_generates_it_and_says_why(tmp_path: Path):
    """`--route` is a human saying the profile is on, and the report keeps the caveat.

    Listing the route under "not generated" after generating it would be a lie, so the
    condition moves onto the endpoint, where it reads as a caveat on the contract
    rather than as an absence.
    """
    patch = _gated(tmp_path)
    try:
        report = _discover(tmp_path, source="stub", route="POST /admin/v1/reverse")
    finally:
        patch.undo()

    assert [endpoint.endpoint for endpoint in report.endpoints] == ["POST /admin/v1/reverse"]
    assert report.off_surface == ()
    assert report.endpoints[0].conditional == "admin"

    rendered = render_report(report)
    assert "## Routes read but not generated" not in rendered
    assert "conditional: `admin`" in rendered
