#!/usr/bin/env bash
# Gate T3 — the harness proves it is generic, or it does not.
#
# Run from the repository root: `./examples/toy-provider/gate.sh`.
#
# Six claims, in the order they can fail:
#
#   1. a clean wheel installs, with no provider of any name and no browser
#      automation anywhere;
#   2. the harness runs against the toy API using nothing but that wheel;
#   3. the run is navigable (`runs/latest`);
#   4. discovery reads the live OpenAPI document and the tree it writes reviews
#      clean, which is the reader half of "works on any REST API";
#   5. the core suite passes with every provider uninstalled;
#   6. nothing the product owns would be published (`bin/audit-remote`);
#   7. the boilerplate `init` writes runs as it lands, once pointed at a real API.
#
# Claim 5 is the one that catches a provider leaking back into the core: if a
# provider is importable, this passes for the wrong reason. Claim 6 is the other
# half of the same boundary — a core that does not need the product, and a
# repository that does not carry it. Claim 7 is the onboarding path: a template
# that stopped validating would otherwise be discovered by a stranger.
#
# Nothing here names a product. A provider is whatever declares itself one, in
# `heimdall_qa.providers`, so these claims hold on a checkout that has a product
# under `providers/` and on one that has never seen a product at all.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXAMPLE="$REPO/examples/toy-provider"
GATE="${GATE_VENV:-/tmp/heimdall-qa-gate}"
PORT="${GATE_PORT:-8110}"

step() { printf '\n== %s\n' "$1"; }

step "building the core wheel"
cd "$REPO"
rm -rf dist build
"$REPO/.venv/bin/python" -m pip wheel . --no-deps -w dist >/dev/null
WHEEL=$(ls dist/heimdall_qa-*.whl)

step "installing it into a clean venv ($GATE)"
rm -rf "$GATE"
python3 -m venv "$GATE"
"$GATE/bin/pip" -q install "$WHEEL"

step "claim 1 — no provider, no browser automation"
"$GATE/bin/python" -c "import heimdall_qa; print('core:', heimdall_qa.__file__)"
# What is forbidden is *browser automation*, and the check names modules rather than a
# category. Playwright and axe drive a real browser to test a frontend; no part of
# reviewing an API needs one, and a core that pulled one in would have become the
# browser harness of ADR-11 in disguise. A webview is a different thing: the desktop
# client draws this harness's own screen in a native window and automates nothing, so
# `webview` is deliberately absent from this list and must not be added to it.
BROWSER_AUTOMATION=$("$GATE/bin/python" - <<'PY'
import importlib.util

print(", ".join(
    name for name in ("playwright", "axe_playwright_python", "selenium")
    if importlib.util.find_spec(name) is not None
))
PY
)
if [ -n "$BROWSER_AUTOMATION" ]; then
    echo "FAIL: browser automation in the core's environment: $BROWSER_AUTOMATION" >&2
    exit 1
fi
# Asked of the entry point group rather than of a name: a provider is a distribution
# that declares itself one, whoever wrote it. This is the claim, and it holds whether
# or not a product is checked out beside the core.
if ! "$GATE/bin/python" - <<'PY'
import sys
from importlib.metadata import entry_points

providers = list(entry_points(group="heimdall_qa.providers"))
print("providers:", [point.value for point in providers] or "none")
sys.exit(1 if providers else 0)
PY
then
    echo "FAIL: a provider came with the core" >&2
    exit 1
fi

step "starting the toy API on $PORT"
cd "$EXAMPLE"
if curl -sf "http://127.0.0.1:$PORT/toy/health" >/dev/null 2>&1; then
    echo "FAIL: something is already listening on $PORT; a foreign server would prove nothing" >&2
    exit 1
fi
"$GATE/bin/python" -m uvicorn app:app --port "$PORT" --log-level warning &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
    curl -sf "http://127.0.0.1:$PORT/toy/health" >/dev/null && break
    sleep 0.25
done
curl -sf "http://127.0.0.1:$PORT/toy/health" >/dev/null || {
    echo "FAIL: the toy API never came up" >&2
    exit 1
}

step "claim 2 — the example validates and runs"
rm -rf runs
"$GATE/bin/heimdall-qa" validate rounds/smoke.yaml
"$GATE/bin/heimdall-qa" run rounds/smoke.yaml

step "claim 3 — the run is navigable"
test -f runs/latest/summary.json || {
    echo "FAIL: runs/latest/summary.json is missing" >&2
    exit 1
}
"$GATE/bin/heimdall-qa" last-run
"$GATE/bin/python" - <<'PY'
import json
from pathlib import Path

summary = json.loads(Path("runs/latest/summary.json").read_text(encoding="utf-8"))
counts = summary["counts"]
assert counts["fail"] == 0, counts
assert counts["pass"] == 3, counts
assert summary["logs_incomplete"] == 0, summary["logs_incomplete"]
print("summary:", counts, "descriptor:", summary["descriptor"]["origin"])
PY

step "claim 4 — discovery reads a live document and produces a tree that reviews clean"
DISCOVERY="${GATE_DISCOVERY:-/tmp/heimdall-qa-discovery}"
rm -rf "$DISCOVERY"
mkdir -p "$DISCOVERY/qa"
cp "$EXAMPLE/qa/project.yaml" "$DISCOVERY/qa/project.yaml"
# `--source`/`--location` and no product on disk: the seam is the core's, so a
# document a person has not committed to the descriptor yet is still readable.
"$GATE/bin/heimdall-qa" discover --root "$DISCOVERY" \
    --source openapi \
    --location "http://127.0.0.1:$PORT/openapi.json"
"$GATE/bin/heimdall-qa" scaffold-round --root "$DISCOVERY" contracts/get-items.yaml --out rounds
"$GATE/bin/heimdall-qa" validate --root "$DISCOVERY" rounds/get-items.yaml
"$GATE/bin/python" - "$DISCOVERY" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
report = (root / "DISCOVERY.md").read_text(encoding="utf-8")
# The three routes of `app.py`, and the one fact FastAPI's document cannot state:
# a product rule is code, so the body axes stay open on the POST — which takes
# `dict[str, str]`, so the document describes a body and no properties.
assert "gaps: 5 across 1 of 3 endpoints" in report, report
for route in ("GET /toy/health", "GET /toy/items", "POST /toy/items"):
    assert f"## {route}" in report, route
contracts = sorted(path.name for path in (root / "contracts").glob("*.yaml"))
assert contracts == ["get-items.yaml", "health.yaml", "post-items.yaml"], contracts
assert (root / "baselines" / "empty.json").is_file()
print("discovery:", ", ".join(contracts))
PY

step "claim 5 — the core suite passes without any provider installed"
cd "$REPO"

# Which provider to remove is read off the entry points, never off a name written
# here: a checkout that has a product under `providers/` removes it, and a checkout
# that has never seen one removes nothing and still makes the claim.
PROVIDER_DISTS=$("$REPO/.venv/bin/python" - <<'PY'
from importlib.metadata import distributions

print("\n".join(
    dist.metadata["Name"]
    for dist in distributions()
    if any(entry.group == "heimdall_qa.providers" for entry in dist.entry_points)
))
PY
)

for dist in $PROVIDER_DISTS; do
    "$REPO/.venv/bin/pip" -q uninstall -y "$dist" >/dev/null
done

if ! "$REPO/.venv/bin/python" -m pytest -q tests 2>&1 | tail -4; then
    echo "FAIL: the core suite needs a provider installed" >&2
    exit 1
fi

# Put back whatever this checkout keeps under `providers/`, which is where a
# product's own distribution lives when it is here at all.
for provider in "$REPO"/providers/*/; do
    [ -f "$provider/pyproject.toml" ] || continue
    "$REPO/.venv/bin/pip" -q install -e "$provider"
done

step "claim 6 — nothing the product owns would be published"
"$REPO/bin/audit-remote"

step "claim 7 — the boilerplate init writes runs as it lands"
# `init` produces a tree about *a* project, so the two values a person edits are the
# two edited here: the origin, and the path. Everything else is what `init` wrote,
# which is the whole of the claim — a starter tree that cannot run is worse than none.
INIT_DEMO="${GATE_INIT:-/tmp/heimdall-qa-init}"
rm -rf "$INIT_DEMO"
"$GATE/bin/heimdall-qa" init --root "$INIT_DEMO"
"$GATE/bin/python" - "$INIT_DEMO" "$PORT" <<'PY'
import sys
from pathlib import Path

root, port = Path(sys.argv[1]), sys.argv[2]
descriptor = root / "qa" / "project.yaml"
descriptor.write_text(
    descriptor.read_text(encoding="utf-8").replace("127.0.0.1:8080", f"127.0.0.1:{port}"),
    encoding="utf-8",
)
contract = root / "contracts" / "health.yaml"
contract.write_text(
    contract.read_text(encoding="utf-8").replace("GET /health", "GET /toy/health"),
    encoding="utf-8",
)
PY
# `run` writes to the working directory's `runs/`, and here the working directory is
# the starter tree — so the evidence lands beside the round that produced it.
cd "$INIT_DEMO"
"$GATE/bin/heimdall-qa" validate rounds/smoke.yaml
"$GATE/bin/heimdall-qa" run rounds/smoke.yaml
"$GATE/bin/python" - <<'PY'
import json
from pathlib import Path

summary = json.loads(Path("runs/latest/summary.json").read_text(encoding="utf-8"))
counts = summary["counts"]
assert counts["pass"] == 1, counts
assert counts["fail"] == 0, counts
print("starter run:", counts)
PY
cd "$REPO"

printf '\nT3: green\n'
