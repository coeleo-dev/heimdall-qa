"""The contract source is a plug-in, and an unread contract is never an empty one.

Three facts are pinned here. First, a contract no longer needs a `dto`: that field
was provenance from a Java target, and requiring it made every other target
undeclarable. Second, `contract.source` is *read* — it was dead configuration until
this point — and a declaration no installed reader can serve fails by name instead
of resolving to "there is nothing to read". Third, two readers need no distribution
at all, so the seam is exercised by the core's own tests rather than only by a
plugin's.
"""

from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
import yaml

from heimdall_qa.contract_source import ENTRY_POINT_GROUP
from heimdall_qa.contract_source import available_contract_sources
from heimdall_qa.contract_source import load_contract_source
from heimdall_qa.descriptor import load_descriptor
from heimdall_qa.errors import HarnessError
from heimdall_qa.project import ProjectView
from heimdall_qa.schema.load import load_campaign
from heimdall_qa.schema.load import load_contract

_MINIMAL = {
    "version": 1,
    "project": {"id": "demo"},
    "environments": {"local": {"base_url": "http://localhost:9000"}},
}

_CONTRACT = {
    "endpoint": "POST /v1/things",
    "baseline": "baselines/empty.json",
    "fields": {},
}


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _view(tmp_path: Path, contract: dict | None = None) -> ProjectView:
    payload = dict(_MINIMAL)
    if contract is not None:
        payload["contract"] = contract
    return ProjectView(descriptor=load_descriptor(_write(tmp_path, "project.yaml", payload)))


@pytest.fixture
def installed(monkeypatch):
    """Replaces the machine's plugin registry with exactly the named readers.

    The core's two built-ins are *not* part of this: they resolve without an entry
    point, so a test that wants to know what a machine without plugins looks like
    gets the built-ins and nothing else.
    """

    def _install(*names: str) -> None:
        entries = [
            EntryPoint(name=name, value=f"fake.{name}:read", group=ENTRY_POINT_GROUP)
            for name in names
        ]
        monkeypatch.setattr(
            "heimdall_qa.contract_source.entry_points",
            lambda **_kwargs: list(entries),
        )

    return _install


# ── a contract no longer needs a dto ──────────────────────────────────────────


def test_a_contract_without_a_dto_loads(tmp_path: Path):
    """`dto` is provenance, so a hand-written or OpenAPI-read contract has none."""
    contract = load_contract(_write(tmp_path, "thing.yaml", _CONTRACT))
    assert contract.dto is None
    assert contract.endpoint == "POST /v1/things"


def test_a_contract_may_still_name_its_dto(tmp_path: Path):
    """Nothing was taken away: a Java target keeps declaring where it came from."""
    payload = {**_CONTRACT, "dto": "com.acme.things.ThingRequest"}
    assert load_contract(_write(tmp_path, "thing.yaml", payload)).dto == (
        "com.acme.things.ThingRequest"
    )


def test_a_round_entry_without_a_dto_loads(tmp_path: Path):
    """`CampaignRound.dto` was required for the same reason, and goes the same way."""
    path = _write(
        tmp_path,
        "campaign.yaml",
        {
            "id": "demo",
            "environment": "local",
            "rounds": [
                {"round": "rounds/a.yaml", "endpoint": "POST /v1/things", "matrix": "A4"}
            ],
        },
    )
    assert load_campaign(path).rounds[0].dto is None


# ── the declaration is an ordered list ────────────────────────────────────────


def test_a_single_source_is_a_one_attempt_list(tmp_path: Path):
    view = _view(tmp_path, {"source": "openapi", "location": "./openapi.yaml"})
    assert view.descriptor.contract.attempts() == ("openapi",)


def test_a_list_keeps_the_declared_order(tmp_path: Path):
    """Order is the fallback policy: the first *installed* reader wins."""
    view = _view(tmp_path, {"source": ["spring", "openapi"]})
    assert view.descriptor.contract.attempts() == ("spring", "openapi")


def test_blank_attempts_are_dropped(tmp_path: Path):
    view = _view(tmp_path, {"source": ["  ", "openapi", ""]})
    assert view.descriptor.contract.attempts() == ("openapi",)


def test_a_source_naming_nothing_is_refused(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        _view(tmp_path, {"source": "   "})
    assert raised.value.code == "DESCRIPTOR_CONTRACT_SOURCE_MISSING"


def test_a_contract_block_is_optional(tmp_path: Path):
    assert _view(tmp_path).contract_source() is None


# ── resolving against what is installed ───────────────────────────────────────


def test_the_two_built_ins_need_no_distribution(tmp_path: Path, installed):
    """A source checkout must resolve its own readers, before any `pip install`."""
    installed()
    available = available_contract_sources()
    assert available["inline"] == "heimdall-qa"
    assert available["openapi"] == "heimdall-qa"


def test_a_built_in_loads_from_the_core():
    assert load_contract_source("openapi").name == "openapi"


def test_inline_reads_nothing_on_purpose():
    """The authored contract is the source, so there is nothing to derive."""
    schema = load_contract_source("inline").read("contracts/whatever.yaml")
    assert schema.names() == ()
    assert schema.gaps == ()


def test_inline_is_always_available(tmp_path: Path, installed):
    installed()
    resolved = _view(
        tmp_path, {"source": "inline", "location": "contracts/x.yaml"}
    ).contract_source()
    assert resolved.source == "inline"
    assert resolved.location == "contracts/x.yaml"


def test_a_built_in_wins_when_it_comes_first(tmp_path: Path, installed):
    installed("spring")
    assert _view(tmp_path, {"source": ["openapi", "spring"]}).contract_source().source == (
        "openapi"
    )


def test_the_first_installed_plugin_wins(tmp_path: Path, installed):
    installed("bru")
    resolved = _view(tmp_path, {"source": ["spring", "bru"]}).contract_source()
    assert resolved.source == "bru"


def test_the_order_decides_and_not_the_name(tmp_path: Path, installed):
    installed("openapi", "spring", "bru")
    assert _view(tmp_path, {"source": ["spring", "bru"]}).contract_source().source == "spring"
    assert _view(tmp_path, {"source": ["bru", "spring"]}).contract_source().source == "bru"


def test_spec_version_travels_with_the_resolved_source(tmp_path: Path, installed):
    installed("openapi")
    resolved = _view(
        tmp_path, {"source": "openapi", "spec_version": "3.1"}
    ).contract_source()
    assert resolved.spec_version == "3.1"


def test_nothing_installed_is_a_named_failure(tmp_path: Path, installed):
    """ "I could not read the contract" must not look like "the contract is empty"."""
    installed()
    with pytest.raises(HarnessError) as raised:
        _view(tmp_path, {"source": ["spring", "bru"]}).contract_source()
    assert raised.value.code == "CONTRACT_SOURCE_UNAVAILABLE"
    assert "spring" in raised.value.message
    assert "inline" in raised.value.hint


def test_the_failure_names_what_is_installed(tmp_path: Path, installed):
    installed("bru")
    with pytest.raises(HarnessError) as raised:
        _view(tmp_path, {"source": "spring"}).contract_source()
    assert "bru" in raised.value.hint


def test_an_unknown_reader_name_is_carried_not_guessed(tmp_path: Path, installed):
    """The core names no framework: it carries `whatever`, and the registry decides."""
    installed("whatever")
    assert _view(tmp_path, {"source": "whatever"}).contract_source().source == "whatever"


def test_an_unknown_reader_that_is_not_installed_is_a_failure(tmp_path: Path, installed):
    installed()
    with pytest.raises(HarnessError) as raised:
        _view(tmp_path, {"source": "whatever"}).contract_source()
    assert raised.value.code == "CONTRACT_SOURCE_UNAVAILABLE"
