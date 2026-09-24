"""The oracle seam: who decides what an observed mutation was worth.

A run settles what it saw twice: once *while* it runs, when every metering or
ingest response becomes a charged or excluded line in the book, and once *after*,
when a probe compares a surface against that total. Both answers are arithmetic
over one product's price model — a wallet gets smaller when metering is consumed,
an ingest is charged `quantity × unit_amount + flat`, a `FROZEN` rating never
settles. None of that is true of a REST API in general, so the core keeps only the
seam:

* `Oracle`, with the few questions the HTTP path asks;
* `NeutralOracle`, the default that answers them naively — a 2xx mutation is
  counted, nothing ever decreases — which is what a project with no settlement
  semantics wants, and what a project with them overrides.

Which oracle a run uses is decided by the descriptor's `provider:` and resolved
by `heimdall_qa.provider`. The core never imports a provider: a product's price
model lives in its own distribution, behind this seam.
"""

from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from typing import Protocol

from heimdall_qa.jsonpath import MISSING
from heimdall_qa.money import to_decimal
from heimdall_qa.schema.models import SurfaceSpec

#: The scale the default quantizes money to. A project that bills in whole units
#: quantizes differently, which is why this is the default and not a constant.
DEFAULT_SCALE = Decimal("0.00000")


@dataclass(frozen=True)
class BookLine:
    """One mutation as the run saw it: charged, or excluded and why.

    The shape is evidence, not domain: `book.json` is read back by a human, so the
    keys are stable even though which lines get in, and for how much, is entirely
    the oracle's answer.
    """

    source: str
    included: bool
    exclusion: str | None
    amount: str | None
    http_status: int
    transaction_id: str | None = None
    idempotency_key: str | None = None
    rating_status: str | None = None


class Oracle(Protocol):
    """The questions the HTTP path asks about value.

    Deliberately small. The book is reached through it rather than handed over: a
    run calls `add_*` and reads `as_dict`, and never sees a line object, so a
    product that prices differently does not have to model its rows the way the
    core does.
    """

    def require_pricing_model(self, pricing_model: str) -> None:
        """Refuse a price model this oracle cannot compute, loudly."""
        ...

    def add_metering(
        self,
        *,
        status_code: int,
        body: Mapping[str, Any] | None,
        amount: Any,
        idempotency_key: str | None,
    ) -> None:
        """Record one metering response."""
        ...

    def add_ingest(
        self,
        *,
        status_code: int,
        transaction_id: str | None,
        idempotency_key: str | None,
        rating_status: str | None,
        quantity: Any,
        unit_amount: Any,
        flat_amount: Any = 0,
    ) -> None:
        """Record one ingest response, which may settle only after a poll."""
        ...

    def included_total(self) -> Any:
        """What the run believes it has charged so far."""
        ...

    def expected_after(
        self,
        surface: SurfaceSpec,
        before: Any,
        oracle_total: Any,
    ) -> Any:
        """What `surface` should read once `oracle_total` settles."""
        ...

    def matches(
        self,
        surface: SurfaceSpec,
        before: Any,
        after: Any,
        oracle_total: Any,
    ) -> bool:
        """Whether `after` is the value the oracle expected."""
        ...

    def as_dict(self) -> dict[str, Any]:
        """The book as evidence."""
        ...


class NeutralOracle:
    """A default with no price model: what a 2xx charged is what it charged.

    It exists so that an API with no settlement semantics — the common case, and
    the one the harness must not pretend to understand — still runs a probe with a
    defined answer instead of an error. Two choices are deliberately naive and said
    out loud here: every successful mutation is included, and no surface ever
    decreases on its own. A project where a balance falls registers the oracle that
    knows it.

    It names no exclusion beyond a replayed idempotency key, because naming one —
    `http_402`, `ingest_insufficient` — is a statement about a product's contract,
    and this class exists precisely because the core does not have one.
    """

    def __init__(self) -> None:
        self.lines: list[BookLine] = []
        self._seen_idempotency: set[str] = set()
        self._seen_transactions: set[str] = set()

    def require_pricing_model(self, pricing_model: str) -> None:
        """The neutral oracle only multiplies, so it has no model to refuse."""
        return None

    def add_metering(
        self,
        *,
        status_code: int,
        body: Mapping[str, Any] | None,
        amount: Any,
        idempotency_key: str | None,
    ) -> None:
        if self._is_replay(idempotency_key, None):
            self._exclude("metering", "replay", status_code, idempotency_key, None)
            return
        self._remember(idempotency_key, None)
        if not _is_success(status_code):
            self._exclude("metering", None, status_code, idempotency_key, None)
            return
        if amount is None:
            # A success with nothing to price. Naming it is the only honest
            # answer: inventing a zero would make a probe pass on an amount
            # nobody declared.
            self._exclude("metering", "no_amount", status_code, idempotency_key, None)
            return
        self._include("metering", amount, status_code, idempotency_key, None)

    def add_ingest(
        self,
        *,
        status_code: int,
        transaction_id: str | None,
        idempotency_key: str | None,
        rating_status: str | None,
        quantity: Any,
        unit_amount: Any,
        flat_amount: Any = 0,
    ) -> None:
        if self._is_replay(idempotency_key, transaction_id):
            self._exclude(
                "ingest",
                "replay",
                status_code,
                idempotency_key,
                transaction_id,
                rating_status,
            )
            return
        self._remember(idempotency_key, transaction_id)
        if not _is_success(status_code):
            self._exclude(
                "ingest",
                None,
                status_code,
                idempotency_key,
                transaction_id,
                rating_status,
            )
            return
        if quantity is None or unit_amount is None:
            self._exclude(
                "ingest",
                "no_amount",
                status_code,
                idempotency_key,
                transaction_id,
                rating_status,
            )
            return
        charged = (
            to_decimal(quantity) * to_decimal(unit_amount) + to_decimal(flat_amount)
        )
        self._include(
            "ingest",
            charged,
            status_code,
            idempotency_key,
            transaction_id,
            rating_status,
        )

    def included_total(self) -> Decimal:
        return sum(
            (to_decimal(line.amount) for line in self.lines if line.included),
            Decimal(0),
        )

    def expected_after(
        self,
        surface: SurfaceSpec,
        before: Any,
        oracle_total: Any,
    ) -> Any:
        if _is_absent(before):
            return None
        if surface.expect in {"unchanged", "increase"}:
            return before
        # No wallet: an `exact` surface moves by the whole book, in one direction.
        return _quantize(to_decimal(before) + to_decimal(oracle_total))

    def matches(
        self,
        surface: SurfaceSpec,
        before: Any,
        after: Any,
        oracle_total: Any,
    ) -> bool:
        if _is_absent(after) or _is_absent(before):
            return False
        if surface.expect == "unchanged":
            return to_decimal(before).compare(to_decimal(after)) == 0
        if surface.expect == "increase":
            return _increased(before, after, surface.min_delta)
        expected = self.expected_after(surface, before, oracle_total)
        return to_decimal(after).compare(to_decimal(expected)) == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "included": [asdict(line) for line in self.lines if line.included],
            "excluded": [asdict(line) for line in self.lines if not line.included],
            "total": str(self.included_total()),
        }

    def _is_replay(
        self,
        idempotency_key: str | None,
        transaction_id: str | None,
    ) -> bool:
        if idempotency_key and idempotency_key in self._seen_idempotency:
            return True
        return bool(transaction_id and transaction_id in self._seen_transactions)

    def _remember(
        self,
        idempotency_key: str | None,
        transaction_id: str | None,
    ) -> None:
        if idempotency_key:
            self._seen_idempotency.add(idempotency_key)
        if transaction_id:
            self._seen_transactions.add(transaction_id)

    def _include(
        self,
        source: str,
        amount: Any,
        status_code: int,
        idempotency_key: str | None,
        transaction_id: str | None,
        rating_status: str | None = None,
    ) -> None:
        self.lines.append(
            BookLine(
                source,
                True,
                None,
                str(_quantize(to_decimal(amount))),
                status_code,
                transaction_id=transaction_id,
                idempotency_key=idempotency_key,
                rating_status=rating_status,
            )
        )

    def _exclude(
        self,
        source: str,
        exclusion: str | None,
        status_code: int,
        idempotency_key: str | None,
        transaction_id: str | None,
        rating_status: str | None = None,
    ) -> None:
        self.lines.append(
            BookLine(
                source,
                False,
                exclusion,
                None,
                status_code,
                transaction_id=transaction_id,
                idempotency_key=idempotency_key,
                rating_status=rating_status,
            )
        )


def _is_absent(value: Any) -> bool:
    return value is MISSING or value is None


def _is_success(status_code: int) -> bool:
    return 200 <= status_code < 300


def _increased(before: Any, after: Any, min_delta: Any) -> bool:
    delta = to_decimal(after) - to_decimal(before)
    if delta.compare(Decimal(0)) <= 0:
        return False
    if min_delta is None:
        return True
    return delta.compare(to_decimal(min_delta)) >= 0


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(DEFAULT_SCALE)
