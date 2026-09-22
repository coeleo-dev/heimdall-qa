from dataclasses import asdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from heimdall_qa.oracle.money import ingest_flat
from heimdall_qa.oracle.money import metering_amount
from heimdall_qa.oracle.money import to_decimal


@dataclass
class BookLine:
    source: str
    included: bool
    exclusion: str | None
    amount: str | None
    http_status: int
    transaction_id: str | None = None
    idempotency_key: str | None = None
    rating_status: str | None = None


class Book:
    def __init__(self) -> None:
        self.lines: list[BookLine] = []
        self._idempotency: set[str] = set()
        self._transaction_ids: set[str] = set()

    def add_metering(
        self,
        *,
        status_code: int,
        body: dict[str, Any] | None,
        amount: Any,
        idempotency_key: str | None,
    ) -> BookLine:
        line = self._metering_line(status_code, body, amount, idempotency_key)
        self.lines.append(line)
        return line

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
    ) -> BookLine:
        line = self._ingest_line(
            status_code,
            transaction_id,
            idempotency_key,
            rating_status,
            quantity,
            unit_amount,
            flat_amount,
        )
        self.lines.append(line)
        return line

    def included_total(self) -> Decimal:
        total = Decimal("0.00000")
        for line in self.lines:
            if line.included and line.amount is not None:
                total += to_decimal(line.amount)
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "included": [asdict(line) for line in self.lines if line.included],
            "excluded": [asdict(line) for line in self.lines if not line.included],
            "total": str(self.included_total()),
        }

    def _metering_line(
        self,
        status_code: int,
        body: dict[str, Any] | None,
        amount: Any,
        idempotency_key: str | None,
    ) -> BookLine:
        if self._is_replay(idempotency_key, None):
            return self._excluded("metering", "replay", status_code, idempotency_key=idempotency_key)
        self._remember(idempotency_key, None)
        if status_code == 402:
            return self._excluded("metering", "http_402", status_code, idempotency_key=idempotency_key)
        if status_code == 429:
            return self._excluded("metering", "http_429", status_code, idempotency_key=idempotency_key)
        status = (body or {}).get("status")
        if status_code == 202 and status == "SUCCESS":
            debit = metering_amount(amount)
            return BookLine(
                "metering",
                True,
                None,
                str(debit),
                status_code,
                idempotency_key=idempotency_key,
            )
        return self._excluded("metering", None, status_code, idempotency_key=idempotency_key)

    def _ingest_line(
        self,
        status_code: int,
        transaction_id: str | None,
        idempotency_key: str | None,
        rating_status: str | None,
        quantity: Any,
        unit_amount: Any,
        flat_amount: Any,
    ) -> BookLine:
        if self._is_replay(idempotency_key, transaction_id):
            return self._excluded(
                "ingest",
                "replay",
                status_code,
                transaction_id=transaction_id,
                idempotency_key=idempotency_key,
                rating_status=rating_status,
            )
        self._remember(idempotency_key, transaction_id)
        if status_code == 429:
            return self._excluded(
                "ingest",
                "http_429",
                status_code,
                transaction_id=transaction_id,
                idempotency_key=idempotency_key,
            )
        if rating_status in {"INSUFFICIENT", "FROZEN"}:
            return self._excluded(
                "ingest",
                "ingest_insufficient",
                status_code,
                transaction_id=transaction_id,
                idempotency_key=idempotency_key,
                rating_status=rating_status,
            )
        if rating_status == "SUCCESS":
            debit = ingest_flat(quantity, unit_amount, flat_amount)
            return BookLine(
                "ingest",
                True,
                None,
                str(debit),
                status_code,
                transaction_id=transaction_id,
                idempotency_key=idempotency_key,
                rating_status=rating_status,
            )
        return self._excluded(
            "ingest",
            None,
            status_code,
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            rating_status=rating_status,
        )

    def _is_replay(self, idempotency_key: str | None, transaction_id: str | None) -> bool:
        if idempotency_key and idempotency_key in self._idempotency:
            return True
        if transaction_id and transaction_id in self._transaction_ids:
            return True
        return False

    def _remember(self, idempotency_key: str | None, transaction_id: str | None) -> None:
        if idempotency_key:
            self._idempotency.add(idempotency_key)
        if transaction_id:
            self._transaction_ids.add(transaction_id)

    def _excluded(
        self,
        source: str,
        exclusion: str | None,
        status_code: int,
        *,
        transaction_id: str | None = None,
        idempotency_key: str | None = None,
        rating_status: str | None = None,
    ) -> BookLine:
        return BookLine(
            source,
            False,
            exclusion,
            None,
            status_code,
            transaction_id=transaction_id,
            idempotency_key=idempotency_key,
            rating_status=rating_status,
        )
