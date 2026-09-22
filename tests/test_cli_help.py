import subprocess
import sys
from pathlib import Path

from nokr_qa.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]


def test_help_prints_usage(capsys):
    parser = build_parser()
    parser.print_help()
    captured = capsys.readouterr()
    text = captured.out.lower()
    assert "usage" in text
    assert "nokr-qa" in captured.out
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
    binary = Path(sys.prefix) / "bin" / "nokr-qa"
    result = subprocess.run(
        [str(binary), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
    assert "nokr-qa" in result.stdout
    assert "run" in result.stdout.lower().split()
    assert "last-run" in result.stdout
    assert "serve" in result.stdout.lower().split()
    assert "scaffold-round" in result.stdout
    assert "campaign" in result.stdout


def test_validate_example_round_cli_passes():
    binary = Path(sys.prefix) / "bin" / "nokr-qa"
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
    binary = Path(sys.prefix) / "bin" / "nokr-qa"
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
    binary = Path(sys.prefix) / "bin" / "nokr-qa"
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
    src = ROOT / "src" / "nokr_qa"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "fastapi" in pyproject.lower()
    leaked: list[str] = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "fastapi" in text.lower() and "serve" not in path.parts:
            leaked.append(str(path.relative_to(src)))
    assert leaked == []
