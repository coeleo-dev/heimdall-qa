"""The provider seam, from the core's side only.

Two promises are pinned here. First, a descriptor that names no provider runs on
the *neutral* oracle: a 2xx mutation is counted, nothing ever decreases, and no
exclusion carries a product's name — which is what an API with no settlement
semantics should get instead of an error. Second, a descriptor that *does* name a
provider is resolved through `importlib.metadata` and never through an import, so
an unknown id fails by name and an installed one is asked for its oracle.

The reference provider's own behaviour is not asserted here: it belongs to that
distribution's suite, which is what keeps `heimdall-qa` installable on its own.
"""

from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest
import yaml

from heimdall_qa.descriptor import load_descriptor
from heimdall_qa.errors import HarnessError
from heimdall_qa.oracle import BookLine
from heimdall_qa.oracle import NeutralOracle
from heimdall_qa.project import ProjectView
from heimdall_qa.provider import ENTRY_POINT_GROUP
from heimdall_qa.provider import load_provider
from heimdall_qa.provider import oracle_factory
from heimdall_qa.schema.models import SurfaceSpec


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


def _view(tmp_path: Path, payload: dict) -> ProjectView:
    return ProjectView(descriptor=load_descriptor(_write(tmp_path, payload)))


def _surface(
    expect: str,
    *,
    id: str = "wallet",
    jsonpath: str = "$.balance",
    min_delta: str | None = None,
) -> SurfaceSpec:
    return SurfaceSpec(
        id=id,
        get="/surface",
        jsonpath=jsonpath,
        expect=expect,
        min_delta=min_delta,
    )


class _EntryPoint:
    """The half of `importlib.metadata.EntryPoint` this module actually reads."""

    def __init__(self, name: str, loaded: object) -> None:
        self.name = name
        self._loaded = loaded

    def load(self) -> object:
        return self._loaded


def _installed(monkeypatch: pytest.MonkeyPatch, loaded: object) -> None:
    """Pretend one provider distribution is installed under the id `acme`."""

    def fake_entry_points(group: str) -> list[_EntryPoint]:
        return [_EntryPoint("acme", loaded)] if group == ENTRY_POINT_GROUP else []

    monkeypatch.setattr("heimdall_qa.provider.entry_points", fake_entry_points)


# ── the neutral default ───────────────────────────────────────────────────────


def test_a_descriptor_without_a_provider_gets_the_neutral_oracle(tmp_path: Path):
    assert isinstance(_view(tmp_path, _minimal()).oracle(), NeutralOracle)


def test_no_descriptor_at_all_gets_the_neutral_oracle():
    """A round with no project file still runs; it just has no opinions."""
    assert isinstance(ProjectView().oracle(), NeutralOracle)


def test_the_neutral_oracle_counts_every_successful_mutation():
    oracle = NeutralOracle()
    oracle.add_metering(
        status_code=202,
        body={"status": "SUCCESS"},
        amount="0.01000",
        idempotency_key="k1",
    )
    oracle.add_ingest(
        status_code=202,
        transaction_id="tx-1",
        idempotency_key="k2",
        rating_status="SUCCESS",
        quantity=100,
        unit_amount="0.00003",
    )

    assert str(oracle.included_total()) == "0.01300"


def test_the_neutral_oracle_names_no_product_exclusion():
    """402 is a status the core does not interpret; the line says so and stops."""
    oracle = NeutralOracle()
    oracle.add_metering(
        status_code=402,
        body={"status": "REJECTED"},
        amount="0.01",
        idempotency_key=None,
    )

    book = oracle.as_dict()

    assert book["included"] == []
    assert book["excluded"][0]["exclusion"] is None
    assert book["excluded"][0]["http_status"] == 402


def test_a_success_with_no_amount_is_excluded_and_named():
    """The common REST case: a 201 that costs nothing anyone declared.

    Including it as zero would let a probe pass against an amount nobody wrote,
    which is the one thing a harness must not do.
    """
    oracle = NeutralOracle()
    oracle.add_metering(
        status_code=201, body={"id": "itm_1"}, amount=None, idempotency_key=None
    )

    book = oracle.as_dict()

    assert book["included"] == []
    assert book["excluded"][0]["exclusion"] == "no_amount"
    assert str(oracle.included_total()) == "0"


def test_the_neutral_oracle_still_refuses_a_replayed_key():
    """Deduplication is not a price model: charging twice is wrong everywhere."""
    oracle = NeutralOracle()
    for _ in range(2):
        oracle.add_metering(
            status_code=202, body=None, amount="0.01", idempotency_key="same"
        )

    assert len(oracle.lines) == 2
    assert oracle.lines[1].exclusion == "replay"
    assert str(oracle.included_total()) == "0.01000"


def test_the_neutral_oracle_moves_an_exact_surface_by_the_book():
    oracle = NeutralOracle()
    surface = _surface("exact")

    assert str(oracle.expected_after(surface, "1.00000", "0.01300")) == "1.01300"


def test_the_neutral_oracle_does_not_fall_on_its_own():
    """Without a declared price model, a decreasing balance cannot be expected."""
    oracle = NeutralOracle()
    surface = _surface("exact")
    book_total = "0.01300"

    assert not oracle.matches(surface, "1.00000", "0.98700", book_total)
    assert oracle.matches(surface, "1.00000", "1.01300", book_total)


def test_unchanged_and_increase_are_read_as_declared():
    oracle = NeutralOracle()

    assert oracle.matches(_surface("unchanged"), "100.00", "100.00", "0")
    assert not oracle.matches(_surface("unchanged"), "100.00", "99.00", "0")
    assert oracle.matches(_surface("increase"), "1.0", "1.5", "0")
    assert not oracle.matches(_surface("increase"), "1.0", "1.0", "0")


def test_min_delta_is_honoured():
    oracle = NeutralOracle()
    surface = _surface("increase", id="overview", jsonpath="$.percent", min_delta="0.5")

    assert oracle.matches(surface, "1.0", "1.5", "0")
    assert not oracle.matches(surface, "1.0", "1.2", "0")


def test_an_absent_value_never_matches():
    """Not measured is not a pass, whatever the expectation said."""
    oracle = NeutralOracle()

    assert not oracle.matches(_surface("exact"), None, "1.00000", "0.01")
    assert not oracle.matches(_surface("exact"), "1.00000", None, "0.01")


def test_the_book_line_is_the_evidence_shape():
    line = BookLine("metering", True, None, "0.01000", 202)

    assert sorted(vars(line)) == [
        "amount",
        "exclusion",
        "http_status",
        "idempotency_key",
        "included",
        "rating_status",
        "source",
        "transaction_id",
    ]


def test_the_neutral_oracle_refuses_a_model_it_cannot_price():
    """It only multiplies, so every declared model is one it can price."""
    assert NeutralOracle().require_pricing_model("TIERED") is None


# ── resolution ────────────────────────────────────────────────────────────────


def test_a_provider_id_that_is_not_a_slug_is_refused_at_load(tmp_path: Path):
    with pytest.raises(HarnessError) as raised:
        load_descriptor(_write(tmp_path, _minimal(provider="Acme")))

    assert raised.value.code == "DESCRIPTOR_PROVIDER_INVALID"


def test_a_provider_that_is_not_installed_is_a_named_error(tmp_path: Path):
    view = _view(tmp_path, _minimal(provider="acme"))

    with pytest.raises(HarnessError) as raised:
        view.oracle()

    assert raised.value.code == "PROVIDER_NOT_FOUND"
    assert "acme" in raised.value.hint


def test_a_provider_is_found_through_its_entry_point(monkeypatch: pytest.MonkeyPatch):
    """The whole point of the phase: `importlib.metadata`, not an import."""
    module = SimpleNamespace(oracle=NeutralOracle)
    _installed(monkeypatch, module)

    assert load_provider("acme") is module
    assert isinstance(oracle_factory("acme")(), NeutralOracle)


def test_an_entry_point_may_point_straight_at_the_factory(
    monkeypatch: pytest.MonkeyPatch,
):
    _installed(monkeypatch, NeutralOracle)

    assert isinstance(oracle_factory("acme")(), NeutralOracle)


def test_a_provider_module_without_an_oracle_is_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
):
    _installed(monkeypatch, ModuleType("heimdall_qa_acme"))

    with pytest.raises(HarnessError) as raised:
        oracle_factory("acme")

    assert raised.value.code == "PROVIDER_INVALID"


def _exploding_import(name: str) -> object:
    """A provider whose module *is* found, but whose own import fails."""
    missing = "acme_dependency"
    raise ModuleNotFoundError(f"No module named '{missing}'", name=missing)


def test_a_second_missing_import_inside_a_provider_is_not_masked(
    monkeypatch: pytest.MonkeyPatch,
):
    """Only "not installed" is an installation problem; a broken provider is not."""
    monkeypatch.setattr(
        "heimdall_qa.provider.entry_points", lambda group: []
    )
    monkeypatch.setattr(
        "heimdall_qa.provider.import_module",
        _exploding_import,
    )

    with pytest.raises(ModuleNotFoundError) as raised:
        oracle_factory("acme")

    assert raised.value.name == "acme_dependency"


def test_a_descriptor_naming_a_provider_is_what_selects_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End of the wire: the name in the YAML decides, and nothing else does."""
    _installed(monkeypatch, SimpleNamespace(oracle=NeutralOracle))

    without = _view(tmp_path, _minimal())
    with_provider = _view(tmp_path, _minimal(provider="acme"))

    assert without.provider_id() is None
    assert with_provider.provider_id() == "acme"
    assert isinstance(without.oracle(), NeutralOracle)
    assert isinstance(with_provider.oracle(), NeutralOracle)
