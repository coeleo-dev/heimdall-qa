"""The partida — what an agent reads before it can start — is a budget, not a taste.

F6 §2 measured that start at 109.778 chars (~27.442 tokens, four chars to a token) and
found 68 % of it inside one document nobody needed whole. F6 §5.4 set the target the
plan accepts: ~2.195 tokens, most of them about the work in hand.

Two of that target's rows belong to this repository and are measured here: the core
skill and `AGENTS.md`. The other three are §5.4's own allowances — a project's
descriptor slice and the contract in hand belong to whatever is being reviewed, and the
harness's validation output is precisely what the agent reads *instead of* the spec.

The total is the gate; a row may trade against another as long as the partida holds.
What may not happen is prose creeping back in unnoticed.
"""

from pathlib import Path

from heimdall_qa.findings import RULE_WHY
from heimdall_qa.findings import explain
from heimdall_qa.mcp.server import onboarding

ROOT = Path(__file__).resolve().parents[1]
CORE_SKILL = ROOT / ".agents" / "skills" / "heimdall-qa" / "SKILL.md"
AGENTS_MD = ROOT / "AGENTS.md"

#: F6 §5.4's heuristic, so the numbers here are comparable with the study's table
#: without pinning a tokenizer version.
CHARS_PER_TOKEN = 4

#: F6 §2's baseline and F6 §5.4's target — the two numbers the plan's acceptance uses.
BASELINE_TOKENS = 27_442
PARTIDA_TOKENS = 2_195

#: The three rows of §5.4 that belong to the project under review, not to the harness.
DESCRIPTOR_TOKENS = 500
CONTRACT_TOKENS = 200
VALIDATE_TOKENS = 200

#: A document this repository owns may not grow past its own row by much, whatever the
#: total says: the two together are 1.293 tokens of §5.4's 2.195.
SKILL_TOKENS = 1_050
AGENTS_TOKENS = 400

#: F6 §5.3 sells `validate --explain` as ~600 tokens against 18.567 of spec. It is the
#: index that replaced the spec, so it must stay an index.
INDEX_TOKENS = 700

#: The onboarding recipe. Asserted here and deliberately *not* added to the partida:
#: it is read when a project is being onboarded and never before, which makes it
#: work-in-hand in the same sense as §5.4's descriptor slice. It is still a budget,
#: because a recipe that grows into a manual is the cost §5.4 exists to remove —
#: arriving through the other door.
ONBOARDING_TOKENS = 300


def _chars(path: Path) -> int:
    return len(path.read_text(encoding="utf-8"))


def _tokens(chars: int) -> float:
    return chars / CHARS_PER_TOKEN


def _rows() -> dict[str, int]:
    """The partida in characters, row by row, as F6 §5.4 lays it out."""
    return {
        "skill": _chars(CORE_SKILL),
        "agents": _chars(AGENTS_MD),
        "descriptor": DESCRIPTOR_TOKENS * CHARS_PER_TOKEN,
        "contract": CONTRACT_TOKENS * CHARS_PER_TOKEN,
        "validate": VALIDATE_TOKENS * CHARS_PER_TOKEN,
    }


def test_the_partida_fits_the_target_the_plan_accepts():
    rows = _rows()
    total = _tokens(sum(rows.values()))
    assert total <= PARTIDA_TOKENS, f"{total:.0f} tokens, from {rows}"


def test_the_two_documents_this_repository_owns_hold_their_rows():
    """The rows that can grow silently are the two the harness writes."""
    assert _tokens(_rows()["skill"]) <= SKILL_TOKENS
    assert _tokens(_rows()["agents"]) <= AGENTS_TOKENS


def test_the_gain_is_the_order_of_magnitude_f6_promised():
    """F6 §5.4: the target is 12,5× smaller than the partida it replaces."""
    assert PARTIDA_TOKENS * 12 <= BASELINE_TOKENS


def test_the_explain_index_stays_an_index():
    """The catalog is read when a code needs explaining, and must not become a manual."""
    catalog = explain()
    assert _tokens(len(catalog)) <= INDEX_TOKENS, catalog
    assert len(catalog.splitlines()) == len(RULE_WHY)


def test_the_onboarding_recipe_stays_a_recipe():
    """The entry point for a new project is the one an agent reads *first*.

    That is what makes it worth a budget of its own: it is the first thing a model
    sees about this harness, and the place a second copy of the documentation would
    most naturally take root. `init`'s templates are the other half of the same
    handout, and `tests/test_onboarding.py` holds them to runnable, not to prose.
    """
    recipe = onboarding()

    assert _tokens(len(recipe)) <= ONBOARDING_TOKENS, recipe
    assert "validate_round" in recipe
    assert "secrets.local.yaml" in recipe
