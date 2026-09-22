---
name: nokr-qa-round
description: >-
  Creates and analyzes Nokr QA review rounds and the Trilho A HTTP campaign
  (YAML contracts/cases, validate, campaign status, last-run). Use when the user
  says "crie uma rodada", "cubra o Trilho A HTTP", "gere a campanha trilho-a",
  "analise a campanha", "analise o último run", "Nokr QA", "cubra a superfície
  do dashboard", or asks for ingest/metering overview checks (10+7).
---

# Nokr QA — rodadas (Antigravity & Cursor)

Leia [`docs/nokr-qa.md`](docs/nokr-qa.md) (A.14). Não chame `http://127.0.0.1:7878`. Segredos só em `secrets.local.yaml` — nunca JWT nem `nk_test_` no YAML da rodada. Piloto ingest: [`rounds/piloto-ingest.yaml`](rounds/piloto-ingest.yaml). Conferência 10+7: [`rounds/values-10m-7i.yaml`](rounds/values-10m-7i.yaml). Campanha sandbox A0–A4: [`campaigns/trilho-a-http.yaml`](campaigns/trilho-a-http.yaml). Go-Live / P-live: [`campaigns/trilho-a-live.yaml`](campaigns/trilho-a-live.yaml).

## Invocação de Comandos CLI no Antigravity

Sempre use o wrapper do repositório ou o binário do virtualenv:
```bash
./bin/nokr-qa <comando>
# ou
.venv/bin/nokr-qa <comando>
```

Comandos fundamentais:
* `nokr-qa validate <round.yaml>` — valida contratos e cobertura da rodada.
* `nokr-qa campaign validate <campaign.yaml>` — valida a cadeia de producers/placeholders de toda a campanha.
* `nokr-qa campaign status <campaign.yaml>` — verifica o status de execução de cada rodada.
* `nokr-qa run <round.yaml> --mode headless` — executa a rodada em modo autônomo headless (sem browser).
* `nokr-qa last-run` — retorna o caminho do diretório `runs/latest`.
* `nokr-qa fixture <kind>` — gera massa de dados válida (CPF, CNPJ, etc.).

---

## Pedido: crie uma rodada (qualquer rota)

1. Abrir o **record DTO** Java em `NokrAPI` (`@NotBlank`, `@Size`, `@Pattern`, `@DecimalMin`, `@NotNull`, `@NotEmpty`, `@JsonAlias`, compact ctor) e os throws do service/entidade.
2. Escrever `contracts/<path>.yaml` + `baselines/<path>.json` (contrato da sessão, não o exemplo do playbook).
3. Se o request não existe no Bruno, criar o `.bru` **e** o case (constituição).
4. `nokr-qa scaffold-endpoint` ou `nokr-qa scaffold-round` → stubs H/O/B/N/I/E/P (expect mecânico a partir do contract; o que o contract não diz continua TODO).
5. Preencher diffs e `expect.status` / `code` **um eixo por vez**. Não inventar 422 sem `rules[]` — conferir o Java / coluna Esperado da matriz. N-over: semente `example` / `Aa1x` até `max_length+1`, nunca `'x'*n` em password. N-rule de quota: `saturate` ou `burst` + `error:` no contract. Cada `rules[]` precisa de `code` ou `error`. **H01** de um POST com `contract.captures` leva `capture_response` **antes** de qualquer round PUT/PATCH com `{{id}}`. E-isolate sandbox: `expect.status: 403`. Não hardcodar `ext-ta-001` (use `generate: uuid` + `{{external_user_id}}`).
6. Identidade (e-mail, senha, nome, razão social, morada, CPF, CNPJ): `nokr-qa fixture KIND` ou `generate:` (`email`, `password`, `person_name`, `company_name`, `address`, `cpf`, `cnpj`). Login/refresh: `secret.email` / `secret.password` / `secret.refresh_token` reusa o `capture` + `capture_response` do `register-H01` (`runs/shared-captures.json`) ou, se existir, `secrets.local.yaml`. JWT/`api_key` para `/platform` e `/api` saem do `capture_response` (`jwt`, `refresh_token`, `api_key` no register; `raw_key` no `api-keys-post-H01`). **Não inventar** `qa-trilho-a@nokr.dev` nem CNPJ de cabeça. Duplicado no mesmo run: H01 `capture: {register_email: email}` e o `N-rule-*` usa `generate: {email: captured.register_email}`. Register: o harness já espera `register_gap_ms` entre POSTs; 429 “Too many requests” no omit **não** é o case — é o bucket. `N-rule-RATE_LIMIT` leva `burst: 4`. Login lockout (P-GAP-8): 401 continua `"Invalid credentials"` — waive `business.rule`.
7. `./bin/nokr-qa validate` até 0 errors.
8. Se for revisão com operador humano: avisar para abrir a coleção via `nokr-qa serve` (`http://127.0.0.1:7878`). **O agente nunca chama a porta 7878.**
9. Se for execução autônoma rápida solicitada: rodar `./bin/nokr-qa run <round.yaml> --mode headless`.

---

## Pedido: cubra o Trilho A HTTP / gere a campanha trilho-a

Não gerar um único round com ~320 IDs. Kind **D**, A5 (observar dashboard) e A6 (Chrome) ficam de fora **do Trilho A HTTP** — a superfície de browser vive no **Trilho C** (A.19), que é round separado e não se mistura com A0–A4. P-live / Go-Live: manifesto [`campaigns/trilho-a-live.yaml`](campaigns/trilho-a-live.yaml) (não misturar no sandbox). Não misturar [`rounds/values-10m-7i.yaml`](rounds/values-10m-7i.yaml).

1. Ler [`campaigns/trilho-a-http.yaml`](campaigns/trilho-a-http.yaml), a matriz A0–A4 HTTP (`NokrAPI/docs/manual-test-trilho-a-matriz.md`) e `p-gaps.yaml`.
2. Para cada entrada: DTO → contract/baseline (se ainda não existir) → `nokr-qa scaffold-round CONTRACT --out rounds/` → auto-fill mecânico → regras Java que faltam. **Reusar** [`rounds/piloto-ingest.yaml`](rounds/piloto-ingest.yaml); não regenerar os 43 ingest. H01 com `capture_response` **antes** de PUT/PATCH. Ingest `customer_id: '{{external_user_id}}'` — não hardcodar `ext-ta-001`.
3. `./bin/nokr-qa campaign validate campaigns/trilho-a-http.yaml` (e `campaigns/trilho-a-live.yaml` se o pedido incluir Go-Live) até 0 errors.
4. Operador opera a árvore no browser (`nokr-qa serve`), ou agente roda headless se expressamente pedido.
5. No fim: `./bin/nokr-qa campaign status` + cada `runs/*-<id>/` + comentários em `verdict.json` → `analysis-campanha.md`.

---

## Pedido: cubra a superfície do dashboard (A.19, Trilho C)

Ler [A.19](docs/nokr-qa.md) antes de escrever qualquer YAML. É o **Trilho C**: round separado, não entra em A0–A4 nem na campanha Trilho A HTTP.

1. Declarar o passo `ui` na suite (`ui: { id, path }`), com `probes.surfaces[].from: ui` quando a tela for superfície de um `probe`. `from` omitido é `api`.
2. Conferir que a tela alvo tem âncora estável (pré-requisito de `E1` no `nokr-ui-lib`). Sem âncora, o passo `ui` não é gravável — avisar, não inventar seletor.
3. `./bin/nokr-qa validate <round.yaml>` até 0 errors (`from: ui` sem `ui.path`/`ui.read` é erro).
4. Avisar o operador para abrir a coleção: `nokr-qa serve` (`http://127.0.0.1:7878`). **O agente não chama a porta 7878.**

Estado: A.19 está **especificado** (Fases 11–12), ainda **não implementado** — não existe passo `ui` no runner nem pack `ui.*`. Se o pedido vier antes das Fases 11–12, entregar o YAML e dizer o que falta, sem fingir execução.

---

## Pedido: analise o último run

1. `./bin/nokr-qa last-run` (ou `runs/latest`).
2. Ler `summary.json`, `steps/*/verdict.json`, `packs.json`, logs, `evidence.md`, e se existir `book.json` + `probes/*/`.
3. Escrever `analysis.md`: produto (`verdict.cause: product`), `instrument` (eixo sujo / quota), pack fails, O/B faltando no contract, waives sem P-GAP, 400/422 trocados, SKIP de worker, **oráculo vs lido** (se o probe falhou: esperado R$ X / lido R$ Y, superfície, se o Overview esgotou o timeout). `summary.counts.instrument` não é defeito de produto.

---

## Pedido: analise a campanha trilho-a / relatório completo

1. `./bin/nokr-qa campaign status campaigns/trilho-a-http.yaml`.
2. Ler cada `runs/*-<round-id>/` (summary, packs, verdicts, logs). Round sem run = **ainda não reviewado**.
3. Escrever `analysis-campanha.md`: por round, 5xx, pack fails, 400 vs 422, SKIP, P-GAP, o que ainda é TODO / não rodado na UI.

---

## Pedido: 10 metering + 7 ingest e valida overview (A.18)

Usar [`rounds/values-10m-7i.yaml`](rounds/values-10m-7i.yaml): **um** `loop` de metering, **um** `loop` de ingest (poll de status depois de cada um) e **um** `probe` — não 17 arquivos H iguais. Rate card da sessão é input do oráculo (P-GAP-4). Schema em A.18. `./bin/nokr-qa validate rounds/values-10m-7i.yaml` e o operador abre `nokr-qa serve rounds/values-10m-7i.yaml`. Não misturar esta rodada na campanha Trilho A HTTP.
