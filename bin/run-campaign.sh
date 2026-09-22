#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

CAMPAIGN="${1:-campaigns/trilho-a-http.yaml}"
echo "=========================================================="
echo "Iniciando execução da campanha: $CAMPAIGN"
echo "=========================================================="

ROUNDS=$(grep "round:" "$CAMPAIGN" | awk '{print $NF}')
TOTAL=$(echo "$ROUNDS" | wc -l)
CURRENT=0
FAIL_ROUNDS=0
PASS_ROUNDS=0

for r in $ROUNDS; do
  CURRENT=$((CURRENT + 1))
  echo ""
  echo "[$CURRENT/$TOTAL] >>> Executando: $r"
  if ./bin/heimdall-qa run "$r" --mode headless; then
    echo "[$CURRENT/$TOTAL] ✓ PASS: $r"
    PASS_ROUNDS=$((PASS_ROUNDS + 1))
  else
    echo "[$CURRENT/$TOTAL] ✗ FAIL/WARN: $r (continuando para próxima rodada)"
    FAIL_ROUNDS=$((FAIL_ROUNDS + 1))
  fi
done

echo ""
echo "=========================================================="
echo "Execução de todas as rodadas finalizada!"
echo "Rounds PASS: $PASS_ROUNDS | Rounds com FAIL: $FAIL_ROUNDS | Total: $TOTAL"
echo "=========================================================="

./bin/heimdall-qa campaign status "$CAMPAIGN" > runs/last-campaign-status.json
echo "Status gravado em runs/last-campaign-status.json"
