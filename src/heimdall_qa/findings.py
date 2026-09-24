"""What `validate` refuses, and the index of reasons it is allowed to give.

`validate` used to answer with a sentence. A sentence cannot be acted on: the agent
had to have read the prose that explained it, and the prose lived in a skill, so the
rule was applied by memory instead of by the tool. A finding carries the four things a
reader needs — a stable `code`, the `where` it applies, the `fix` to write, and a
one-line `why` — which is what lets the rules live in the code.

`RULE_WHY` is not documentation: it is the catalog `validate --explain` prints, and it
is also the invariant that keeps a code from reaching a user with no reason attached
(`finding` looks it up, so an undocumented code fails at the first test that runs it).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationFinding:
    """One reason `validate` refused, addressable by code.

    Frozen and hashable on purpose: findings are de-duplicated by equality, and a
    caller that wants to ask "did the rule about seeds fire?" compares codes instead
    of matching prose that a later rewording would break.
    """

    code: str
    where: str
    message: str
    fix: str
    why: str

    def render(self) -> str:
        """The four lines the CLI prints, and the shape the study's F6 §5.2 asks for."""
        return "\n".join(
            (
                self.code,
                f"  {self.where}: {self.message}",
                f"  fix: {self.fix}",
                f"  why: {self.why}",
            )
        )

    def summary(self) -> str:
        """One line, for a surface that shows the first refusal and nothing more.

        The review screen puts this in a paragraph, so the block would collapse
        there; the fix and the reason stay in `render`, which the CLI prints.
        """
        return f"{self.code}: {self.where}: {self.message}"

    def __str__(self) -> str:
        return self.render()

    def __contains__(self, needle: str) -> bool:
        """A finding is searched as the block it renders.

        `in`, not `==`, is how a test asks about one of these ("does a finding about
        `403` exist?") without pinning the whole message, and it is the same question
        the assertions asked of the sentences this replaced.
        """
        return needle in self.render()


#: Every code `validate` can emit, with the one line that says why it exists. Sorted
#: by the order `--explain` prints: the loading failures first, then the contract,
#: the case, the seed, and the round.
RULE_WHY: dict[str, str] = {
    "ROUND_UNREADABLE": "the round file does not load, so nothing it names can be checked.",
    "SUITE_UNREADABLE": "the suite the round names does not load.",
    "CASE_UNREADABLE": "a case file the round includes does not load.",
    "CONTRACT_UNREADABLE": (
        "a contract a case points at does not load, so its fields and rules are unknown."
    ),
    "TODO_SENTINEL": (
        "`TODO` is what the scaffold writes when it could not derive a value; an"
        " unanswered question is not a passing test."
    ),
    "COVERAGE_GAP": "the contract declares a condition no case exercises.",
    "RULE_NEEDS_IDENTITY": (
        "a rule the contract declares must name the failure it causes, with a `code`"
        " or an `error` message."
    ),
    "422_WITHOUT_RULE": "a 422 that no rule explains is a fabricated expectation.",
    "SEED_PLACEHOLDER": (
        "a filler seed is not identity data: it either reaches the API as a literal or"
        " hides the value the case meant to send."
    ),
    "HARDCODED_ID": (
        "identity data comes from the harness, so that a re-run does not collide with"
        " the previous one."
    ),
    "SECRET_IN_ROUND": "a credential written into content is a credential committed to git.",
    "LIVE_ONLY_IN_SANDBOX": (
        "a live-only rule, or a round from the other environment, cannot be observed in"
        " sandbox — claiming it was is a fabricated result."
    ),
    "TRACK_MIX": (
        "the manifest excludes this track; a round that brings it back makes the"
        " campaign's scope a lie."
    ),
    "PLACEHOLDER_NO_PRODUCER": (
        "a placeholder with no producer reaches the API as literal braces."
    ),
    "RESOURCE_ID_NO_PRODUCER": (
        "the route takes its id in the path; without a producer the case requests a URL"
        " nobody created."
    ),
    "UNIQUE_JSON_MISSING": (
        "a POST whose name must be unique needs a generated value, or the second run"
        " collides with the first."
    ),
    "E_ISOLATE_SANDBOX_STATUS": (
        "sandbox isolation must answer 403; any other status means the boundary was not"
        " tested."
    ),
}


def finding(code: str, where: str, message: str, fix: str) -> ValidationFinding:
    """Build a finding, failing fast on a code nobody documented."""
    return ValidationFinding(
        code=code,
        where=where,
        message=message,
        fix=fix,
        why=RULE_WHY[code],
    )


def explain() -> str:
    """The catalog as the CLI prints it: one line per code, `code  why`."""
    width = max(len(code) for code in RULE_WHY)
    return "\n".join(f"{code.ljust(width)}  {why}" for code, why in RULE_WHY.items())
