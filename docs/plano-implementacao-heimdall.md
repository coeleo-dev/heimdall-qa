# Heimdall QA — plano de implementação

> **O que é isto.** A Parte B da refatoração `nokr-qa` → `heimdall-qa`: a sequência de PRs que executa o que o estudo de nove frentes decidiu.
> **Onde nasceu.** [`docs/estudo-heimdall/09-consolidacao.md`](estudo-heimdall/09-consolidacao.md) §2 (as fases), §1 (as 11 ADRs) e §4 (os 48 edge cases). O estudo é o *porquê*; este documento é o *o quê*, na ordem.
> **Como usar.** Um PR, uma fase. A fase N+1 não começa sem o aceite da fase N. O aceite da fase anterior vai no corpo do PR seguinte.
> **Registro em 22/09/2026.** Nada aqui está implementado.

Ordem: **REST primeiro, navegador por último.** A Etapa 3 não entra no caminho crítico das Etapas 1 e 2.

---

## Convenção de caminhos

Os caminhos são os de **hoje**, antes de qualquer fase. Duas transformações se aplicam ao longo do plano:

- depois de **1.3/1.4**, tudo que é conteúdo ou domínio passa a `providers/nokr/` (hoje está na raiz: `cases/`, `contracts/`, `campaigns/`, `rounds/`, `suites/`, `baselines/`, `p-gaps.yaml`, `oracle/`);
- depois de **1.5**, `src/nokr_qa/` → `src/heimdall_qa/` em todos os caminhos.

Um caminho ou superfície marcado **(novo)** ainda não existe — nem o arquivo, nem a flag. Em particular, `validate --explain` (Fase 2.4) e `serve --export` (Fase 3.2) são **criados por essas fases**; hoje `validate` só aceita `round` e `--root`, e `serve` não tem subcomando de export.

---

## Invariantes

Valem em toda fase. Uma PR que viole qualquer uma é recusada, independentemente de estar verde.

| # | Invariante | Como se verifica |
|---|---|---|
| I1 | **O núcleo não importa provider, em nenhuma fase.** | T3 (§ gate de 1.4) |
| I2 | **Nada de navegador nas Etapas 1 e 2** — nem "só um pouquinho". | `pyproject.toml` sem playwright; nenhum caso com passo `ui` |
| I3 | **O E3 não avança nem é revertido** durante as Etapas 1 e 2. Congelado não é abandonado. | `git log` de `baselines/ui/` e dos packs `ui.*` |
| I4 | **O Trilho A não muda.** | `git diff campaigns/` vazio |
| I5 | **Não reescrever.** Aditivo e reversível, com prova de não-quebra por fase. | o aceite de cada fase |
| I6 | **Não generalizar antes do segundo provider existir.** | o provider de brinquedo da fase 1.1 |
| I7 | **A fonte de verdade do conteúdo não vira binário.** | sem índice commitado em `cases/` |
| I8 | **Não deixar o E3 pela metade sem registro.** | [`00-baseline.md`](estudo-heimdall/00-baseline.md) §4 |

**I4 é a que mais tenta ser quebrada por acidente**, porque 1.2 e 1.3 tocam exatamente os YAML que ela protege. O `git diff` vazio é o único juiz.

---

# Etapa 1 — Núcleo agnóstico HTTP

**Entrega:** setup por projeto (F2), mapa de acoplamento aplicado (F1), corte do provider (F8) — só com o adapter HTTP.
**Gate de saída:** `git diff campaigns/` vazio **e** o provider de brinquedo rodando numa wheel limpa sem Nokr em disco.

---

## Fase 1.0 — Corte de dependências

**Objetivo:** instalar o núcleo para testar HTTP deixa de custar 1,3 GB. `playwright` e `axe` saem para o extra `[browser]`, que a Etapa 3 reativa.

**Arquivos:** `pyproject.toml` (só ele), `tests/test_no_browser_extra.py` (novo — o gate vira teste).

**Não fazer:** tocar em `src/` (o código **já** é lazy — verificado: `browser.py:229`, `:597`, `:709`, todos em `try/ImportError`); mover `faker`; mover `validate-docbr` (isso é 1.3); mexer em `tests/`.

**Aceite:** `pip install -e .` num venv limpo **não** instala `playwright`; a suíte de HTTP passa com os dois módulos inexistentes no ambiente.

**Verificar:**
```
# 1. os dois pacotes não estão mais nas deps obrigatórias
python -c "import tomllib;d=tomllib.load(open('pyproject.toml','rb'));\
print([x for x in d['project']['dependencies'] if 'playwright' in x or 'axe' in x])"   # => []

# 2. a suíte HTTP passa sem eles (bloqueio em sys.meta_path)
PYTHONPATH=/tmp:src python -m pytest -q --ignore=tests/test_ui_*.py
```
Script de bloqueio (`/tmp/block_plugin.py`), que vira `tests/test_no_browser_extra.py`:
```python
import sys
BLOCKED = ("playwright", "axe_playwright_python")
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"BLOCKED for [browser]-extra gate: {name}")
        return None
sys.meta_path.insert(0, Block())
```

**Baseline medido hoje (pré-mudança, com o browser instalado):** `257 passed, 3 skipped in 6.44s`.
**Medido com o browser bloqueado:** `257 passed, 3 skipped` — idêntico. É a rara fase cujo aceite **já passa antes** da mudança.

**Gate:** é a base de onboarding e o que torna T3 (1.4) executável. Sem ela, "o núcleo não precisa do browser" é afirmação, não fato.

**Reversão:** `git revert` — uma linha.

---

## Fase 1.1 — Descriptor de projeto

**Objetivo:** URL, auth, rotas, budgets, trace e fontes de log saem do núcleo e passam a ser um arquivo **do alvo**, versionado no repo do alvo (ADR-01).

**Arquivos:** `src/nokr_qa/config.py` (vira o carregador), `src/nokr_qa/schema/` (modelo Pydantic do descriptor), `config.yaml` → `qa/project.yaml`, `tests/fixtures/descriptors/` (3 exemplos), `tests/test_descriptor.py`.

**Não fazer:** implementar o adapter HTTP (é 1.2); mover casos ou contratos (é 1.3); resolver a precedência por `importlib` — dois níveis, produto vence, nada mais.

**Aceite:** o descriptor do **provider de brinquedo** (uma API que não é a Nokr) resolve base_url, auth e rotas; `validate` recusa um descriptor sem `base_url` e recusa dois descriptors que declarariam a mesma rota com auth diferente.

**Verificar:** os 3 exemplos preenchidos de F2 §4 (Nokr, o Node/Express do F2, e o brinquedo) carregam; um teste por campo obrigatório ausente.

**Gate:** sem descriptor, 1.2 não tem o que consumir. É o pré-requisito lógico da Etapa 1 inteira.

**Risco:** `log_files.web` tem default `../NokrAPI/logs/nokr-web.log` — um caminho **fora do repo** (`config.py:49-54`). Default de produto morre aqui.

---

## Fase 1.2 — Adapter HTTP sobre o descriptor

**Objetivo:** os vazamentos V1, V2, V3, V6, V7, V9, V10 e V11 de F1 §2.1 deixam de existir em código e passam a ser dado no descriptor.

**Arquivos:** um por vazamento. A tabela é a lista de arquivos e o que sai de cada um.

| Arquivo | O que sai | Item |
|---|---|---|
| `packs/__init__.py:15` | regex com `com\.nokr`, `nk_test_`, `nk_live_` | V6, V9 |
| `packs/__init__.py:478-483` | `X-Nokr-Environment`, exigência de `/platform` + JWT | V1, V2 |
| `session_validate.py:30-35` | as seis rotas `/platform/*` | V2 |
| `logs/collector.py` | os dois arquivos fixos (`web_log`, `worker_log`) | V10 |
| `bru_parser.py`, `collection.py` | Bruno como fonte única | V11 |
| `runner.py` | resolução de rota e budget a partir de prefixo | V3, V7 |
| `http_client.py` | budgets por constante | V3 |

**Não fazer:** **tocar no oráculo** (`oracle/money.py`, `oracle/book.py`, `packs/values.py`) — V4 é da Etapa 2, quando `checks` plugáveis existirem; nada de browser; mudar o comportamento observável de nenhum case.

**Aceite:** nenhum literal de produto nos arquivos acima; **os 539 cases rodam pelo caminho novo com resultado idêntico**; `validate` recusa uma rota sem `auth` declarado.

**Verificar:**
```
rg -i 'com\.nokr|nk_test_|X-Nokr-Environment|/platform|nokr-web|nokr-worker' \
   src/nokr_qa/runner.py src/nokr_qa/packs/__init__.py \
   src/nokr_qa/session_validate.py src/nokr_qa/logs/collector.py \
   src/nokr_qa/bru_parser.py src/nokr_qa/collection.py        # => vazio
python -m pytest -q --ignore=tests/test_ui_*.py
```

**Gate:** é o corte de verdade. Depois dele, o único acoplamento de produto que resta em `src/` é o que 1.3 e 1.4 removem.

**Risco:** V11 não é descriptor, é **adapter** (F8 §2.1) — a capacidade de ler requests fica no núcleo, a escolha da fonte no descriptor. Não confundir os dois lados.

---

## Fase 1.3 — Migrar o provider Nokr

**Objetivo:** o conteúdo e o domínio viram provider. `cases/`, `contracts/`, `campaigns/`, `rounds/`, `suites/`, `baselines/` e `p-gaps.yaml` saem do núcleo.

**Arquivos:** `qa/project.yaml` (o ex-Nokr), `providers/nokr/` (novo destino), `oracle/money.py` + `oracle/book.py` (→ provider), `validate-docbr` (→ deps do provider, ADR-11), `tests/test_fixtures.py` (migra junto).

**Não fazer:** mexer em `browser.py` ou nos packs `ui.*` — são Etapa 3 (invariante I2/I3); alterar qualquer YAML do Trilho A (invariante I4).

**Aceite:** os 539 cases rodam verdes **e** `git diff campaigns/` está vazio. Nenhum arquivo de conteúdo fica no diretório do núcleo.

**Verificar:**
```
git diff --stat campaigns/            # => vazio
git status --stat providers/nokr/     # => o conteúdo está lá
python -m pytest -q --ignore=tests/test_ui_*.py
```

**Gate:** é a metade "o Nokr roda sobre o núcleo" do gate de saída da Etapa 1 — a outra metade é 1.4.

---

## Fase 1.4 — Partir o pacote

**Objetivo:** `providers/nokr/` deixa de estar sob o `src/` do núcleo e vira **distribuição própria** (`heimdall-qa-nokr`), ADR-05.

**Arquivos:** `pyproject.toml` (núcleo), `providers/nokr/pyproject.toml` (novo), `providers/nokr/src/heimdall_qa_nokr/`

**Não fazer:** manter o provider dentro do `src/` do núcleo — sem a separação física, T3 não prova nada e o ADR-05 fica decorativo.

**Aceite:** T3, o gate de genericidade do ADR-06 — três blocos, todos verdes.
```
# 1. wheel limpa, sem produto em disco, sem browser
python -m venv /tmp/gate && /tmp/gate/bin/pip install dist/heimdall_qa-*.whl
test ! -d ../NokrAPI                                          # nada de produto
/tmp/gate/bin/python -c "import playwright" && exit 1          # browser NÃO instalado

# 2. o exemplo roda
/tmp/gate/bin/heimdall-qa validate
/tmp/gate/bin/heimdall-qa run examples/toy-provider/suites/smoke.yaml
test -f examples/toy-provider/runs/latest/run.json

# 3. o run é navegável sem o produto
/tmp/gate/bin/heimdall-qa last-run
```

**Verificar:** os três blocos acima. O passo 1 falhando = o núcleo depende do provider; o passo 2 falhando = o núcleo não é utilizável sozinho.

**Status:** **vermelho por construção hoje** — `config.py:50-51` ainda tem `nokr_web`/`nokr_admin`, e o descriptor de 1.1 não existe. É o gate que mede o trabalho da Etapa 1.

**Gate:** é o que dá dentes ao ADR-05 e fecha o gate de saída da Etapa 1.

**Requisito:** precisa do **provider de brinquedo** (`examples/toy-provider/`: descriptor de 12 linhas + mock FastAPI de 3 rotas + 3 casos). Ele serve três papéis ao mesmo tempo: exemplo canônico, provider mínimo do teste de contrato, e alvo de T3.

---

## Fase 1.5 — Rename por último

**Objetivo:** `nokr_qa` → `heimdall_qa`, em commit isolado e puramente mecânico.

**Arquivos:** `src/nokr_qa/` → `src/heimdall_qa/` (25 arquivos), `bin/nokr-qa`, `pyproject.toml` (`name`, `[project.scripts]`, `package-data`), `tests/`, `src/nokr_qa/serve/templates/base.html` (chave de `localStorage` `nokr-qa-queue-width` — identidade **do harness**, não do produto).

**Não fazer:** misturar **qualquer** mudança semântica neste commit. O objetivo é que um erro de digitação apareça no diff.

**Aceite:** suíte verde sem mudança de comportamento; `git diff --color-moved` legível; nenhum `import nokr_qa` remanescente.

**Verificar:**
```
rg -n 'nokr_qa|nokr-qa|NOKR_QA' src/ tests/ pyproject.toml bin/    # => vazio
python -m pytest -q --ignore=tests/test_ui_*.py
```

**Gate:** o rename vem **depois** de 1.3/1.4 porque toca 1.432 ocorrências (F8 §0) — antes, obrigaria a revisar o mesmo diff duas vezes sobre arquivos que o provider também reescreve.

---

## Fase 1.6 — Documentação do núcleo em inglês

**Objetivo:** o núcleo passa a ter documentação em inglês (ADR-08). ~500-600 linhas de prosa **nova**, não tradução de 1.271.

**Arquivos:** `README.md`, `docs/README.md`, `AGENTS.md`, docstrings/comentários em `src/` (158 linhas), `.cursor/skills/` (fonte única + geração).

**Não fazer:** traduzir `docs/nokr-qa.md` (1.271 linhas) — é a spec **do produto** e fica em português, no provider; traduzir a pasta `docs/estudo-heimdall/` (insumo descartável); traduzir 152 linhas de skill **antes** de consolidar as duas cópias divergentes (F8 §4.3).

**Aceite:** README + `docs/architecture.md` legíveis sem PT-BR; skill do núcleo em inglês, skill do provider em português; nenhuma regra duplicada entre as duas.

**Verificar:** leitura por quem não fala português (o teste é humano, e é o único de toda a Etapa 1); `rg -l '[áéíóúãõç]' src/` para os docstrings.

**Gate:** fecha a Etapa 1. É o único aceite não automatizável da etapa — e por isso o último.

---

# Etapa 2 — As alavancas

**Entrega:** auto-discovery (F3), storage e índice (F4), fontes de log (F5), contrato do agente (F6).
**Gate de saída:** round gerado de OpenAPI roda sem o agente escrever YAML mecânico; busca em run é sub-segundo; **nenhum fallback por relógio local** existe.

---

## Fase 2.1 — Auto-discovery

**Objetivo:** o agente deixa de escrever YAML mecânico. `dto` deixa de ser obrigatório (ADR-02) e a fonte do contract passa a ser o schema.

**Arquivos:** `src/nokr_qa/schema/` (campo `dto` opcional — hoje `str` obrigatório e decorativo, o que torna o formato impossível fora do Java), `scaffold.py` (o gerador), `session_validate.py` (trata `generate:`), `tests/fixtures/openapi/`.

**Não fazer:** gerar exaustivamente — o teto é `rotas × eixos_deriváveis`; inventar caso a partir de schema (o não-derivável sai como `TODO` explícito e o `validate` recusa).

**Aceite:** um round gerado de `docs/estudo-heimdall/prototipos/openapi-ingest-3.1.yaml` roda **sem o agente escrever YAML mecânico**; todo campo não-derivável sai como `TODO` e o `validate` os aponta.

**Verificar:** gerar, rodar, e conferir que o diff entre o gerado e o contract escrito à mão é só o não-derivável. **Número a bater: 48,8% derivável** (263 de 539 casos, F3 §4).

**Gate:** **o alvo numérico deste gate não está fixado** (aberto #3 de F9 §6.1). A medição de 48,8% é de uma rota no protótipo, não do corpus — fixar o número antes de fechar a fase.

---

## Fase 2.2 — Storage e consolidação de casos

**Objetivo:** a granularidade de arquivo deixa de ser 539 arquivos para 48 contracts.

**Arquivos:** `cases/` (consolidação), `coverage.py`, `validate.py`

**Não fazer:** **não ordenar os casos por ID dentro do arquivo consolidado** — a ordem do disco é preservada, senão todo arquivo vira diff churn a cada edição (F4); não commitar índice binário (invariante I7).

**Aceite:** 539 arquivos → **47**, com custo de 1,12× em bytes; a busca continua sub-segundo **sem índice**; editar um caso produz diff de poucas linhas.

**Verificar:**
```
time rg -l 'kind: I-replay' cases/        # => sub-segundo (baseline: 7 ms)
time rg -l 'MISSING_PROPERTY' cases/      # => sub-segundo (baseline: 8 ms)
git diff --stat após editar 1 caso        # => poucas linhas, não o arquivo todo
```

**Gate:** o FTS5 foi **rejeitado por medição** (18,9 MB para salvar ~15 ms). O número existe para que a próxima discussão de "indexar para ficar rápido" comece por ele.

---

## Fase 2.3 — Fontes de log

**Objetivo:** as fontes de log passam a ser declaradas, e o defeito de correlação assíncrona é corrigido (ADR-03).

**Arquivos:** `src/nokr_qa/logs/collector.py` (reescrito), descriptor (`log_sources[]`), `tests/test_logs_collector.py`, `tests/test_logs_async.py` (novo).

**Não fazer:** manter o fallback por janela de tempo; devolver `skipped` quando a causa é "não esperei o suficiente".

**Aceite:** `logs/collector.py` sem fallback de relógio; o defeito do F5 coberto por teste: **o coletor não pode retornar ao achar a linha `sync` quando existe fonte `async` pendente**; `propagate: false` declarado ⇒ `skipped` de instrumento; `propagate: true` + linha ausente ⇒ **`fail` de produto**.

**Verificar:**
```
rg -n 'datetime.now|monotonic' src/nokr_qa/logs/collector.py   # => só deadline, nunca janela de ±1s
python -m pytest -q tests/test_logs_collector.py tests/test_logs_async.py
```

**Gate:** hoje um endpoint assíncrono tem os packs de observabilidade **silenciosamente pulados**. É o único defeito funcional que o estudo encontrou.

---

## Fase 2.4 — Contrato do agente

**Objetivo:** as 9 regras acionáveis que hoje só existem em prosa viram erro de `validate`; as 5 inúteis são apagadas (F6 §3).

**Arquivos:** `src/nokr_qa/validate.py` (as 9 regras), `src/nokr_qa/cli.py` (`validate --explain`, **novo**), `AGENTS.md`, `.cursor/skills/` + `.agents/skills/` (fonte única)

**Não fazer:** apagar as 5 regras antes de as 9 estarem em código (o agente perde o contrato no meio); mover regra para o modelo em vez do código.

**Aceite:** partida de **27.442 → ~2.195 tokens**; `validate --explain` aponta arquivo, linha e correção sugerida; o ponteiro "leia `docs/nokr-qa.md`" sai da skill.

**Verificar:** medir a partida com a mesma heurística do F6 (4 chars/token) e comparar com a tabela de F6 §5.4; cada regra migrada tem um teste que a viola e espera o erro.

**Gate:** 43% das regras acionáveis estão hoje no balde errado — são verificáveis por máquina e não são verificadas. É a maior alavanca isolada do estudo.

---

## Fase 2.5 — `run.json`

**Objetivo:** a saída vira dado. `run.json` com `schema_version` é a fonte; `evidence.md` passa a ser renderização (ADR-10).

**Arquivos:** `src/nokr_qa/run_store.py`, `src/nokr_qa/workspace.py`, `src/nokr_qa/serve/app.py`, `src/nokr_qa/serve/templates/`

**Não fazer:** manter `summary.json` e `book.json` como autônomos — eles entram no `run.json`.

**Aceite:** o review UI lê `run.json`; `evidence.md` é gerado a partir dele; um `run.json` com `schema_version` desconhecida é **recusado** com mensagem acionável.

**Verificar:**
```
ls runs/latest/                      # => run.json presente, summary/book absorvidos
python -m pytest -q tests/test_run_json.py
```
O teste escreve um `run.json` com `schema_version: 99` e afirma que o leitor **recusa** com mensagem acionável, em vez de tentar ler. (`last-run` hoje só aceita `--runs-dir` — a recusa por versão é do loader, e o teste é o lugar certo para ela. Não inventar uma flag `--from`.)

**Gate:** sem isso, nenhuma integração externa acontece sem parsing de markdown.

---

# Etapa 3 — Superfície de navegador (por último)

**Entrega:** portar `ui.*` e packs estruturais, depois o redesenho de UI/UX (F7).
**Gate de saída:** `NOKR_QA_SLOW=1 pytest -m slow tests/test_ui_slow.py` com **7 verdes** sobre o núcleo novo.

---

## Fase 3.1 — Portar o passo `ui`

**Objetivo:** `browser.py` e os packs `ui.*` passam a rodar sobre o núcleo novo. O alvo é **portar**, não reescrever.

**Arquivos:** `src/heimdall_qa/browser.py` (735 linhas), `ui_step.py` (383), `packs/__init__.py` (onde os packs `ui.*` moram — 649 linhas), `pyproject.toml` (extra `[browser]` reativado), `tests/test_ui_*.py`

**Não fazer:** reescrever `browser.py`; deixar `ui` no caminho crítico de qualquer outra fase.

**Aceite:** `NOKR_QA_SLOW=1 pytest -m slow tests/test_ui_slow.py` ⇒ **7 verdes**, com stack vivo. O review de HTTP **não regride**.

**Verificar:** comando acima. Exige `NokrAPI` no ar — é o único gate que não roda em CI hermético.

**Gate:** é o que libera 3.2. E é a metade "a tela funciona" do gate de saída da Etapa 3.

**Risco:** o navegador apodrecer na fila e alguém decidir que reescrever é mais rápido. Os **7** testes são a âncora que impede isso.

---

## Fase 3.2 — Redesenho de UI/UX

**Objetivo:** o review de tela (ARIA, axe com nó apontado, diff de superfície, screenshot), com os 4 blocos que o E6 acrescenta (F7 §3).

**Arquivos:** `src/nokr_qa/serve/app.py`, `src/nokr_qa/serve/templates/`, `src/nokr_qa/serve/static/`

**Não fazer:** trocar por SPA (ADR-04); criar um segundo renderizador para o export estático — é a **mesma** função de render, outra saída.

**Aceite:** review de tela renderizado; export estático do mesmo run abre offline; o review de HTTP não regride.

**Verificar:** `serve --export runs/latest` (**novo**, ADR-04) produz HTML que abre sem servidor. **A própria UI passa em a11y** (aberto #8 de F9 §6.2 — nunca testado).

**Gate:** fecha a Etapa 3 e libera 3.3. É o único gate cujo aceite tem uma parte humana (o desenho) e uma automática (o export abre offline).

---

## Fase 3.3 — Retomada da emenda 11

**Objetivo:** a emenda 11 sai da pausa, condicionada a 3.1.

**Arquivos:** `docs/emenda-11-ui-browser.md` (documento), `rounds/ui-overview.yaml`, os itens E4/E6 que 3.2 não cobriu. Nada no núcleo.

**Não fazer:** reabrir decisões que o estudo já fechou (ADR-04); retomar antes de 3.1 verde; tratar a emenda como se ela nunca tivesse sido pausada.

**Aceite:** os itens do E4/E6 cobertos pelo redesenho de 3.2.

**Verificar:** reler o registro de congelamento (F0 §4) e conferir, item a item, se ele basta para retomar sem reabrir arquivo por arquivo.

**Gate:** é o único ponto do plano em que o **registro de congelamento do E3** (F0 §4) é testado pela realidade: se ao retomar for preciso reabrir arquivo por arquivo para entender o estado, o registro era genérico demais.

---

# Ordem e PRs

| Fase | PR típico | Repo | Etapa |
|---|---|---|---|
| 1.0 | `pyproject` — browser para extra | nokr-qa | 1 |
| 1.1 | descriptor de projeto | nokr-qa | 1 |
| 1.2 | adapter HTTP sobre o descriptor | nokr-qa | 1 |
| 1.3 | migrar o conteúdo para provider | nokr-qa | 1 |
| 1.4 | partir a distribuição + toy provider | nokr-qa | 1 |
| 1.5 | rename mecânico | nokr-qa | 1 |
| 1.6 | docs do núcleo em inglês | nokr-qa | 1 |
| 2.1 | auto-discovery | nokr-qa | 2 |
| 2.2 | consolidação de casos | nokr-qa | 2 |
| 2.3 | fontes de log + defeito async | nokr-qa | 2 |
| 2.4 | 9 regras para `validate` | nokr-qa | 2 |
| 2.5 | `run.json` | nokr-qa | 2 |
| 3.1 | portar passo `ui` | nokr-qa | 3 |
| 3.2 | redesenho UI/UX + export | nokr-qa | 3 |
| 3.3 | retomada da emenda 11 | nokr-qa (+ nokr-ui-lib) | 3 |

**Um PR, uma fase. O aceite da fase anterior no corpo do PR seguinte.**

**Comece por 1.0.** É a mudança de maior impacto de onboarding, de menor risco, e o aceite dela **já passa hoje**. Não depende de nenhuma decisão de desenho.

**O gate que importa é o de 1.4** — o provider de brinquedo numa wheel limpa somado a `git diff campaigns/` vazio. Os dois juntos são a definição operacional de "genérico": o núcleo roda sem o Nokr, e o Nokr roda sobre o núcleo.

---

# O que este plano não decide

| Aberto | Onde | Bloqueia |
|---|---|---|
| formato do envelope de erro | F9 §6.1 #1 | 2.1 |
| `validate --drift` | F9 §6.1 #2 | 2.1 |
| alvo numérico do gate de 2.1 | F9 §6.1 #3 | **2.1 (o próprio gate)** |
| retenção de runs | F9 §6.1 #4 | 2.2 |
| `junit.xml` | F9 §6.1 #5 | 2.5 |
| concorrência `run` × `serve` | F9 §6.1 #6 | 2.2, 2.5 |
| o `serve/` antigo fica de pé na Etapa 2? | F9 §6.2 #7 | 2.5 |
| a UI passa em a11y? | F9 §6.2 #8 | 3.2 |
| W3C `traceparent` | F9 §6.2 #9 | 2.3 |
| **servidor MCP** como interface do agente | F9 §6.3 #10 | nada — frente própria |
| fontes de log remotas (docker/k8s/ssh) | F9 §6.3 #11 | nada |

**Onze abertos, nenhum bloqueia a Etapa 1.** O que mais merece uma frente própria é o servidor MCP: ataca a mesma alavanca do 2.4 aplicada à interface inteira.
