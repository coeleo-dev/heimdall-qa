# Heimdall QA

Harness de **review** da [NokrAPI](../NokrAPI). Você percorre HTTP no browser; agentes Cursor escrevem YAML e leem a pasta do run. Não é a suíte JUnit e não substitui o Bruno.

**Spec:** [docs/nokr-qa.md](docs/nokr-qa.md). **Agentes:** [AGENTS.md](AGENTS.md) e [`.cursor/skills/heimdall-qa-round/SKILL.md`](.cursor/skills/heimdall-qa-round/SKILL.md).

A UI só existe para o operador. Bind `127.0.0.1` (nunca `0.0.0.0`). Sem OpenAPI (`/docs` 404). O agente **não** chama a porta 7878 — a API dele é `runs/latest`.

---

## Instalação

Na raiz deste repo (`heimdall-qa`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium   # passo `ui` (A.19); sem o binário o passo falha
heimdall-qa --help
```

Python ≥ 3.12. `--verbose` em qualquer comando imprime traceback. O Chromium só é
necessário para rounds com passo `ui`; o resto do harness ignora-o.

---

## Configuração

### `config.yaml` (versionado)

Caminhos da NokrAPI, timeouts e UI. Defaults úteis:

| Chave | Default | Uso |
| --- | --- | --- |
| `nokr_web` | `http://127.0.0.1:8080` | API pública (`/api`, `/platform`, `/auth`, `/webhooks`) |
| `nokr_admin` | `http://127.0.0.1:9090` | `/admin` |
| `nokr_dashboard` | `http://localhost:4200` | Origem do dashboard (passo `ui`). O hostname **tem** de ser `localhost`: o browser resolve a API em `localhost:8080` e o CORS só permite `localhost:4200` |
| `ui.host` / `ui.port` | `127.0.0.1` / `7878` | Browser. `ui.host` **tem** de ser localhost |
| `ui.page_ms` | `15000` | Orçamento de carregamento de uma tela; esgotar **falha** o passo, nunca SKIP |
| `ui.logs_ms` | `1500` | Espera pela linha de `trace_id` do passo nos logs da JVM |
| `ui.screenshot` | `true` | Grava `screenshot.png` por passo |
| `ui.a11y` | `true` | Roda `axe-core` (WCAG A/AA) por passo; `false` faz `ui.a11y` reportar `skipped` com o motivo |
| `log_files.web` / `worker` | `../NokrAPI/logs/...` | Coletor de `trace_id` |
| `probes.*` | 8 s ingest/ledger, 30 s Overview | Poll e conferência A.18 |
| `register_gap_ms` | `2000` | Pausa entre `POST /auth/register` (bucket IP: 3 tokens, refill ~1,7 s). `0` nos testes. |

`--config PATH` sobrepõe. Sem ficheiro, usam-se os defaults do pacote.

Na NokrAPI, profile `web` grava `logs/nokr-web.log` via logback; `worker` / `admin` via `application-{profile}.yml`. Profile `test` não. Working directory do IntelliJ = módulo NokrAPI.

### `secrets.local.yaml` (gitignored)

Copie [secrets.example.yaml](secrets.example.yaml). Nunca cole `nk_test_`, JWT ou UUIDs reais no YAML da rodada.

```yaml
api_key: nk_test_…          # Bearer /api
jwt: eyJ…                   # Bearer /platform
admin_secret: …             # /admin
email: …                    # POST /auth/login (tenant já existente)
password: …
refresh_token: …            # POST /auth/refresh
external_user_id: ext-ta-001
nokr_user_id: …             # UUID do user no tenant
```

`--secrets PATH` sobrepõe. Sem ficheiro, o harness sobe com mapa vazio (fixtures de teste).

---

## CLI

Todos os paths de round são relativos a `--root` (default: diretório atual). Falhas: `error[CODE]:` + `hint:` no stderr.

### `heimdall-qa validate ROUND`

Confere YAML do round, cases, `expect.status` ≠ TODO e **cobertura** do contract (`expand()`), excepto suites com `steps` (loop/probe da conferência de valores).

```bash
heimdall-qa validate rounds/piloto-ingest.yaml
heimdall-qa validate rounds/values-10m-7i.yaml
heimdall-qa validate tests/fixtures/rounds/walk-hn.yaml --root tests/fixtures
```

Exit `0` = zero erros. Exit `1` = lista no stderr (ex.: `coverage: missing ingest-N-omit-timestamp`).

### `heimdall-qa scaffold-endpoint CONTRACT`

Gera stubs H/O/B/N/I/E/P. O que o contract determina é preenchido (`N-omit`/`N-pattern`/`N-over` → 400, `N-auth` → 401, `N-rule-*` → `rules[]`). O resto fica `TODO` (a rodada **não** valida enquanto houver TODO). `--h01-status 202` semente o H01 e clona o 2xx para `B-*` / `I-replay` / `I-new-key`.

```bash
heimdall-qa scaffold-endpoint contracts/api-ingest-post.yaml --out cases/ingest
heimdall-qa scaffold-endpoint tests/fixtures/contracts/api-ingest-post.yaml --out /tmp/stubs
```

`--force` sobrescreve ficheiros existentes. Preencha diffs e status **um eixo por vez**, depois `validate`.

### `heimdall-qa scaffold-round CONTRACT --out rounds/`

`scaffold-endpoint` + YAML do round com `include` = todos os ids de `expand()`.

```bash
heimdall-qa scaffold-round contracts/api-metering-post.yaml --out rounds/ --cases-out cases/metering --h01-status 202
```

Não regenere [`rounds/piloto-ingest.yaml`](rounds/piloto-ingest.yaml).

### `heimdall-qa campaign validate CAMPAIGN`

Corre `validate` em cada round do manifesto. Stderr nomeia o path que falhou (ficheiro em falta ou erro de cobertura).

```bash
heimdall-qa campaign validate campaigns/trilho-a-http.yaml
```

### `heimdall-qa campaign status CAMPAIGN`

Para cada round, se existe `runs/<stamp>-<round-id>/summary.json` (pass / fail / 5xx) ou **ainda não reviewado**. JSON no stdout. Gancho do relatório de campanha.

```bash
heimdall-qa campaign status campaigns/trilho-a-http.yaml
```

### `heimdall-qa run ROUND --mode headless`

Executa a rodada sem browser: HTTP real (ou mock em teste), packs, `runs/<stamp>-<id>/`, symlink `runs/latest`. Imprime o path absoluto do run.

Só `--mode headless` é válido aqui. `walk` / `review` exigem `serve`.

```bash
heimdall-qa run tests/fixtures/rounds/example.yaml --root tests/fixtures --mode headless
heimdall-qa run rounds/piloto-ingest.yaml --mode headless
```

Exit `1` se `counts.fail` ou `http_5xx` no `summary.json`.

### Passo `ui` (browser)

Um passo `ui` abre o Chromium, autentica pelo formulário real e lê uma tela — provando
que o valor que o cliente vê é o que a API respondeu (A.19).

```yaml
steps:
  - ui:
      id: login                       # obrigatório
      path: /auth/login               # obrigatório: rota declarada, igual a core/surfaces.ts
      wait_for: /platform/dashboard/metrics   # resposta que prova que a tela carregou
      region: main                    # região do ARIA snapshot (nunca o body inteiro)
      timeout_ms: 15000               # sobrepõe ui.page_ms
      baseline: baselines/ui/overview.aria.yml   # template ARIA commitado (E3)
      waive:                          # mesmo modelo `Waive` dos cases
        - pack: ui.visual
          reason: "…≥40 caracteres…"
```

Regras que o runner impõe:

- **Auth é automática.** O JWT do dashboard vive só em memória, então o passo dirige
  `secret.email` / `secret.password`. `path: /auth/login` significa "autentique": a tela
  lida é para onde o guard manda a sessão (`/overview`).
- **O `trace_id` é a fronteira do passo.** O passo manda `X-Trace-Id: nokrqa-<run>-<n>` em
  todas as chamadas que faz e só atribui a si mesmo respostas com esse id. Uma tela que
  não dispara chamada própria não herda a resposta de outro passo — ela falha como
  **instrument**.
- **Cada passo tem de carregar a própria tela.** Um segundo passo na mesma rota não
  refaz o fetch, então use telas diferentes (a navegação in-app preserva a sessão; um
  `goto` a perderia).
- **`wait_for` casa por substring** do URL. Sem ele, a prontidão cai na primeira chamada
  `/platform/**` do passo.
- **Nunca `sleep`.** Toda espera é condição com deadline; esgotar `ui.page_ms` falha.
- **Dashboard fora do ar** → `verdict.cause: instrument`, `summary.counts.instrument ≥ 1`
  e o round para. Nunca SKIP.
- `--mode review` / `walk` recusam round com passo `ui` (`UI_STEP_REVIEW_UNSUPPORTED`)
  até a E6.

#### Packs de tela (E3)

| Pack | Falha quando | Fonte |
|---|---|---|
| `ui.render` | Erro no console (ou `pageerror`), ou request observada com 5xx | `console.log`, `network.json` |
| `ui.structure` | Árvore ARIA diverge do `baseline` | `aria.yml` vs baseline |
| `ui.a11y` | Violação WCAG A/AA no estado da tela | `a11y.json` (axe-core) |
| `ui.visual` | Diff de pixel acima da tolerância | fora de escopo no v1 |

Os três primeiros são **produto**, não instrumento: falham o passo e o round segue. Uma
verificação que o harness não conseguiu *rodar* é instrumento e para o round
(`UI_BASELINE_MISSING`, `A11Y_SCRIPT_MISSING`, `A11Y_RUN_FAILED`).

`baseline` é resolvido contra o `--root`. **Sem `baseline` declarado**, `ui.structure`
devolve `skipped` com hint — nunca `pass`. **Com `baseline` declarado e o arquivo
ausente**, é `UI_BASELINE_MISSING` (instrumento), porque senão um typo apagaria o gate.
O campo é validado: não aceita `..` nem path absoluto.

O template é uma **asserção de subconjunto**: falha pelo que declara, não pelo que omite.
Máscara é regex no valor inteiro (`- button /.*/`), nunca embutida num literal. A receita
completa está em `docs/nokr-qa.md` A.19.

`ui.a11y` roda `axe-core` (empacotado no wheel da `axe-playwright-python`, sem rede),
escopado à `region` e limitado às tags WCAG A/AA. Desligar é decisão explícita
(`ui.a11y: false` no `config.yaml`) e o pack reporta `skipped` com o motivo.

`ui.value` é o pack que justifica a seção e **não é waivável**: o waive é recusado com o
motivo (`fail`), não aplicado.

Artefatos por passo, em `steps/NNN-ui-<id>/`: `ui.json`, `aria.yml`, `a11y.json`,
`ui-values.json`, `console.log`, `network.json`, `screenshot.png`, `logs-web.txt`,
`logs-worker.txt`, `packs.json`, `verdict.json`. O tráfego do browser reusa os packs de
transporte sem alterá-los: `http.baseline`, `observability`, `http.success`.

```bash
./bin/heimdall-qa run rounds/ui-overview.yaml --mode headless   # suíte de demonstração
```

### `heimdall-qa last-run`

Imprime o path absoluto de `runs/latest`. `--runs-dir` default `runs`.

```bash
heimdall-qa last-run
```

### `heimdall-qa fixture KIND`

JSON de identidade no stdout (faker `pt_BR` + validate-docbr). Um kind por invocação: `email`, `password`, `person_name`, `company_name`, `address`, `cpf`, `cnpj`, ou `register` (bloco típico). O runner aceita esses kinds em `generate:`, `secret.<nome>` (`secrets.local.yaml`) e o e-mail/senha/`jwt`/`refresh_token`/`api_key` do `register-H01` em `runs/shared-captures.json` (`capture` + `capture_response`). Não inventar e-mail/CNPJ no YAML.

```bash
heimdall-qa fixture register
heimdall-qa fixture cnpj
```

### `heimdall-qa serve [TARGET]`

Sobe a UI da coleção em `http://127.0.0.1:7878`. Recusa bind que não seja localhost. Carrega `config.yaml` + `secrets.local.yaml` da working directory. Sem argumento indexa `campaigns/*.yaml` e rounds órfãos. Opcional: focar uma campanha ou um round.

```bash
heimdall-qa serve
heimdall-qa serve campaigns/trilho-a-http.yaml
heimdall-qa serve rounds/piloto-ingest.yaml
heimdall-qa serve rounds/values-10m-7i.yaml
heimdall-qa serve tests/fixtures/rounds/walk-hn.yaml --root tests/fixtures
```

YAML ilegível (ficheiro partido) **não sobe**: `ROUND_INVALID`. Round parseável mas incompleto (TODO / cobertura) **sobe** na árvore como não pronto e **não inicia**. `Ctrl+C` encerra. Um processo = um round HTTP de review de cada vez; com veredito pendente, iniciar outro é `ROUND_BUSY`.

### Códigos estáveis

`ROUND_INVALID`, `ROUND_BUSY`, `SECRET_MISSING`, `HTTP_UNREACHABLE`, `HTTP_TIMEOUT`, `LAST_RUN_MISSING`, `MODE_REQUIRES_UI`, `VERDICT_INVALID`, `CONFIG_INVALID`, `STEP_INTERNAL`, `FIXTURE_UNKNOWN`, `GENERATE_UNKNOWN`, `CAPTURE_MISSING`, `PLACEHOLDER_UNRESOLVED`.

---

## UI (browser)

Abra o URL que o `serve` imprimiu. `/docs` e `/openapi.json` não existem.

### 1. Iniciar

A esquerda é a **coleção** (campanha → fluxo → endpoint → caso) com status. Clique num endpoint sem run para o formulário à direita: round, ambiente (`sandbox` / `production`), dimensões e quantos passos há na fila. Escolha o modo e **Iniciar** (ou **Reexecutar**, que cria um run novo):

| Modo | Comportamento |
| --- | --- |
| **walk** | Para em **todo** passo (e no probe). |
| **review** | Auto-avança quando packs passam; para se algum pack falhar, se o case for `gate: human`, ou no probe se `values.*` falhar. |

Ao iniciar, o harness dispara o HTTP do primeiro passo (ou o loop inteiro, nas suites A.18) e abre `/round`.

### 2. Duas colunas

- **Esquerda — Coleção.** Campanhas, fluxos (`matrix` ou suite), endpoints e casos. Status em português: Em falta / Não pronto / Não reviewado / Pendente / Passou / Falhou / Pulado / HTTP 5xx. Round com run: clicar um caso reabre o passo **sem** reenviar HTTP. No round em curso, **Anterior** / **Próximo** percorrem só passos já alcançados. Passos `loop` e `probe` são um item cada (`loop metering ×10`, não 17 linhas H).
- **Direita — Detalhe.** Rollup da campanha, Iniciar/Reexecutar, ou review: packs (fail/warn no topo; pass colapsável) → tabela **esperado vs lido** no probe → HTTP e ms → body/headers/logs em `<details>`. Segredos (`nk_test_`, JWT) saem redacted.

O formulário de veredito só aparece no passo que está **à espera** de Aprovar/Reprovar. Nos passos já julgados vê o veredito gravado e o atalho **Ir ao passo atual**.

### 3. Veredito

| Botão | Efeito |
| --- | --- |
| **Aprovar** | Segue; comentário opcional. |
| **Reprovar e seguir** | Exige comentário; vai ao próximo. |
| **Reprovar e parar** | Exige comentário; encerra a rodada. |

Reprovar sem texto = HTTP 400 e a mensagem **Reprovar exige comentário** no próprio form.

### 4. Fim

KPIs (pass/fail/skip/5xx, packs, cobertura, p50/p95, logs incompletos, rejeição humana) e o path `runs/<id>/` num campo só de leitura. O JSON cru fica dentro de `<details>`.

---

## Rodadas prontas

| Round | O que cobre | Quando usar |
| --- | --- | --- |
| [`rounds/piloto-ingest.yaml`](rounds/piloto-ingest.yaml) | 43 kinds de `POST /api/ingest` | Matriz completa daquele path |
| [`rounds/values-10m-7i.yaml`](rounds/values-10m-7i.yaml) | 10 metering + 7 ingest + probe | Conferência de valores (A.18), **não** misturar com o piloto nem com a campanha |
| [`campaigns/trilho-a-http.yaml`](campaigns/trilho-a-http.yaml) | A0–A4 HTTP, um round por path | Pedido **cubra o Trilho A HTTP**; reusa o piloto ingest; exclui D / A5 / A6 |
| [`campaigns/trilho-a-live.yaml`](campaigns/trilho-a-live.yaml) | Go-Live + P-live HTTP | Pedido **P-live / Go-Live**; `nk_live_` em secrets; exclui D / A5 / A6 |

**Superfície de browser (A.19):** especificada nas Fases 11–12 — passo `ui` e packs `ui.*` implementados (E2/E3); a campanha do **Trilho C ainda não existe** (não procure `campaigns/trilho-c-ui.yaml`: só nasce na E5). O Trilho C é round separado e não se mistura com A0–A4.

**Campanha Trilho A:** o agente gera os rounds até `campaign validate` = 0 errors nos manifestos sandbox e (se pedido) live; você abre `heimdall-qa serve` e opera a árvore (um round HTTP de cada vez). Depois **analise a campanha trilho-a** lê `campaign status` + `runs/` e escreve `analysis-campanha.md`. Sem UI de 300 itens na fila. Sem misturar o 10+7.

**Piloto ao vivo:** NokrAPI **web + worker**, catálogo `llm_tokens` SUM `properties.tokens`, user `ext-ta-001`, `rating_engine_dimensional` ligado, `api_key` `nk_test_`, logs em ficheiro.

**Valores 10+7 ao vivo:** o mesmo + **ClickHouse**, `jwt`, `nokr_user_id`, `external_user_id`, rate card FLAT `unit_amount` `0.00003`. Poll de ingest ou Overview no timeout = **falha** (nunca SKIP).

---

## Artefatos de um run

```
runs/<stamp>-<round-id>/
  round.yaml
  summary.json
  evidence.md
  book.json                 # só suites com oracle
  steps/001-<case-id>/      # request, response, packs, logs, verdict
  steps/007-ui-<step-id>/   # ui.json, aria.yml, a11y.json, ui-values.json,
                            # console.log, network.json, screenshot.png (A.19/E3)
  probes/<id>/              # before, after, delta, oracle
runs/latest -> <último run>
```

O agente, depois do review: `heimdall-qa last-run`, lê esses ficheiros, escreve `analysis.md`. Depois de uma campanha: `heimdall-qa campaign status` e `analysis-campanha.md`. Não gera o harness.

---

## Testes

```bash
pytest
HEIMDALL_QA_SLOW=1 pytest     # opcional: tenta localhost:8080; senão skip
```

Pytest **não** exige NokrAPI no ar. O 10+7 live é aceite humano (`heimdall-qa serve rounds/values-10m-7i.yaml`).
