#!/usr/bin/env bash
#
# The Java parity gate: `examples/spring-fixture/`, read and then talked to.
#
# What it proves, in five claims:
#
#   1. `heimdall-qa-spring` is installed and serves the `spring` seam, so the reader
#      half of an arbitrary Spring project is present;
#   2. discovery reads the fixture's live source tree and writes a tree that reviews
#      clean — contracts, cases and a report that names every axis it could not
#      derive;
#   3. a human's two edits (a happy baseline and four path values) are the whole of
#      the work between that tree and a round that passes;
#   4. no pack that could be measured came back unmeasured, and the log correlation
#      reads a real logback file;
#   5. the tree committed in the repository is the tree discovery writes, plus the
#      one case no family can express — so the fixture cannot drift away from the
#      reader it is supposed to measure.
#
# `tests/test_spring_fixture.py` makes the same claims in pytest, and that is what CI
# runs. This script exists because a person debugging a reader wants to see the run
# directory, not a pytest summary.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$REPO/examples/spring-fixture"
HEIMDALL="${HEIMDALL:-$REPO/.venv/bin/heimdall-qa}"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
PORT="${GATE_PORT:-8121}"
WORK="${GATE_WORK:-/tmp/heimdall-qa-spring}"

step() { printf '\n== %s\n' "$1"; }

cleanup() {
    if [ -n "${APP_PID:-}" ]; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

step "claim 1 — the spring reader is installed"
"$PYTHON" - <<'PY'
from heimdall_qa.contract_source import load_contract_source

reader = load_contract_source("spring")
print("reader:", reader.name)
assert reader.name == "spring", reader.name
PY

step "building the fixture jar"
cd "$FIXTURE"
if [ ! -f target/spring-fixture-0.0.1-SNAPSHOT.jar ]; then
    ./mvnw -q -DskipTests package
fi
JAR="$FIXTURE/target/spring-fixture-0.0.1-SNAPSHOT.jar"
[ -f "$JAR" ] || { echo "FAIL: no jar at $JAR" >&2; exit 1; }

step "starting the fixture on $PORT"
rm -rf "$WORK"
mkdir -p "$WORK/logs" "$WORK/qa" "$WORK/baselines" "$WORK/suites" "$WORK/rounds"
if curl -sf "http://127.0.0.1:$PORT/fixture/health" >/dev/null 2>&1; then
    echo "FAIL: something is already listening on $PORT; a foreign server would prove nothing" >&2
    exit 1
fi
java -jar "$JAR" --server.port="$PORT" --logging.file.name="$WORK/logs/fixture.log" \
    >"$WORK/app.log" 2>&1 &
APP_PID=$!
for _ in $(seq 1 120); do
    sleep 0.25
    if curl -s -o /dev/null -w '%{http_code}' -H 'X-Fixture-Token: fixture-token' \
        "http://127.0.0.1:$PORT/fixture/health" 2>/dev/null | grep -q '^200$'; then
        break
    fi
done
curl -sf -o /dev/null -H 'X-Fixture-Token: fixture-token' \
    "http://127.0.0.1:$PORT/fixture/health" || { echo "FAIL: the fixture never came up" >&2; exit 1; }

step "claim 2 — discovery reads the live source and writes a tree that reviews clean"
cp "$FIXTURE/qa/project.yaml" "$WORK/qa/project.yaml"
# The run root is $WORK, so the descriptor's own `location` (relative to itself)
# would resolve inside it: the live source is passed explicitly instead.
"$HEIMDALL" discover --root "$WORK" --source spring --location "$FIXTURE/src/main/java"
"$PYTHON" - "$WORK" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
report = (root / "DISCOVERY.md").read_text(encoding="utf-8")
assert "gaps: 3 across 3 of 3 endpoints" in report, report
assert "| `idempotency_conflict` | 409 |" in report, report
assert "| `POST /fixture/items` | `baseline` |" in report, report
assert "| `GET /fixture/items/{id}` | `path_values.id` |" in report, report
contracts = sorted(path.name for path in (root / "contracts").glob("*.yaml"))
assert contracts == ["fixture-items-id.yaml", "health.yaml", "items.yaml"], contracts
print("discovery:", ", ".join(contracts))
PY

step "claim 3 — the human's edits are the whole of the work, and the round passes"
# Exactly what the report named, and nothing else: the happy baseline the source had
# no value for, and the path value it had no value for.
cp "$FIXTURE/baselines/items.json" "$WORK/baselines/items.json"
sed -i 's|baseline: baselines/empty.json|baseline: baselines/items.json|' "$WORK/contracts/items.yaml"
"$PYTHON" - "$WORK" "$FIXTURE" <<'PY'
import sys
from pathlib import Path

import yaml

work, fixture = Path(sys.argv[1]), Path(sys.argv[2])
# The path value, in the one file the area holds its cases in.
area = work / "cases" / "fixture-items-id.yaml"
cases = yaml.safe_load(area.read_text(encoding="utf-8"))
for body in cases.values():
    assert body.pop("path_values") == {"id": "TODO"}
    body["path_values"] = {"id": "1"}
area.write_text(yaml.safe_dump(cases, sort_keys=False, allow_unicode=True), encoding="utf-8")

# The one case no family expresses: the same idempotency key with a different body.
committed = fixture / "cases" / "items.yaml"
written = yaml.safe_load((work / "cases" / "items.yaml").read_text(encoding="utf-8"))
hand = yaml.safe_load(committed.read_text(encoding="utf-8"))["items-I-replay-conflict"]
written["items-I-replay-conflict"] = hand
(work / "cases" / "items.yaml").write_text(
    yaml.safe_dump(written, sort_keys=False, allow_unicode=True), encoding="utf-8"
)
PY
cp "$FIXTURE/suites/smoke.yaml" "$WORK/suites/smoke.yaml"
cp "$FIXTURE/rounds/smoke.yaml" "$WORK/rounds/smoke.yaml"
sed -i "s|127.0.0.1:8120|127.0.0.1:$PORT|" "$WORK/qa/project.yaml"
printf 'api_key: fixture-token\n' > "$WORK/secrets.local.yaml"

cd "$WORK"
"$HEIMDALL" validate --root "$WORK" rounds/smoke.yaml
"$HEIMDALL" run --root "$WORK" rounds/smoke.yaml --mode headless

step "claim 4 — nothing measurable came back unmeasured"
"$PYTHON" - "$WORK" <<'PY'
import json
import sys
from pathlib import Path

run = (Path(sys.argv[1]) / "runs" / "latest").resolve()
summary = json.loads((run / "summary.json").read_text())
assert summary["counts"]["fail"] == 0, summary["counts"]
assert summary["counts"]["pass"] == 21, summary["counts"]
assert summary["logs_incomplete"] == 0, summary
required = {"http.baseline", "auth.surface", "observability", "business.rule"}
for step in sorted((run / "steps").iterdir()):
    packs = json.loads((step / "packs.json").read_text())
    by_id = {item["pack_id"]: item["status"] for item in packs["results"]}
    for pack in required:
        assert by_id[pack] != "skipped", (step.name, pack, by_id)
logs = next(run.glob("steps/*items-H01/logs-web.txt")).read_text(encoding="utf-8")
assert "trace_id: [" in logs, "the log correlation read no line"
print("run:", run.name, summary["counts"])
PY

step "claim 5 — the committed tree is the tree discovery writes"
AGAIN="$WORK/again"
rm -rf "$AGAIN"
mkdir -p "$AGAIN/qa"
cp "$WORK/qa/project.yaml" "$AGAIN/qa/project.yaml"
"$HEIMDALL" discover --root "$AGAIN" --source spring --location "$FIXTURE/src/main/java" >/dev/null
"$PYTHON" - "$AGAIN" "$FIXTURE" <<'PY'
import sys
from pathlib import Path

import yaml

work, fixture = Path(sys.argv[1]), Path(sys.argv[2])


def _case_ids(cases: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(cases.glob("*.yaml")):
        found.extend(yaml.safe_load(path.read_text(encoding="utf-8")))
    return found


for name in ("health.yaml", "fixture-items-id.yaml"):
    generated = (work / "contracts" / name).read_text(encoding="utf-8")
    committed = (fixture / "contracts" / name).read_text(encoding="utf-8")
    assert generated == committed, name

# `items.yaml` differs in the one line a human wrote, and in nothing else.
generated_case = yaml.safe_load((work / "contracts" / "items.yaml").read_text(encoding="utf-8"))
committed_case = yaml.safe_load((fixture / "contracts" / "items.yaml").read_text(encoding="utf-8"))
assert generated_case.pop("baseline") == "baselines/empty.json"
assert committed_case.pop("baseline") == "baselines/items.json"
assert generated_case == committed_case

generated = set(_case_ids(work / "cases"))
committed = set(_case_ids(fixture / "cases"))
assert committed - generated == {"items-I-replay-conflict"}, committed - generated
assert generated - committed == set(), generated - committed

# The `path_values` a human filled are the only other difference, and `TODO` is all
# discovery wrote there.
for area in sorted(path.name for path in (work / "cases").glob("*.yaml")):
    fresh = yaml.safe_load((work / "cases" / area).read_text(encoding="utf-8"))
    kept = yaml.safe_load((fixture / "cases" / area).read_text(encoding="utf-8"))
    for case_id, body in fresh.items():
        if "path_values" in body:
            assert body.pop("path_values") == {"id": "TODO"}, case_id
            assert kept[case_id].pop("path_values") == {"id": "1"}, case_id
        assert body == kept[case_id], case_id
print("the fixture matches the reader")
PY

printf '\nspring-fixture gate: green\n'
