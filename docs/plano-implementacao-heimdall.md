# Heimdall QA — plano de implementação

> **O que é isto.** A sequência de PRs que transforma `nokr-qa` em **Heimdall QA**: um harness de **REST** genérico, renomeado e sem nenhuma referência ao Nokr no núcleo.
> **Onde nasceu.** [`docs/estudo-heimdall/09-consolidacao.md`](estudo-heimdall/09-consolidacao.md) §1 (as 11 ADRs), §2 (as fases) e §4 (os 48 edge cases).
> **Como usar.** Um PR, uma fase. A fase N+1 não começa sem o aceite da fase N. O aceite da fase anterior vai no corpo do PR seguinte.
> **Revisão de 22/09/2026.** Substitui a versão anterior deste plano, que incluía a Etapa 3 (navegador). **O frontend sai do escopo** (§2) e a identidade vira a primeira etapa (§4).
> **Nada aqui está implementado.**

---

## 1. Escopo

**Dentro:** o harness de HTTP. Descriptor de projeto, adapter HTTP, provider, alavancas (auto-discovery, storage, logs, contrato do agente, saída como dado).

**Fora:** todo o teste de navegador. Não é uma fase adiada — **não está neste plano** (§2).

**Consequência dura:** o harness que sai deste plano é REST-only. Ele não instala `playwright`, não importa `browser`, não tem passo `ui`.

**Sem Etapa 3.** A versão anterior tinha três fases de navegador (3.1–3.3). Elas saem, e a condição de retomada da emenda 11 deixa de estar neste plano. O código não é apagado: é **preservado fora de `main`** (§2.2), e volta como **provider**, nunca como código do núcleo.

---

## 2. O frontend sai sem quebrar o backend

O requisito é: *commitar só a parte de REST, não subir nada de frontend, e ainda assim o backend funcionar normalmente*. Três partes.

### 2.1 O que exatamente sai

Inventariado por medição, não por glob:

| O quê | Tamanho | Onde |
|---|---:|---|
| `browser.py` | 735 linhas | `src/nokr_qa/` |
| `ui_step.py` | 383 linhas | `src/nokr_qa/` |
| packs `ui.*` | ~130 linhas | dentro de `packs/__init__.py` (649) |
| `UiStep`, `SurfaceSpec` | ~50 linhas | `schema/models.py:210,253` |
| 8 arquivos de teste + `support_ui.py` | 2.307 linhas | `tests/` |
| `overview.aria.yml` | — | `baselines/ui/` |
| `ui-overview.yaml`, `ui-smoke.yaml` | — | `rounds/`, `suites/` |
| `playwright`, `axe-playwright-python` | **1,3 GB** de binário | `pyproject.toml` |
| 4 chaves do bloco `ui:` (`page_ms`, `logs_ms`, `screenshot`, `a11y`) | — | `config.yaml:9-15` |
| **total** | **~3.605 linhas** | |

**O que NÃO sai, e é fácil confundir:** `src/nokr_qa/serve/` (910 linhas) é a **UI de review do próprio harness** — a tela onde um humano lê um run. Não é teste de frontend e não sai. O mesmo vale para duas das seis chaves do bloco `ui:` do `config.yaml`: **`host` e `port` ficam** (são o bind da UI de review — `serve/bind.py` depende delas); `page_ms`, `logs_ms`, `screenshot` e `a11y` saem (são política do passo de navegador).

**`campaigns/` está limpo e continua limpo.** A busca por `ui` em `campaigns/*.yaml` casa apenas `req`**`ui`**`red` — falso positivo. Nenhuma campanha referencia navegador, então a invariante I3 (`git diff campaigns/` vazio) não é tocada por esta etapa.

### 2.2 Como preservar sem subir

O código já está commitado — o E3 fechou antes desta refatoração. A operação é tirá-lo de `main` e deixá-lo recuperável:

```bash
git tag e3-freeze              # marco do congelamento (o registro está em 00-baseline.md §4)
git branch ui-browser e3-freeze  # a linha do navegador continua viva, fora de main
```

Depois disso, `main` não carrega nada de navegador, e **recuperar é um comando**:
```bash
git checkout e3-freeze -- tests/test_ui_slow.py src/nokr_qa/browser.py
```

Trabalhar localmente no navegador sem subir nada é trabalhar num branch a partir de `e3-freeze`.

> **Ressalva honesta.** A tag preserva o **conteúdo**; o **histórico** já contém o E3 e continuará contendo. Se a publicação exigir que nem o histórico mencione o frontend, isso é `git filter-repo` — operação destrutiva, **fora deste plano**, e só se a publicação realmente precisar.

### 2.3 O corte técnico: 4 linhas

O risco real daqui é `suite_run.py`, que é **caminho HTTP** e importa navegador **no topo do módulo**:

```startLine:16:17:src/nokr_qa/suite_run.py
from nokr_qa.browser import UiDriver
from nokr_qa.browser import UiSession
```
```startLine:60:61:src/nokr_qa/suite_run.py
from nokr_qa.ui_step import UiOutcome
from nokr_qa.ui_step import execute_ui_step
```

Import removido, `suite_run` quebra. Então o corte **não** é `rm`: é instalar uma **costura** e depois remover.

A costura já existe pela metade: `packs/__init__.py:101` despacha por tipo de passo —

```startLine:101:110:src/nokr_qa/packs/__init__.py
def run_all(ctx: PackContext) -> list[PackResult]:
    # A `ui` step has no case contract, so it gets its own set instead of the
    # HTTP one. Dispatching here (rather than in the caller) keeps every step
    # kind going through a single `run_all`, so a new kind cannot silently skip
    # its packs.
    if ctx.case_kind == "ui":
        return run_ui(ctx)
```

O trabalho é transformar esse `if` em **registro de tipos de passo**: o núcleo registra `http`; `ui` deixa de ser um `if` no código e passa a ser um tipo que ninguém registrou. Os 4 imports de `suite_run.py` viram resolução tardia pelo registro — a mesma forma que o provider vai usar em 1.6, o que faz desta costura a fundação da arquitetura de provider, e não um remendo.

**Aceite do corte, prova de que o backend não quebrou:** um round que declara um passo `ui` falha com mensagem acionável (`tipo de passo não registrado: ui`), **nunca com `ImportError`**.

---

## 3. Invariantes

Valem em toda fase. Uma PR que viole qualquer uma é recusada, independentemente de estar verde.

| # | Invariante | Como se verifica |
|---|---|---|
| I1 | **O núcleo não importa provider, em nenhuma fase.** | T3 (§ fase 1.6) |
| I2 | **Nenhum código ou dependência de navegador em `main`.** | `rg -i 'playwright\|axe\|browser\|ui_step' src/ pyproject.toml` |
| I3 | **O Trilho A não muda.** | `git diff campaigns/` vazio |
| I4 | **O E3 não avança nem é revertido.** Preservado em `e3-freeze`, não em `main`. | `git tag --list e3-freeze`; `git diff e3-freeze --stat -- src/nokr_qa/browser.py` vazio |
| I5 | **Não reescrever.** Aditivo e reversível, com prova de não-quebra por fase. | o aceite de cada fase |
| I6 | **Não generalizar antes do segundo provider existir.** | o provider de brinquedo da fase 1.6 |
| I7 | **A fonte de verdade do conteúdo não vira binário.** | sem índice commitado em `cases/` |
| I8 | **O núcleo fica com zero referências de produto.** | `rg -i nokr src/heimdall_qa/` vazio (fase 1.4) |

**I2 e I8 são as duas que definem o resultado.** I2 é "não é um harness de browser"; I8 é "não é um harness do Nokr". Juntas: um harness de REST genérico.

**I4 mudou de forma.** Antes era "congelado no lugar"; agora é "preservado fora de linha". O E3 continua sendo um ativo — só não é um ativo dentro de `main`.

---

## 4. Convenção de caminhos

Os caminhos são os de **hoje**. Duas transformações se aplicam ao longo do plano:

- depois de **1.2**, `src/nokr_qa/` → `src/heimdall_qa/` em todos os caminhos;
- depois de **1.5**, tudo que é conteúdo ou domínio passa a `providers/nokr/` (hoje está na raiz: `cases/`, `contracts/`, `campaigns/`, `rounds/`, `suites/`, `baselines/`, `p-gaps.yaml`, `oracle/`).

Um caminho ou superfície marcado **(novo)** ainda não existe — nem o arquivo, nem a flag. Em particular, `validate --explain` (fase 2.4) e `serve --export` (fase 2.5) são **criados por essas fases**.

---

# Etapa 1 — Identidade e desacoplamento

**Entrega:** o harness renomeado, REST-only, e sem nenhuma referência ao Nokr no núcleo — com o conteúdo do Nokr isolado num provider.
**Gate de saída:** `rg -i nokr src/heimdall_qa/` vazio **e** `git diff campaigns/` vazio **e** T3 verde.

**Por que o rename e a "remoção de todas as referências" não cabem num PR só, nem bastam sozinhos.** Remover as 53 referências de produto do núcleo é uma **migração, não um find-replace**: cada referência precisa de um destino. Os destinos são dois, e por isso as fases 1.3 e 1.5 existem dentro desta etapa:

| Referência | Exemplo | Destino |
|---|---|---|
| **Valor de configuração** | `nokr_web: http://127.0.0.1:8080` (`config.py:50`) | o **descriptor** do projeto (fase 1.3) |
| **Conhecimento de domínio** | `HALF_EVEN`, `exclude_settled_from` (`oracle/money.py`) | o **provider** (fase 1.5) |

Um find-replace deixaria o núcleo sem saber para onde pedir a URL e o oráculo sem saber o que é uma compra. É por isso que a Etapa 1 tem sete fases e não duas.

---

## Fase 1.1 — Cortar o navegador

**Objetivo:** `main` deixa de ter navegador. O harness passa a ser REST-only, com a costura de tipos de passo instalada no lugar dos `if` (§2.3).

**Arquivos:** `src/nokr_qa/suite_run.py` (os 4 imports de `:16-17,60-61` viram registro), `src/nokr_qa/packs/__init__.py` (`run_all:101` despacha por registro; `run_ui`, `run_ui_transport`, `_ui_render`, `_ui_structure`, `_ui_a11y`, `_ui_visual`, `_describe_violations` saem; `NON_WAIVABLE_PACKS` perde `ui.value`), `schema/models.py` (`UiStep:210`, `SurfaceSpec:253` saem), `pyproject.toml`, `config.yaml`, `tests/test_step_kinds.py` (novo).

**Remover:** `src/nokr_qa/browser.py`, `src/nokr_qa/ui_step.py`, `tests/test_ui_*.py` (8), `tests/support_ui.py`, `tests/fixtures/` de ui, `baselines/ui/`, `rounds/ui-overview.yaml`, `suites/ui-smoke.yaml`.

**Não fazer:** **não tocar em `src/nokr_qa/serve/`** — é a UI de review do harness, não teste de frontend (§2.1); não remover `ui.host`/`ui.port` do `config.yaml` (são o bind da review, usados por `serve/bind.py`); não apagar nada antes de a tag `e3-freeze` existir; não mexer em `campaigns/` (está limpo).

**Aceite:** os 257 testes de REST passam com `playwright` **e** `axe` inimportáveis; um round que declara um passo `ui` falha com mensagem acionável, não com `ImportError`; a UI de review continua subindo em `127.0.0.1:7878`.

**Verificar:**
```
git tag --list e3-freeze                                     # existe ANTES de remover
PYTHONPATH=/tmp:src python -m pytest -q --ignore=tests/test_ui_*.py
python -m pytest -q tests/test_step_kinds.py                 # passo ui -> erro claro
rg -i 'playwright|axe|browser|ui_step' src/ pyproject.toml   # => vazio
python -c "import tomllib;d=tomllib.load(open('pyproject.toml','rb'));\
print([x for x in d['project']['dependencies'] if 'playwright' in x or 'axe' in x])"   # => []
```
Script de bloqueio (`tests/test_step_kinds.py` usa o mesmo):
```python
import sys
BLOCKED = ("playwright", "axe_playwright_python")
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"BLOCKED: {name}")
        return None
sys.meta_path.insert(0, Block())
```

**Baseline medido com o browser instalado:** `257 passed, 3 skipped in 6.44s`. **Com o browser bloqueado:** idêntico. O aceite **já passa hoje** — a fase só o torna o estado normal do repo.

**Gate:** é o que produz o artefato REST-only. Sem ela, "não subir frontend" é intenção.

**Reversão:** `git checkout e3-freeze -- <paths>`.

---

## Fase 1.2 — Rename mecânico

**Objetivo:** `nokr_qa` → `heimdall_qa` (ADR-05, ADR-07), em commit isolado e puramente mecânico.

**Arquivos:** `src/nokr_qa/` → `src/heimdall_qa/`, `bin/nokr-qa` → `bin/heimdall-qa`, `pyproject.toml` (`name`, `[project.scripts]`, `package-data`, o marker `slow` do pytest), `tests/` (imports), `src/heimdall_qa/serve/templates/base.html` (chave de `localStorage` `nokr-qa-queue-width` — identidade **do harness**, não do produto).

**Não fazer:** **nenhuma** mudança semântica neste commit — o objetivo é que um erro de digitação apareça no diff; mexer nas referências de produto (isso é 1.3/1.4, e elas não casam `nokr_qa`); renomear `providers/nokr/` (não existe ainda, e o nome é legítimo — o produto testado é o Nokr).

**Aceite:** suíte verde sem mudança de comportamento; `git diff --color-moved` legível; nenhum `nokr_qa` remanescente.

**Verificar:**
```
rg -n 'nokr_qa|nokr-qa|NOKR_QA' src/ tests/ pyproject.toml bin/    # => vazio
PYTHONPATH=/tmp:src python -m pytest -q
```

**Gate:** libera 1.3–1.7. **O rename vem primeiro por uma razão que a versão anterior deste plano não considerava:** todas as fases seguintes escrevem **código novo** (o descriptor, o adapter, o provider). Se o rename vier depois, esse código nasce com o nome antigo e precisa ser renomeado também. Renomear antes faz o trabalho novo nascer certo — e o corte de 1.1 já reduziu a superfície em ~3.605 linhas.

**Superfície:** 761 ocorrências mecânicas em 25 arquivos de `src/` (medido em F8 §0). Nada de decisão.

---

## Fase 1.3 — Descriptor de projeto

**Objetivo:** criar o **destino** das referências de configuração. URL, auth, rotas, budgets, trace e fontes de log deixam de ser campos do núcleo e passam a ser um arquivo **do alvo**, versionado no repo do alvo (ADR-01).

**Arquivos:** `src/heimdall_qa/config.py` (vira o carregador), `src/heimdall_qa/schema/` (modelo Pydantic do descriptor), `config.yaml` → `qa/project.yaml`, `tests/fixtures/descriptors/` (3 exemplos, **novo**), `tests/test_descriptor.py` (**novo**).

**Não fazer:** implementar o adapter HTTP (é 1.4); mover casos ou contratos (é 1.5); construir resolução genérica de N níveis — **dois níveis, o alvo vence**, nada mais.

**Aceite:** o descriptor do **provider de brinquedo** (uma API que não é a Nokr) resolve base_url, auth e rotas; `validate` recusa descriptor sem `base_url` e recusa dois descriptores que declarariam a mesma rota com auth diferente.

**Verificar:** os 3 exemplos de F2 §4 (Nokr, o Node/Express do F2, e o brinquedo) carregam; um teste por campo obrigatório ausente.

**Gate:** sem o destino, 1.4 não tem para onde mover nada. É o pré-requisito lógico de 1.4 e 1.5.

**Risco:** `log_files.web` aponta para **fora do repo** (`../NokrAPI/logs/nokr-web.log` — `config.py:49-54`). Default de produto morre aqui, e vira `log_sources[].path` relativo ao alvo.

---

## Fase 1.4 — Desacoplar o caminho HTTP

**Objetivo:** as **53 referências de produto em 14 arquivos de `src/`** saem do núcleo. Cada uma vai para o descriptor, e o caminho de request passa a lê-las de lá.

**Arquivos:** os 14, medidos um a um.

| Arquivo | refs | O que sai |
|---|---:|---|
| `config.py` | 9 | `nokr_web`, `nokr_admin`, `nokr_dashboard`, `log_files.*`, `bruno_collection` |
| `runner.py` | 9 | `nokr_web`, `nokr_admin`, `X-Nokr-Environment`, `X-Nokr-Admin-Secret`, prefixos de fase |
| `packs/__init__.py` | 6 | regex com `com\.nokr`, `X-Nokr-Environment` (`:15`, `:478-483`) |
| `suite_run.py` | 5 | `nokr_dashboard`, `nokr_web`, `X-Nokr-Environment` |
| `http_client.py` | 4 | `nokr_web`, base URL da NokrAPI |
| `session.py` | 2 | idem |
| `session_validate.py` | — | as seis rotas `/platform/*` (`:30-35`) → `routes[]` |
| `autofill.py` | 1 | `X-Nokr-Environment` |
| `cli.py`, `fixtures.py`, `logs/__init__.py`, `serve/app.py`, `__init__.py` | 5 | rótulos e docs |
| `bru_parser.py`, `collection.py` | — | Bruno como fonte única → adapter `request_source` (V11) |

**Não fazer:** tocar no **oráculo** (`oracle/money.py`, `oracle/book.py`, `packs/values.py`) — V4 é de 1.5, quando `checks` plugáveis existirem; mudar comportamento observável de nenhum case; tocar em `serve/` além do rótulo de 1 linha.

**Aceite:** zero referências de produto no núcleo; **os 539 cases rodam pelo caminho novo com resultado idêntico**; `validate` recusa rota sem `auth` declarado.

**Verificar:**
```
rg -i 'nokr' src/heimdall_qa/                              # => vazio
PYTHONPATH=/tmp:src python -m pytest -q
git diff --stat campaigns/                                 # => vazio (I3)
```

**Gate:** é o corte de verdade. Depois dele o núcleo não sabe o que é Nokr quando faz um request — que é metade de I8 (a outra metade é o conteúdo, em 1.5).

**Risco:** V11 não é descriptor, é **adapter** — a capacidade de ler requests fica no núcleo, a escolha da fonte no descriptor. Não confundir os dois lados.

---

## Fase 1.5 — Extrair o provider

**Objetivo:** o conteúdo e o domínio saem do núcleo e viram provider. `cases/`, `contracts/`, `campaigns/`, `rounds/`, `suites/`, `baselines/`, `p-gaps.yaml` e `oracle/` deixam de ser do núcleo (ADR-02 para o `dto`, ADR-10 para o `validate-docbr`).

**Arquivos:** `qa/project.yaml`, `providers/nokr/` (**novo**), `providers/nokr/cases/`, `.../contracts/`, `.../campaigns/`, `oracle/money.py` + `oracle/book.py` → provider, `tests/test_fixtures.py` migra junto com `validate-docbr`.

**Não fazer:** mexer em `browser.py` ou packs `ui.*` — **não existem mais** (1.1); alterar qualquer YAML do Trilho A (I3); criar `checks` plugáveis genéricos — é Etapa 2, quando houver segundo consumidor.

**Aceite:** os 539 cases rodam verdes; `git diff campaigns/` vazio; nenhum arquivo de conteúdo ou domínio fica no diretório do núcleo.

**Verificar:**
```
git diff --stat campaigns/                 # => vazio
ls providers/nokr/                         # => cases, contracts, campaigns, oracle
ls src/heimdall_qa/oracle 2>/dev/null      # => não existe (foi para o provider)
PYTHONPATH=/tmp:src python -m pytest -q
```

**Gate:** é a metade "o Nokr roda sobre o núcleo" do gate de saída da Etapa 1. A outra é 1.6.

---

## Fase 1.6 — Partir a distribuição e provar o genérico

**Objetivo:** `providers/nokr/` deixa de estar sob o `src/` do núcleo e vira **distribuição própria** (`heimdall-qa-nokr`), ADR-05. Mais o **provider de brinquedo**, que é o gate do desenho.

**Arquivos:** `pyproject.toml` (núcleo), `providers/nokr/pyproject.toml` (**novo**), `providers/nokr/src/heimdall_qa_nokr/`, `examples/toy-provider/` (**novo**: descriptor de 12 linhas + mock FastAPI de 3 rotas + 3 casos).

**Não fazer:** manter o provider dentro do `src/` do núcleo — sem separação física, T3 não prova nada e ADR-05 vira decorativo; fazer do brinquedo um segundo produto real (é deliberadamente barato).

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

**Verificar:** os três blocos. O passo 1 falhando = o núcleo depende do provider; o passo 2 falhando = o núcleo não é utilizável sozinho.

**Status:** **vermelho por construção hoje** — `config.py:50-51` ainda tem `nokr_web`/`nokr_admin` e o descriptor de 1.3 não existe.

**Gate:** fecha a Etapa 1. O brinquedo serve três papéis ao mesmo tempo: exemplo canônico, provider mínimo do teste de contrato, e alvo de T3.

---

## Fase 1.7 — Documentação do núcleo em inglês

**Objetivo:** o núcleo passa a ter documentação em inglês (ADR-08). ~500-600 linhas de prosa **nova**, não tradução de 1.271.

**Arquivos:** `README.md`, `docs/README.md`, `AGENTS.md`, docstrings e comentários em `src/` (158 linhas), `.cursor/skills/` + `.agents/skills/` (fonte única + geração).

**Não fazer:** traduzir `docs/nokr-qa.md` (1.271 linhas) — é a spec **do produto** e fica em português, no provider (move-se em 1.5); traduzir `docs/estudo-heimdall/` (insumo descartável); traduzir as 152 linhas de skill **antes** de consolidar as duas cópias divergentes.

**Aceite:** README + `docs/architecture.md` (**novo**) legíveis sem PT-BR; skill do núcleo em inglês, skill do provider em português; nenhuma regra duplicada entre as duas.

**Verificar:** leitura por quem não fala português — o único aceite humano de toda a Etapa 1, e por isso o último.

**Gate:** fecha a Etapa 1.

---

# Etapa 2 — As alavancas

**Entrega:** auto-discovery (F3), storage e índice (F4), fontes de log (F5), contrato do agente (F6), saída como dado.
**Gate de saída:** round gerado de OpenAPI roda sem o agente escrever YAML mecânico; busca em run sub-segundo; **nenhum fallback por relógio local**; partida de ~2.195 tokens.

---

## Fase 2.1 — Auto-discovery

**Objetivo:** o agente deixa de escrever YAML mecânico. `dto` deixa de ser obrigatório (ADR-02) e a fonte do contract passa a ser o schema.

**Arquivos:** `src/heimdall_qa/schema/` (campo `dto` opcional — hoje `str` obrigatório e decorativo, o que torna o formato impossível fora do Java), `scaffold.py` (o gerador), `session_validate.py` e `runner.py` (tratam `generate:`), `tests/fixtures/openapi/` (**novo**).

**Não fazer:** gerar exaustivamente — o teto é `rotas × eixos_deriváveis`; inventar caso a partir de schema: o não-derivável sai como `TODO` explícito e o `validate` recusa.

**Aceite:** um round gerado de `docs/estudo-heimdall/prototipos/openapi-ingest-3.1.yaml` roda **sem o agente escrever YAML mecânico**; todo campo não-derivável sai como `TODO` e o `validate` os aponta.

**Verificar:** gerar, rodar, e conferir que o diff entre o gerado e o contract escrito à mão é só o não-derivável. **Número a bater: 48,8% derivável** (263 de 539, F3 §4).

**Gate:** **o alvo numérico deste gate não está fixado** (aberto #3 de F9 §6.1). A medição de 48,8% é de uma rota no protótipo, não do corpus — fixar o número antes de fechar a fase.

---

## Fase 2.2 — Storage e consolidação de casos

**Objetivo:** a granularidade de arquivo deixa de ser 539 arquivos para 48 contracts.

**Arquivos:** `providers/nokr/cases/` (consolidação), `coverage.py`, `validate.py`

**Não fazer:** **não ordenar os casos por ID** dentro do arquivo consolidado — a ordem do disco é preservada, senão todo arquivo vira diff churn a cada edição (F4); não commitar índice binário (I7).

**Aceite:** 539 arquivos → **47**, com custo de 1,12× em bytes; busca continua sub-segundo **sem índice**; editar um caso produz diff de poucas linhas.

**Verificar:**
```
time rg -l 'kind: I-replay' providers/nokr/cases/     # => sub-segundo (baseline: 7 ms)
time rg -l 'MISSING_PROPERTY' providers/nokr/cases/   # => sub-segundo (baseline: 8 ms)
git diff --stat após editar 1 caso                    # => poucas linhas, não o arquivo todo
```

**Gate:** o FTS5 foi **rejeitado por medição** (18,9 MB para salvar ~15 ms). O número existe para que a próxima discussão de "indexar para ficar rápido" comece por ele.

---

## Fase 2.3 — Fontes de log

**Objetivo:** as fontes de log passam a ser declaradas e o defeito de correlação assíncrona é corrigido (ADR-03).

**Arquivos:** `src/heimdall_qa/logs/collector.py` (reescrito), descriptor (`log_sources[]`), `tests/test_logs_collector.py`, `tests/test_logs_async.py` (**novo**).

**Não fazer:** manter o fallback por janela de tempo; devolver `skipped` quando a causa é "não esperei o suficiente".

**Aceite:** sem fallback de relógio; o defeito do F5 coberto por teste — **o coletor não pode retornar ao achar a linha `sync` quando existe fonte `async` pendente**; `propagate: false` declarado ⇒ `skipped` de instrumento; `propagate: true` + linha ausente ⇒ **`fail` de produto**.

**Verificar:**
```
rg -n 'datetime.now|monotonic' src/heimdall_qa/logs/collector.py   # => só deadline, nunca janela de ±1s
PYTHONPATH=/tmp:src python -m pytest -q tests/test_logs_collector.py tests/test_logs_async.py
```

**Gate:** hoje um endpoint assíncrono tem os packs de observabilidade **silenciosamente pulados**. É o único defeito funcional que o estudo encontrou.

---

## Fase 2.4 — Contrato do agente

**Objetivo:** as 9 regras que hoje só existem em prosa viram erro de `validate`; as 5 inúteis são apagadas (F6 §3).

**Arquivos:** `src/heimdall_qa/validate.py` (as 9 regras), `src/heimdall_qa/cli.py` (`validate --explain`, **novo**), `AGENTS.md`, `.cursor/skills/` + `.agents/skills/` (fonte única)

**Não fazer:** apagar as 5 regras antes de as 9 estarem em código (o agente perde o contrato no meio); mover regra para o modelo em vez do código.

**Aceite:** partida de **27.442 → ~2.195 tokens**; `validate --explain` aponta arquivo, linha e correção sugerida; o ponteiro "leia `docs/nokr-qa.md`" sai da skill.

**Verificar:** medir a partida com a mesma heurística do F6 (4 chars/token) contra a tabela de F6 §5.4; cada regra migrada tem um teste que a viola e espera o erro.

**Gate:** 43% das regras acionáveis estão hoje no balde errado — são verificáveis por máquina e não são verificadas. É a maior alavanca isolada do estudo.

---

## Fase 2.5 — `run.json` e export

**Objetivo:** a saída vira dado. `run.json` com `schema_version` é a fonte; `evidence.md` passa a ser renderização (ADR-10). Mais o export estático (ADR-04), que **passa a existir aqui** porque é a mesma função de render com outra saída — e é ele que permite arquivar um run para um PR.

**Arquivos:** `src/heimdall_qa/run_store.py`, `src/heimdall_qa/workspace.py`, `src/heimdall_qa/serve/app.py`, `src/heimdall_qa/serve/templates/`

**Não fazer:** manter `summary.json` e `book.json` como autônomos — entram no `run.json`; criar um **segundo** renderizador para o export.

**Aceite:** a review UI lê `run.json`; `evidence.md` é gerado a partir dele; `run.json` com `schema_version` desconhecida é **recusado** com mensagem acionável; `serve --export runs/latest` (**novo**) produz HTML que abre offline.

**Verificar:**
```
ls runs/latest/                      # => run.json presente, summary/book absorvidos
PYTHONPATH=/tmp:src python -m pytest -q tests/test_run_json.py
python -m pytest -q tests/test_export.py
```
O teste escreve um `run.json` com `schema_version: 99` e afirma que o leitor **recusa** com mensagem acionável, em vez de tentar ler. (`last-run` hoje só aceita `--runs-dir` — a recusa por versão é do loader, e o teste é o lugar certo para ela. Não inventar uma flag `--from`.)

**Gate:** sem isso, nenhuma integração externa acontece sem parsing de markdown.

---

# Ordem e PRs

| Fase | PR típico | Repo | Etapa |
|---|---|---|---|
| 1.1 | cortar o navegador + registro de tipos de passo | nokr-qa | 1 |
| 1.2 | rename mecânico `nokr_qa` → `heimdall_qa` | nokr-qa | 1 |
| 1.3 | descriptor de projeto | nokr-qa | 1 |
| 1.4 | desacoplar o caminho HTTP (53 refs) | nokr-qa | 1 |
| 1.5 | extrair o provider | nokr-qa | 1 |
| 1.6 | partir a distribuição + toy provider + T3 | nokr-qa | 1 |
| 1.7 | docs do núcleo em inglês | nokr-qa | 1 |
| 2.1 | auto-discovery | nokr-qa | 2 |
| 2.2 | consolidação de casos | nokr-qa | 2 |
| 2.3 | fontes de log + defeito async | nokr-qa | 2 |
| 2.4 | 9 regras para `validate` | nokr-qa | 2 |
| 2.5 | `run.json` + export | nokr-qa | 2 |
| — | **navegador** | **fora deste plano** (§2) | — |

**Um PR, uma fase. O aceite da fase anterior no corpo do PR seguinte.**

**Comece por 1.1.** É o que produz o artefato REST-only, e o aceite dela **já passa hoje** (`257 passed, 3 skipped` com o browser bloqueado). Ela também encolhe as fases seguintes: ~3.605 linhas saem, e o rename de 1.2 passa a ter menos arquivos.

**Depois 1.2, e só depois 1.3.** O rename vem cedo porque todo o código novo das fases seguintes nasce com o nome certo.

**O gate que importa é o de 1.4** — `rg -i nokr src/heimdall_qa/` vazio —, e o segundo é o de 1.6 (T3 + `git diff campaigns/` vazio). Juntos: o núcleo não sabe o que é Nokr, roda sem o Nokr, e o Nokr roda sobre o núcleo.

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
| a UI de review passa em a11y? | F9 §6.2 #8 | 2.5 |
| W3C `traceparent` vs header próprio | F9 §6.2 #9 | 2.3 |
| **servidor MCP** como interface do agente | F9 §6.3 #10 | nada — frente própria |
| fontes de log remotas (docker/k8s/ssh) | F9 §6.3 #11 | nada |

**Dez abertos, nenhum bloqueia a Etapa 1.** O que mais merece uma frente própria é o servidor MCP: ataca a mesma alavanca de 2.4 aplicada à interface inteira.

**Sobre o retorno do navegador.** Ele não tem fase, não tem gate e não tem data neste plano. Quando voltar, o caminho já está preparado: a costura de tipos de passo de 1.1 e o registro de provider de 1.6 fazem dele um **provider** (`heimdall-qa-browser`), nunca código do núcleo. É por isso que a Etapa 3 não faz falta como plano — faz falta como *branch*.
