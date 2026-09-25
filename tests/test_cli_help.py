import ast
import subprocess
import sys
from pathlib import Path

from heimdall_qa.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]


def test_help_prints_usage(capsys):
    parser = build_parser()
    parser.print_help()
    captured = capsys.readouterr()
    text = captured.out.lower()
    assert "usage" in text
    assert "heimdall-qa" in captured.out
    assert "validate" in captured.out
    assert "scaffold-endpoint" in captured.out
    assert "scaffold-round" in captured.out
    assert "campaign" in captured.out
    words = captured.out.lower().split()
    assert "run" in words
    assert "last-run" in captured.out
    assert "serve" in words
    assert "fixture" in captured.out


def test_help_subprocess_exit_zero():
    binary = Path(sys.prefix) / "bin" / "heimdall-qa"
    result = subprocess.run(
        [str(binary), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "heimdall-qa" in result.stdout
    assert "run" in result.stdout.lower().split()
    assert "last-run" in result.stdout
    assert "serve" in result.stdout.lower().split()
    assert "scaffold-round" in result.stdout
    assert "campaign" in result.stdout


def test_validate_example_round_cli_passes():
    binary = Path(sys.prefix) / "bin" / "heimdall-qa"
    round_path = ROOT / "tests" / "fixtures" / "rounds" / "example.yaml"
    root = ROOT / "tests" / "fixtures"
    result = subprocess.run(
        [str(binary), "validate", str(round_path), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""


def test_validate_walk_hn_round_cli_passes():
    binary = Path(sys.prefix) / "bin" / "heimdall-qa"
    round_path = ROOT / "tests" / "fixtures" / "rounds" / "walk-hn.yaml"
    root = ROOT / "tests" / "fixtures"
    result = subprocess.run(
        [str(binary), "validate", str(round_path), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""


def test_validate_h01_only_round_cli_fails():
    binary = Path(sys.prefix) / "bin" / "heimdall-qa"
    round_path = ROOT / "tests" / "fixtures" / "rounds" / "h01-only.yaml"
    root = ROOT / "tests" / "fixtures"
    result = subprocess.run(
        [str(binary), "validate", str(round_path), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "ingest-N-omit-timestamp" in result.stderr


def test_fastapi_is_confined_to_serve():
    """Only the serving layer, and the shipped mock it is meant to review, use it.

    Measured with `ast` and not by searching for the word: this is a rule about
    *imports*, and a docstring that explains which app the desktop shell reuses is not
    a dependency on it. The substring version flagged that prose and would have kept
    flagging every honest sentence about the framework, which is how a real leak would
    eventually get waved through.

    `demo/app.py` is allowed because it is not harness plumbing: it is the fake
    product the bundled sample reviews, and it only ever runs while the demo switch is
    on. The confinement that matters — the core never drives a request through the web
    framework — still holds for everything else under `src/`.
    """
    src = ROOT / "src" / "heimdall_qa"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "fastapi" in pyproject.lower()
    allowed = {"serve", "demo"}
    leaked = [
        str(path.relative_to(src))
        for path in sorted(src.rglob("*.py"))
        if not allowed & set(path.parts) and _imports_fastapi(path)
    ]
    assert leaked == []


def _imports_fastapi(path: Path) -> bool:
    """Whether a module imports `fastapi` or anything under it, at any nesting."""
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "fastapi" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == "fastapi":
                return True
    return False
