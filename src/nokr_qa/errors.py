from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass
class HarnessError(Exception):
    code: str
    message: str
    hint: str = ""
    details: tuple[str, ...] = field(default_factory=tuple)
    exit_code: int = 1

    def __str__(self) -> str:
        return self.message


def format_cli(err: HarnessError) -> str:
    lines = [f"error[{err.code}]: {err.message}"]
    if err.hint:
        lines.append(f"hint: {err.hint}")
    for detail in err.details:
        lines.append(f"  {detail}")
    return "\n".join(lines)


def to_dict(err: HarnessError) -> dict[str, object]:
    return {
        "code": err.code,
        "message": err.message,
        "hint": err.hint,
        "details": list(err.details),
        "exit_code": err.exit_code,
    }
