# F1 — Mapa de acoplamento com classificação

> **Frente F1 do estudo Heimdall QA.** Documento de estudo, não spec.
>
> Pergunta desta frente: *cada ocorrência de identidade Nokr é dado do produto, política
> do harness, ou vazamento?*
>
> Baseline de referência: `nokr-qa@616cf6e` (ver [00-baseline.md](00-baseline.md)).

---

## 1. Regra de classificação

Três baldes, e o critério que separa o barato do caro:

| Classe | Definição | Custo de resolver |
|---|---|---|
| **núcleo** | Política do harness que é neutra de domínio. Só o *nome* vaza. | Renomear / extrair constante |
| **descriptor** | Dado do produto que o harness precisa para operar. O núcleo *consome*, não decide. | Mover para o descriptor (F2), com papel neutro |
| **vazamento** | O harness toma uma **decisão de política** inspecionando um identificador do produto. | Vira dado do descriptor ou hook plugável |

O terceiro balde é o único caro. Os outros dois são mecânicos — e é importante não
confundi-los, porque o esforço do desacoplamento está inteiramente no terceiro.

**Teste do vazamento:** se trocar `/api/ingest` por outra rota muda o *comportamento do
harness* sem que ninguém tenha declarado nada, é vazamento. Dado não decide; política decide.

---

## 2. Volume

Padrão varrido (case-insensitive, em `src/`, `docs/`, `tests/`, `config.yaml`, `bin/`):
`nokr|nk_test_|nk_live_|X-Nokr|HALF_EVEN|HALF_UP|/platform/|/api/ingest|metering|rate.?card|billable|entitlement|nokr_selected_environment|bruno`

| Alvo | Ocorrências | Observação |
|---|---|---|
| `src/` | **346** | em 34 arquivos |
| `tests/` | **645** (425 fora de `nokr_qa`) | fixtures, constantes, nomes de log |
| `docs/` | **248** | `nokr-qa.md` 163, emenda 56, estudo 27, README 2 |
| `README.md` raiz | 57 | |
| `config.yaml` | 7 | |
| `AGENTS.md` + `p-gaps.yaml` + `bin/` | 9 | |

**Dos 346 hits em `src/`, 251 são a string `nokr_qa`** — o nome do pacote, em imports,
docstrings e hints. É *uma* renomeação mecânica, não 251 problemas. Os **95 restantes** são
a matéria real desta frente, e estão catalogados abaixo.

---

## 3. Vazamentos (o custo real do desacoplamento)

Ordenados por profundidade — do mais superficial ao mais profundo.

### V1. Cabeçalho de ambiente fixo no núcleo

| Onde | O quê |
|---|---|
| `runner.py:561` | `headers.setdefault("X-Nokr-Environment", environment)` |
| `runner.py:564` | `"X-Nokr-Admin-Secret"` |
| `suite_run.py:603` | `headers = {"X-Nokr-Environment": environment}` |
| `suite_run.py:613` | idem |
| `autofill.py:144` | `{"X-Nokr-Environment": "production"}` |
| `packs/__init__.py:478,483` | leitura dos mesmos headers para o veredito |

O núcleo **fabrica** o header em vez de deixar o projeto declará-lo. Qualquer API que use
`X-Environment`, `X-Stage` ou um campo no path fica de fora.
**→ descriptor:** `environment.header` (nome) + `environment.values` (mapa sandbox/production).

### V2. Roteamento por prefixo decide a auth

| Onde | O quê |
|---|---|
| `suite_run.py:614` | `if "/platform/" in path` ⇒ usa `jwt`; senão `api_key` |
| `packs/__init__.py:477-484` | `/platform/` ⇒ exige JWT + env; `/admin/` ⇒ exige admin secret |
| `browser.py:703` | lista fixa `("/api/", "/platform/", "/auth/", "/admin/", "/webhooks/")` como "path da aplicação" |

O modelo de auth do produto está codificado como *substring de rota*. A constituição da
NokrAPI até documenta esse mapeamento (§36), mas ele é do **produto** — o núcleo não pode
conhecê-lo.
**→ descriptor:** lista de `route_prefixes` com `{prefix, auth}`. O núcleo consulta a lista,
não a constante.

### V3. Orçamento e espera decididos por rota

| Onde | O quê |
|---|---|
| `runner.py:731` | `_require_worker_logs`: `/api/ingest` ou `/api/ledger` ⇒ esperar log do worker |
| `runner.py:743` | `_budget_for`: `/api/ingest`/`/api/metering` ⇒ `hot_path`; `kyc`/`onboarding`/`/auth/` ⇒ `kyc` |

Este é o vazamento **mais caro de todos**, porque é *duas decisões de política* (SLA e
assincronia) tomadas por comparação de string de rota. Num produto sem `/api/ingest`, o
harness silenciosamente usa o budget `default` e não espera log nenhum — e ninguém é avisado.
**→ descriptor:** o projeto declara `budgets` por rota (ou por `kind` de caso) e
`services` com `async: true`. Nada de inferência.

### V4. Semântica de dinheiro mora no core

| Onde | O quê |
|---|---|
| `oracle/money.py:2-3,17-23` | `ROUND_HALF_UP` para metering, `ROUND_HALF_EVEN` para débito, `DEBIT_SCALE` |
| `oracle/book.py:29-155` | `add_metering`, `add_ingest`, exclusão por `replay`, `http_402`, `http_429` |

`REPLAY`, `402` e `429` como motivos de exclusão do livro são **regras de negócio da Nokr**,
não aritmética. O núcleo hoje não sabe distinguir "quantizei errado" de "excluí a linha
certa porque é replay".
**→ provider:** o estudo §6 já prescreve a saída (separar aritmética de domínio). F1 confirma:
`delta_exact`, `delta_positive`, `unchanged` e `matches_schema` ficam no núcleo; `money_debit`
vira dado do descriptor, puro, sobre valores coletados pelo core.

### V5. Matriz de campanha dentro do schema

| Onde | O quê |
|---|---|
| `schema/models.py:140` | `MatrixSection = Literal["A0"..."A4", "LIVE", "B", "B1"..."B6"]` |
| `schema/models.py:146` | `CampaignExclude.kinds` default `["D"]` |
| `schema/models.py:147` | `CampaignExclude.sections` default `["A5", "A6"]` |

O schema do núcleo **conhece as seções do Trilho A** e o default de exclusão que a emenda 11
exige (dashboard e A5/A6 fora do Trilho A). Isso é decisão editorial da campanha Nokr gravada
como default do núcleo.
**→ descriptor:** `matrix` vira `list[str]` validada contra o descriptor do projeto;
o default de `CampaignExclude` passa a ser vazio (explícito, nunca herdado).

### V6. Prefixo de API key inferido do ambiente

| Onde | O quê |
|---|---|
| `packs/__init__.py:473` | `expected = "nk_live_" if environment == "production" else "nk_test_"` |
| `packs/__init__.py:487` | `if "nk_test_" in authorization or "nk_live_" in authorization` |
| `packs/__init__.py:15` | `nk_test_\|nk_live_` no regex de redact |

Formato de credencial do produto no núcleo.
**→ descriptor:** `api_key.prefix_by_environment: {sandbox: "nk_test_", production: "nk_live_"}`,
e o redact usa a lista declarada em vez de literais.

### V7. Rotas do produto pré-registradas na sessão

| Onde | O quê |
|---|---|
| `session_validate.py:30-35` | seis rotas `/platform/*` que a validação de sessão conhece de antemão |

O núcleo tem uma **lista de endpoints da Nokr** para pré-aquecer a sessão de review.
**→ descriptor:** lista de `warmup_routes`.

### V8. Vocabulário de caso com nome de produto

| Onde | O quê |
|---|---|
| `runner.py:60` | `_PATH_GHOST_KINDS = frozenset({"N-notfound", "S-bola"})` |
| `runner.py:611` | `if kind.startswith("H") or kind == "I-new-key"` |
| `coverage.py:31-40` | gerador de casos por eixo (`H01`, `O-*`, `N-*`, `I-*`, `E-*`, `P-*`) |

`S-bola` é vocabulário do Trilho B da Nokr ("bola" = entidade redonda). O resto dos eixos é
política genérica e defensável — mas `S-bola` não é.
**→ renomear** `S-bola` para um eixo neutro (ex.: `S-path-id`); o resto é núcleo, com o
vocabulário documentado como o contrato de eixos do harness.

### V9. Bases do produto como fontes

| Onde | O quê |
|---|---|
| `packs/__init__.py:15` | `at com\.nokr` no regex de redact de stack trace |
| `packs/__init__.py:417` | `if error.startswith("com.nokr")` |

O núcleo conhece o **pacote base Java** da Nokr para decidir se um stack trace "é do produto".
**→ descriptor:** `product_packages: ["com.nokr"]`.

### V10. Coletor de log com forma fixa

| Onde | O quê |
|---|---|
| `logs/collector.py:16-17` | dataclass com exatamente `web_lines` + `worker_lines` |
| `logs/collector.py:41` | `marker = re.compile(r"trace_id: \[" + trace_id + r"\]")` |
| `logs/collector.py:44-46` | lê exatamente dois arquivos |
| `logs/collector.py:53` | fallback por janela de tempo do **relógio local** |

Três amarras: número fixo de fontes, formato de marcador específico do Logback, e um
fallback que pode produzir falso positivo. É a peça mais frágil do harness (ver
[05-logs.md](05-logs.md), frente F5).
**→ núcleo + sources:** N fontes declaradas; marcador como regex declarada; fallback por
timestamp da linha, nunca pelo relógio local.

### V11. Bruno como fonte de request

| Onde | O quê |
|---|---|
| `config.py:49` | `bruno_collection` |
| `suite_run.py:590-597` | `_path_from_bru` — deriva o path do `.bru` |
| `runner.py:306-308` | idem, com `{{base_url}}` |
| `bru_parser.py` | parser de `.bru` |

O núcleo depende de um **formato de arquivo de um produto de terceiro** para descobrir a rota.
`AGENTS.md` do repo reforça isso como regra.
**→ adapter:** `request_source` plugável (OpenAPI, Postman, Insomnia, `.bru`, inline).

---

## 4. Descriptor (dado do produto, destino = descriptor)

Sem decisão embutida — o núcleo precisa do valor e pronto.

| Onde | Dado | Chave neutra proposta |
|---|---|---|
| `config.py:50` | `nokr_web: http://127.0.0.1:8080` | `targets.api` |
| `config.py:51` | `nokr_admin: http://127.0.0.1:9090` | `targets.admin` |
| `config.py:52` | `nokr_dashboard: http://localhost:4200` | `targets.dashboard` |
| `config.py:37-39` | `../NokrAPI/logs/nokr-{web,worker,admin}.log` | `log_sources[].path` |
| `config.py:14-16` | `budgets_ms.hot_path / default / kyc` | `budgets` (nomes neutros: `fast`/`default`/`long`) |
| `config.py:44-46` | `probes.ingest_poll_ms / ledger_ms / overview_ms` | `probes.<nome do fluxo>` |
| `runner.py:457-465` | `_SENTINEL_FIELD_KINDS`: `cpf`, `cnpj`, `document_number` | `fixtures.fields` |
| `fixtures.py:17` | `Faker("pt_BR")` | `fixtures.locale` |
| `fixtures.py:10,35-37` | kinds `cpf`/`cnpj` + `validate-docbr` | `fixtures.generators` |
| `browser.py:255` | `localStorage['nokr_selected_environment']` | `environment.storage_key` |
| `browser.py:677` | `/platform/` como "resposta primária da tela" | `dashboard.primary_response_prefixes` |
| `runner.py:173`, `ui_step.py:83` | prefixo de `trace_id` `nokrqa-` | `trace.prefix` |
| `fixtures.py:26` | domínio de e-mail `@qa.nokr.dev` | `fixtures.email_domain` |
| `http_client.py:38,44`, `session.py:481` | hints citando "NokrAPI profile web/admin" | texto do descriptor (`services[].name`) |

---

## 5. Núcleo (renomear, não mover)

| Item | Onde | Ação |
|---|---|---|
| Pacote `nokr_qa` (251 hits) | todo `src/` | renomear para o pacote do harness (`heimdall_qa`) |
| CLI `prog="nokr-qa"` | `cli.py:33-34` | renomear; manter `nokr-qa` como alias deprecado |
| Hints `run: nokr-qa ...` | `runner.py:890,972,989,1037`; `cli.py:269,336,347`; `workspace.py:218` | renomear com o binário |
| Marca na UI | `serve/templates/base.html:6,21,98`; `workspace.html:2`; `serve/app.py:41` | renomear |
| `localStorage['nokr-qa-queue-width']` | `serve/templates/base.html:11,98` | renomear |
| `bin/nokr-qa`, `bin/run-campaign.sh` | `bin/` | renomear |
| `pyproject.toml` `name`/`description` | `pyproject.toml` | renomear |
| Docstrings "Nokr QA — review harness for the NokrAPI HTTP surface" | `__init__.py:1`, `logs/__init__.py:1` | renomear e neutralizar |
| `NOKR_QA_BOOT__` | env var de bootstrap | renomear |
| `_SECRET_CAPTURE_KEYS`, `_SENTINEL_FIELD_KINDS` (chaves) | `runner.py:450-465` | genérico (auth schemes) — manter, só o *valor* de produto sai |

---

## 6. Conteúdo declarativo (o ativo)

Nada aqui é "lixo a limpar" — é a **prova de não-quebra** e por isso vai para o provider.

| Diretório | Arquivos | Linhas | Classificação |
|---|---|---|---|
| `cases/` | 539 | 6.713 | provider Nokr (100% produto) |
| `contracts/` | 48 | 1.425 | provider Nokr |
| `rounds/` | 71 | 1.351 | provider Nokr |
| `campaigns/` | 3 | 401 | provider Nokr |
| `suites/` | 10 | 268 | provider Nokr |
| `baselines/` | 34 | 300 | provider Nokr |

**46 endpoints distintos** e **37 DTOs distintos** (6 pacotes de `com.nokr.domain.*`). Os DTOs
são a fonte do contract hoje — ver [03-auto-discovery.md](03-auto-discovery.md) para a ADR que
muda isso.

Os 425 hits de coupling fora de `nokr_qa` em `tests/` são majoritariamente **fixtures que
espelham o produto** (`/platform/dashboard/metrics`, `nokr-web.log`, `nk_test_x`, DTOs). Eles
migram junto com o provider; os testes do *núcleo* é que precisam perder isso.

---

## 7. Docs e skill

| Alvo | Hits | Ação |
|---|---|---|
| `docs/nokr-qa.md` | 163 | fica no provider; a spec do núcleo nasce separada |
| `docs/emenda-11-ui-browser.md` | 56 | fica no provider (é instanciação Nokr) |
| `docs/estudo-harness-agnostico.md` | 27 | é o desenho genérico — vira doc do núcleo |
| `README.md` raiz | 57 | reescrever: genérico + provider como exemplo externo |

O `docs/estudo-harness-agnostico.md` já está do lado certo da fronteira. Ele é o único
documento do repo que fala do harness sem falar do produto.

---

## 8. Ordem de desacoplamento

O critério é *valor por risco*: o que destrava o "funciona em qualquer API REST" primeiro,
com menor chance de quebrar os 539 cases.

| # | Item | Por que nesta posição | Bloqueia |
|---|---|---|---|
| 1 | **V1 + V2** (headers e auth por prefixo) | sem isto, nenhuma API que não seja a Nokr autentica | F2 |
| 2 | **V3** (budget/async por rota) | sem isto, o harness mente sobre SLA em qualquer API | F2 |
| 3 | **V10** (fontes de log) | remove o falso positivo por relógio; é o mais frágil | F5 |
| 4 | **V9 + V7 + V6** (pacotes, warmup, prefixo de key) | curiosidades do produto, isoladas | — |
| 5 | **V5** (matriz no schema) | muda o contrato do schema; fazer depois de estabilizar | F8 |
| 6 | **V11** (`request_source`) | adapter novo, não muda o caminho quente | F3 |
| 7 | **V4** (oráculo de dinheiro) | o mais profundo e o mais arriscado; exige o desenho de §6 do estudo | F8 |
| 8 | **V8** (`S-bola`) | cosmético | — |
| 9 | **Núcleo** (§5) | renomear **por último**, quando o comportamento já estiver provado | F8 |

**Por que o rename é por último.** Enquanto o pacote se chamar `nokr_qa`, todo `git diff`
das fases 1–2 é legível e o provider Nokr continua importando o que já importava. Trocar o
nome antes de trocar a arquitetura mistura duas variáveis num diff só — e se algo quebrar,
não se sabe qual das duas foi.

---

## 9. O que esta frente prova

1. **O custo não está no rename.** 251 dos 346 hits são a string do pacote. Renomear é um
   `sed`; o que custa são os **11 vazamentos**, e três deles (V1, V2, V3) bloqueiam o
   requisito principal — "testar qualquer API REST".
2. **Existe um caminho incremental.** A ordem de §8 ataca primeiro o que destrava a fase 1
   (V1, V2, V3) e deixa o que é profundo (V4, V5) e o cosmético (rename) para o fim.
3. **O maior vazamento é o mais barato de consertar.** V3 é ~15 linhas de inferência
   (`_require_worker_logs` + `_budget_for`) trocadas por leitura de descriptor. Fica
   desproporcionalmente à frente no ranking de valor.
4. **O ativo está seguro.** Os 539 cases / 48 contracts não são reescritos: são *movidos*
   para o provider, e é o permanecerem verdes que prova que o núcleo novo está correto.
