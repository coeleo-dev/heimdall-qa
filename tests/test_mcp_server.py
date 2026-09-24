"""The MCP front end: the same operations in a register a model reads.

Two halves, and the split is the point. Most of this calls the tool functions
directly, because they are plain functions that never import the SDK — so the tools
are checked without it, and a project that only ever uses the CLI still tests them.
The handshake at the bottom is the one part that needs `mcp`, and it skips when the
extra is not installed.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from heimdall_qa import __version__
from heimdall_qa.findings import explain
from heimdall_qa.mcp import server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLE_ROUND = "rounds/example.yaml"


def _sdk_installed() -> bool:
    try:
        import mcp  # noqa: F401
    except ImportError:
        return False
    return True


needs_mcp = pytest.mark.skipif(
    not _sdk_installed(), reason="the mcp extra is not installed"
)


def test_validate_round_reports_a_verdict_as_well_as_the_findings():
    """`ok` is there so a model does not have to infer it from an empty list."""
    result = server.validate_round(EXAMPLE_ROUND, root=str(FIXTURES))

    assert result["ok"] is True
    assert result["findings"] == []


def test_a_domain_refusal_comes_back_as_something_a_model_can_act_on():
    """The documented contract: a refusal is data, not a traceback.

    `LAST_RUN_MISSING` is a normal state — nobody has run anything yet — and a model
    that receives an exception for it has been told nothing it can use.
    """
    result = server.last_run(root="/tmp/heimdall-qa-no-such-project")

    assert "error" in result
    assert result["error"]["code"] == "LAST_RUN_MISSING"
    assert result["error"]["hint"]


def test_a_run_file_cannot_leave_the_run():
    """The evidence is a directory, not a filesystem.

    Both shapes are refused, because a model that has read `summary.json` once will
    reach for `../../` next, and the second call must fail the same way as the first.
    """
    for name in ("../../../../etc/passwd", "/etc/passwd"):
        result = server.read_run_file(name, run="latest", root=str(FIXTURES))
        assert result["error"]["code"] == "RUN_FILE_OUTSIDE", name


def test_scaffold_refuses_a_contract_that_is_not_there():
    """A typo is the most common reason to scaffold nothing, and it is not a bug."""
    result = server.scaffold_round("contracts/no-such-contract.yaml", root=str(FIXTURES))

    assert result["error"]["code"] == "CONTRACT_UNREADABLE"
    assert "not found" in result["error"]["message"]


def test_explain_rules_is_the_catalog_the_cli_prints():
    """One catalog, two front ends — stated where it is enforced and copied nowhere."""
    assert server.explain_rules() == explain()
    assert "ROUND_UNREADABLE" in server.explain_rules()


def test_fixture_generates_from_the_projects_own_declaration():
    """The domain comes off the descriptor in force, not off anything in the core."""
    generated = server.fixture("email", root=str(FIXTURES))

    assert generated["email"].endswith("@qa.example.dev")


def test_init_writes_the_starter_tree_through_the_tool(tmp_path: Path):
    """The onboarding prompt names this tool, so the tool has to exist and work.

    A prompt that tells a model to run a shell command is a prompt written for a
    front end the model does not have; this is the difference between the two.
    """
    result = server.init(root=str(tmp_path))

    assert result["root"] == str(tmp_path.resolve())
    assert "wrote qa/project.yaml" in result["files"]


def test_the_tool_list_is_registered_and_reads_as_a_review():
    """The order is the procedure, so it is asserted rather than left to drift.

    `init` leads because a project that has no tree cannot be validated — it is the
    first thing a model does on a project it has never seen, and the last thing it
    needs on one that is already running.
    """
    assert [tool.__name__ for tool in server.TOOLS] == [
        "init",
        "validate_round",
        "explain_rules",
        "discover",
        "scaffold_endpoint",
        "scaffold_round",
        "run_round",
        "last_run",
        "campaign_validate",
        "campaign_status",
        "fixture",
        "read_run_file",
    ]


def test_the_http_transport_refuses_a_public_bind(capsys):
    """The harness reaches a project's API; its own surface is never exposed.

    `assert_local_bind` is the core's, borrowed rather than reimplemented, so the
    server and the review UI cannot disagree about what "local" means.
    """
    code = server.main(["--transport", "streamable-http", "--host", "0.0.0.0"])

    assert code == 2
    assert "error[" in capsys.readouterr().err


def test_the_prompts_carry_the_two_recipes(capsys):
    """`onboarding` and `review_run` are procedures, so they name the tools to call."""
    onboarding = server.onboarding(root="/tmp/a-project")
    review = server.review_run()

    assert "validate_round" in onboarding
    assert "read_run_file" in onboarding
    assert "secrets.local.yaml" in onboarding
    assert "read_run_file" in review
    assert "summary.json" in review


@needs_mcp
def test_the_stdio_handshake_lists_the_tools_and_answers_a_call():
    """A client's first two messages, against a real subprocess.

    Everything above tests the tools; this tests that they are *reachable* — that the
    console script starts, answers `initialize`, and returns a tool result over the
    wire rather than dying on an import.
    """
    asyncio.run(_handshake())


async def _handshake() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "heimdall_qa.mcp.server"],
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "heimdall-qa"
            # The version a client is told is the version it installed. Read from the
            # distribution rather than written down twice, so a release that forgets
            # one of the two places fails here.
            assert initialized.server_info.version == __version__

            listed = await session.list_tools()
            assert len(listed.tools) == len(server.TOOLS)

            prompts = await session.list_prompts()
            assert sorted(prompt.name for prompt in prompts.prompts) == [
                "onboarding",
                "review_run",
            ]

            result = await session.call_tool(
                "validate_round",
                {"round": EXAMPLE_ROUND, "root": str(FIXTURES)},
            )
            assert result.is_error is False
            assert json.loads(result.content[0].text)["ok"] is True
