# Análise de exemplo — piloto ingest

Copiar para `runs/latest/analysis.md` depois do review humano. O harness **não** gera este ficheiro.

Round: `piloto-ingest` (`POST /api/ingest`, 43 kinds do contract).

## Produto

- Meta: zero HTTP 5xx. Qualquer 500 é falha dura.
- `ingest-N-auth` (401): `ApiKeyAuthenticationFilter.sendUnauthorized` devolve `{"error":"..."}` sem `traceId` nem `X-Trace-Id`. Packs `http.baseline` e `http.error` falham; `gate: human`. Não é waive — é achado de envelope.
- `ingest-B-max-event_type`: 422 `UNKNOWN_EVENT_TYPE` (slug de 64 caracteres válido no `@Pattern` mas fora do catálogo da sessão). Não é 202 no max do `@Size`.
- `ingest-B-max-customer_id`: 404 (id de 128 caracteres não é `ext-ta-001`).
- N-omit / N-pattern / N-over / denylist: 400 (Bean Validation ou `ResponseStatusException` BAD_REQUEST). Conferir com a matriz §11.4.
- N-rule `UNKNOWN_EVENT_TYPE` / `MISSING_PROPERTY`: 422 + `code`. `UNKNOWN_CUSTOMER`: 404.

## Packs

- Listar `pack fails` não waivados. Waives permitidos: `mutation` em `I-missing` e `I-format` (header omitido ou `abc` de propósito).
- 400 vs 422 sem 500: warn + comentário humano; não é fail duro.

## Worker

- SKIP / `observability` skipped / `logs_incomplete` só se o worker estiver parado, com nota.
- Se o worker está no ar e o H01 202 não produz linha de worker: investigar, não tratar como SKIP de produto.

## Cobertura

- 100% dos kinds de `expand(contracts/api-ingest-post.yaml)`. Nested properties e preview (P-GAP-7) não entram neste round.
- O/B de catálogo (B-max-event_type, B-max-customer_id) documentados acima — não faltam no contract; o HTTP não pode ser 2xx sem métrica/user correspondentes.

## Oráculo

- Fora de âmbito (Fase 10). Não interpretar saldo nesta rodada.
