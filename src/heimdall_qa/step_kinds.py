"""The step kinds the harness knows how to execute.

A step kind used to be an `if` in two places at once: `packs.run_all` looked for
`case_kind == "ui"` and `SuiteRun.execute_all` looked for `step.ui`. Nothing
rejected a kind that no code implemented, so a suite could declare a step the
harness could not run and the failure only surfaced deep into the run — or not
at all, because `SuiteStep` ignored unknown keys in silence.

Registering the kinds here turns "not implemented" into a load-time error that
names the kind and lists the ones that exist. This is also the seam a provider
extends later: the core stops owning the set, it only owns the ones it ships.
"""

from collections.abc import Iterable

# A step either snapshots the probes before the round, runs a case N times, or
# compares the probes after the cases ran. Those are the three the core ships.
PROBE_BEGIN = "probe_begin"
LOOP = "loop"
PROBE = "probe"

SUITE_STEP_KINDS = frozenset({PROBE_BEGIN, LOOP, PROBE})


def registered_step_kinds() -> frozenset[str]:
    """The kinds a suite may declare today."""
    return SUITE_STEP_KINDS


def unknown_step_kinds(declared: Iterable[str]) -> list[str]:
    """The declared kinds nobody registered, in a stable order."""
    return sorted(name for name in declared if name not in SUITE_STEP_KINDS)


def unknown_step_kind_message(name: str) -> str:
    """The actionable form: what was declared, and what exists instead."""
    known = ", ".join(sorted(SUITE_STEP_KINDS))
    return f"step kind not registered: {name} (registered kinds: {known})"
