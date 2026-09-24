#!/usr/bin/env bash
#
# The demo: the fixture up, the round reviewable.
#
# `gate.sh` proves the reader and leaves its evidence in a scratch directory. This
# script is the other half of the same fixture — what a person runs to *show* the
# harness: it starts the application on the port the descriptor declares, points its
# log where the descriptor says the log is, and then either serves the review UI or
# runs the round headless.
#
# The log path is not written down here. It is asked of the same code that reads it
# (`ProjectView.log_sources`), because a script that hardcoded `logs/fixture.log`
# would keep working on the day the declaration drifted away from the application —
# which is exactly how the fixture spent a day reporting `source_missing`.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$REPO/examples/spring-fixture"
HEIMDALL="${HEIMDALL:-$REPO/.venv/bin/heimdall-qa}"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
PORT="${DEMO_PORT:-8120}"
MODE=headless

for argument in "$@"; do
    case "$argument" in
        --headless) MODE=headless ;;
        --serve) MODE=serve ;;
        *) echo "usage: demo.sh [--serve|--headless]" >&2; exit 2 ;;
    esac
done

step() { printf '\n== %s\n' "$1"; }

cleanup() {
    if [ -n "${APP_PID:-}" ]; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

cd "$FIXTURE"

step "the jar"
if [ ! -f target/spring-fixture-0.0.1-SNAPSHOT.jar ]; then
    ./mvnw -q -DskipTests package
fi
JAR="$FIXTURE/target/spring-fixture-0.0.1-SNAPSHOT.jar"

if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    echo "FAIL: something is already listening on $PORT; a foreign server would prove nothing" >&2
    exit 1
fi

step "where the descriptor says the log is"
# Asked, not assumed: the declaration is what the harness will read.
LOG="$("$PYTHON" - <<'PY'
from pathlib import Path

from heimdall_qa.descriptor import load_descriptor
from heimdall_qa.project import ProjectView

descriptor = Path("qa/project.yaml")
project = ProjectView(
    descriptor=load_descriptor(descriptor), descriptor_dir=descriptor.parent
)
print(project.log_sources()[0].path)
PY
)"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"
echo "$LOG"

step "the fixture on $PORT"
java -jar "$JAR" --server.port="$PORT" --logging.file.name="$LOG" \
    >"$FIXTURE/logs/demo-app.log" 2>&1 &
APP_PID=$!
for _ in $(seq 1 120); do
    sleep 0.25
    if curl -s -o /dev/null -w '%{http_code}' -H 'X-Fixture-Token: fixture-token' \
        "http://127.0.0.1:$PORT/fixture/health" 2>/dev/null | grep -q '^200$'; then
        break
    fi
done
curl -sf -o /dev/null -H 'X-Fixture-Token: fixture-token' \
    "http://127.0.0.1:$PORT/fixture/health" \
    || { echo "FAIL: the fixture never came up (see $FIXTURE/logs/demo-app.log)" >&2; exit 1; }
echo "up"

step "the round reviews clean"
"$HEIMDALL" validate rounds/smoke.yaml
echo "0 findings"

if [ "$MODE" = "headless" ]; then
    step "the round, run headless"
    "$HEIMDALL" run rounds/smoke.yaml --mode headless
    "$PYTHON" - "$(readlink -f runs/latest)" <<'PY'
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
counts = summary["counts"]
print(f"\n{run.name}: {counts['pass']} pass, {counts['fail']} fail, "
      f"{counts['skip']} skip, {counts['http_5xx']} 5xx, "
      f"{summary['logs_incomplete']} incomplete log(s)")
PY
    printf '\nthe same round, reviewable:  %s serve rounds/smoke.yaml\n' "$HEIMDALL"
    exit 0
fi

step "the review UI"
printf 'opening %s serve rounds/smoke.yaml — Ctrl+C stops both\n' "$HEIMDALL"
"$HEIMDALL" serve rounds/smoke.yaml
