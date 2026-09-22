# F8 — Rename, OSS e o corte do provider

> **Frente:** F8 do [plano de estudo](../../../../../.cursor/plans/heimdall_qa_plano_de_estudo_eb1f8620.plan.md).
> **Pergunta:** o que fica no núcleo e o que vai para o provider, e em que ordem?
> **Escopo declarado no plano:** o corte é aplicado **só sobre o caminho HTTP**. `browser.py` e os packs `ui.*` migram na fase 3.
> **Entregáveis:** critério de corte aplicado item a item, plano de empacotamento, definição do provider de brinquedo.
> **Data:** 22/09/2026.

---

## 0. Resumo executivo

| Decisão | Resultado | Onde |
|---|---|---|
| Nome | **Heimdall QA** — distribuição `heimdall-qa`, import `heimdall_qa`, CLI `heimdall-qa`, env `HEIMDALL_QA_*`. **`heimdall-qa` está livre no PyPI** (verificado, HTTP 404) | §3 |
| Idioma da documentação | **Inglês no núcleo, português permitido no provider.** A conta real é **~500-600 linhas de prosa nova**, não 1.271: as 1.271 são a spec do *produto* e ficam em português | §4 |
| Empacotamento | **Duas distribuições, um repo**: `heimdall-qa` (núcleo) + `heimdall-qa-nokr` (provider). `playwright`+`axe` saem para `[browser]` (**1,3 GB**); `faker` **fica** (140 KB); `validate-docbr` vai para o provider | §5, §6 |
| Licença | **Apache-2.0** no núcleo. Atenção a um achado: `axe.min.js` é **MPL-2.0** | §7 |
| Versionamento | `schema_version` em `run.json` — **que ainda não existe**: hoje `evidence.md` *é* a fonte, não a renderização | §8 |
| Provider de brinquedo | `examples/toy-provider/` — 1 descriptor de 12 linhas + 1 mock FastAPI + 3 casos. Gate em §9.4 | §9 |

**O achado que muda a ordem de serviço.** O critério do estudo §1 aplicado com instrumento (não a olho) dá um número muito melhor do que o F1 sugeria:

```
1.432 ocorrências de `nokr*` na superfície estudada
  761  identidade do harness  (`nokr_qa` / `nokr-qa`)  → rename mecânico, zero semântica
  671  identidade de produto  (47 tokens distintos)     → a lista de corte de verdade
```

E a distribuição **por escopo** diz onde dói e onde não dói:

| Escopo | rename mecânico | produto | % produto |
|---|---:|---:|---:|
| `src/` | 260 | 53 | **17%** |
| `tests/` | 231 | 214 | 48% |
| `docs/` | 159 | 330 | 67% |
| raiz (YAML/Bruno/shell) | 111 | 74 | 40% |
| **total** | **761** | **671** | **47%** |

O núcleo já está **83% limpo**. O F1 falava em 251 ocorrências sem separar pacote de produto, o que superestimava o trabalho no lugar errado: o grosso do acoplamento está em testes, docs e YAML de conteúdo — que é onde ele **deve** estar, porque é a camada de conteúdo (§1 do estudo). O corte caro fica concentrado em **47 tokens**, não em 1.432 ocorrências.

> **Método, para poder ser refutado.** O script conta `[Nn]okr[A-Za-z0-9_-]*` em `src/`, `tests/`, `docs/` (`.py .html .css .yaml .md`) e na raiz (`.yaml .yml .bru .sh .toml .md`, excluindo `runs/ cases/ contracts/ campaigns/ rounds/ suites/`). Tudo que casa exatamente `nokr_qa` ou `nokr-qa` é renomeação mecânica; o resto é candidato a produto e passa pelo crivo de §2.2. Uma armadilha que custou uma medição errada: um padrão que inclui `.` na classe de caracteres casa `nokr_qa.schema.models` como **um** token e o classifica como produto — foram ~250 falsos positivos antes de a classe ser corrigida. Números refeitos: 671, não 589.

---

## 1. O critério, agora falsificável

O estudo §1 dá um critério em prosa:

> se remover o provider deixar o núcleo sem conseguir importar, houve vazamento.

Isso é bom como princípio e fraco como gate: ninguém "remove o provider" para ver o que acontece. A lista de edge cases do plano pede explicitamente *"falta um teste que **prove** que o núcleo não importa provider (equivalente a ArchUnit, em Python)"*. O critério vira **três testes**, do mais fraco ao mais forte:

| # | Teste | O que pega | Força |
|---|---|---|---|
| **T1** | `rg -i 'nokr' src/heimdall_qa/` retorna vazio | vazamento lexical | fraca — regex burra |
| **T2** | `import heimdall_qa, heimdall_qa.cli` num subprocesso com `sys.path` que **exclui** todo pacote `heimdall_qa_*` | vazamento de import | média |
| **T3** | buildar a wheel do núcleo, instalar num venv limpo com **só** as deps do núcleo, rodar `heimdall-qa --help` e `heimdall-qa run <toy>` | vazamento de empacotamento | **forte — é o gate** |

**T3 é o único que é gate.** T1 e T2 são pré-condições de review, não de CI (T1 é a regex que o F1 já mostrou ser frágil; T2 não pega uma dependência declarada a mais no `pyproject.toml`). T3 pega exatamente o que interessa: **se o núcleo precisa do provider para instalar ou rodar, ele não é genérico** — e ele falha por *construção*, não por opinião.

Corolário para o §5: T3 só é executável se o núcleo e o provider forem **distribuições separadas**. Empacotamento não é decisão de conveniência aqui; é o que torna o gate possível. Essa é a razão técnica para a recomendação do estudo §13.3.

### 1.1 O que isso não prova

T3 prova que o núcleo não *depende* do Nokr. Não prova que ele é *útil* sem o Nokr — isso é o provider de brinquedo (§9), que é o gate do **desenho**, não do empacotamento. Os dois gates são diferentes e ambos são necessários: T3 pega o acoplamento estrutural, o de brinquedo pega o acoplamento conceitual (o núcleo "saber" o que é dinheiro mesmo sem citar o Nokr).

---

## 2. O critério aplicado item a item

O plano pede "o critério do estudo §1 aplicado a cada item de F1". Aplicar isso aos **11 vazamentos nomeados** do F1 é o exercício pedido; aplicar aos **47 tokens** medidos é o que fecha a conta.

### 2.1 Os 11 vazamentos de F1

| # | Item (F1) | Destino | Forma que assume no destino |
|---|---|---|---|
| V1 | Header de ambiente (`X-Nokr-Environment`) | **descriptor** | `environment.header` + `environment.values[]` |
| V2 | Auth por prefixo de rota | **descriptor** | `routes[].auth` |
| V3 | Budget e async por rota | **descriptor** | `routes[].budget` / `routes[].async` |
| V4 | Oráculo de dinheiro (`HALF_EVEN`, 402, `INSUFFICIENT`) | **provider Nokr** | `checks.money_debit` (§2.3 — é o item mais caro) |
| V5 | Matriz de casos em código de schema | **provider** (descriptor de campanha) | `campaigns/*.yaml` no repo do provider |
| V6 | Prefixo de API key (`nk_test_`) | **descriptor** | `auth.api_key.prefixes[]` |
| V7 | Rotas de warmup | **descriptor** | `routes[].warmup: true` |
| V8 | Nome de eixo `S-bola` (produto) | **rename** | eixo neutro; o rótulo vira dado |
| V9 | Pacotes de produto no regex de stack trace (`com.nokr`) | **descriptor** | `errors.product_packages[]` |
| V10 | Fontes de log fixas | **descriptor** | `log_sources[]` (F5 §2 já desenhou o formato) |
| V11 | Bruno como fonte de request | **adapter** | `request_source: { type: bruno \| openapi \| postman \| insomnia \| inline }` |

**Observação sobre V11.** É o único item que não é descriptor nem provider — é *adapter*. A razão é que a fonte de request é uma **capacidade do núcleo** (ler requests), não um dado de produto: OpenAPI e Postman servem a qualquer produto. O que é dado de produto é *qual* fonte. Logo: capacidade no núcleo, escolha no descriptor. Isso é coerente com o estudo §3.

**Observação sobre V4.** É o único item que **não pode** virar descriptor por completo. A aritmética (`decimal_delta`, `scale`, `rounding`) é dado, mas a *lista de exclusões* (`402`, `INSUFFICIENT`, replay) é semântica de domínio e precisa de código no provider. Ver §2.3.

**Onde os V's moram hoje, concretamente.** `config.yaml` **já é** o descriptor — feito, porém, como arquivo *do harness*, no repo do harness, com chaves de produto e defaults que apontam para fora:

```startLine:49:54:src/nokr_qa/config.py
    bruno_collection: str = ""
    nokr_web: str = "http://127.0.0.1:8080"
    nokr_admin: str = "http://127.0.0.1:9090"
    nokr_dashboard: str = "http://localhost:4200"
    ui: UiConfig = Field(default_factory=UiConfig)
```

Duas coisas são notáveis. Primeiro, **três dos quatro defaults são URLs de produto** — um núcleo instalado por um terceiro sobe apontando para `localhost:8080` da Nokr, e `log_files.web` aponta para `../NokrAPI/logs/nokr-web.log`, um caminho **fora do repo**. Segundo, **a forma já é a certa**: o bloco `ui:` já é agrupado por concern, o que é a estrutura que o F2 propôs. O corte de V1–V3, V6, V7, V9 e V10 é, na prática, **renomear o arquivo, remover os defaults de produto e movê-lo para o repo do produto** — não redesenhar. O trabalho caro é a *resolução* (dois níveis, produto vencendo, ADR-01 do F2), não o formato.

### 2.2 Os 47 tokens, agrupados em 7 famílias

Os 47 tokens distintos não são 47 problemas: eles caem em 7 famílias, e **cada família tem um destino** — a maioria, um destino único. É isso que torna o corte mecânico depois de decidido. Todos os números fecham em 671 (§0).

| Família | tokens | ocorr. | Tokens | Destino |
|---|---:|---:|---|---|
| **F-a. Identidade de repositório** | 10 | 358 | `nokr`, `nokrapi`, `nokr-ui-lib`, `nokr-b2b-dashboard`, `nokr-workspace`, `nokr-website`, `nokr-frontend`, `nokr-api`, `nokr-billing`, `nokr-round` | `project.id` + paths relativos no descriptor |
| **F-b. Fontes de log** | 6 | 88 | `nokr-web`, `nokr-worker`, `nokr-admin`, `nokr_web`, `nokr_admin`, `nokr_web_and_budgets` | `log_sources[]` (F5) |
| **F-c. Auth e ambiente** | 4 | 55 | `nokr-environment`, `nokr-admin-secret`, `nokr_selected_environment`, `nokr-button` | `auth` / `environment` no descriptor |
| **F-d. Tenant e fixture** | 4 | 51 | `nokr_user_id`, `nokr_dashboard`, `nokr_audit_results`, `nokr_qa_audit` | fixtures declaradas (`generate:` / `capture:`) |
| **F-e. Skill e doc** | 2 | 24 | `nokr-qa-round`, `nokrapi_agents_points_to_sibling_harness` | rename: skill `heimdall-qa-round` |
| **F-f. Dado de campanha** | 19 | 90 | `nokrqa-phase4` (33), `nokrqa-demo-1/2`, `nokrqa-ui-*`, `nokrqa-structure-*`, `nokrqa-run-1`, `nokrqa-phase3`… | dado de caso, **não é código** |
| **F-g. Harness disfarçado** | 1 | 5 | `nokr-qa-queue-width` | **rename mecânico** → `heimdall-qa-queue-width` |
| | **47** | **671** | | |

Três leituras que só aparecem com a tabela montada:

**F-f é o maior token-sprawl e o menor problema.** 19 tokens para 90 ocorrências (média de 4,7) — são nomes de dado de volume de campanha (`nokrqa-phase4`, `nokrqa-demo-1`), gerados por execução de teste. Não têm destino em código nenhum: são conteúdo, e conteúdo é do provider por definição. Note que **33 das 90 vêm de um único token** (`nokrqa-phase4`, um rótulo de fase de campanha) — o que confirma que a cauda longa aqui é ruído de dado, não acoplamento.

**F-a é o inverso: 10 tokens para 358 ocorrências** (média de 36). É onde está o trabalho real de corte, e é onde a decisão de *onde* cada referência mora precisa ser tomada caso a caso — uma referência a `NokrAPI` num comentário não é a mesma coisa que um path em `config.yaml`.

**F-g é o achado que justifica ter medido em vez de olhado.** `nokr-qa-queue-width` é a chave de `localStorage` do resizer da UI de review — 5 ocorrências, duas delas em `serve/templates/base.html:11,98` — e é identidade *do harness*, não do produto. Um grep cego por `nokr` a classificaria como vazamento de produto e a mandaria para o descriptor, onde ela não tem lugar nenhum. A medição por token é o que separa "tem nokr no nome" de "sabe o que é Nokr".

### 2.3 O item que não generaliza: o oráculo de dinheiro (V4)

O estudo §6 já decidiu a forma: **aritmética no núcleo, domínio no provider**. O F8 precisa dar o passo seguinte, que é dizer **o que exatamente cruza a fronteira**, porque é aqui que a generalização ingênua morre.

```yaml
# descriptor do provider Nokr — não no núcleo
checks:
  money_debit:
    type: decimal_delta        # aritmética: núcleo
    scale: 5                   # aritmética: núcleo
    rounding: HALF_EVEN        # aritmética: núcleo
    exclude_settled_from:      # domínio: provider
      status: [402]
      reason: [INSUFFICIENT]
    replay: exclude            # domínio: provider
```

| Peça | Lado | Por quê |
|---|---|---|
| `decimal_delta`, `scale`, `rounding` | **núcleo** | é aritmética; um produto de estoque usaria `integer_delta` |
| a **mecânica** (foto antes, lote, espera com retry, timeout que **falha** e não pula) | **núcleo** | é infraestrutura de teste |
| `exclude_settled_from`, `replay` | **provider** | é o que significa "isso não é uma compra" |
| a **lista** `[402, INSUFFICIENT]` | **descriptor** | é dado, e é o que o `validate` consegue checar antes de rodar |

O ponto fino: `exclude_settled_from` é *código no provider* porque exige **ler um estado** (o status/razão do retorno) e decidir; `[402]` é *dado no descriptor* porque é uma constante. Separar os dois é o que permite ao `validate` avisar "essa exclusão referencia um status que nenhum caso produz" — hoje impossível, porque está tudo enterrado em `oracle/money.py`.

### 2.4 Onde o corte encosta no caminho HTTP (escopo da fase 1)

O plano restringe o corte à fase 1 ao caminho HTTP. Aplicando: **V1, V2, V3, V6, V7, V9, V10, V11 saem na fase 1** (todos descriptor/adapter, todos alcançáveis por HTTP). **V4 sai na fase 1 pela metade**: a aritmética fica no núcleo (é usada por `packs/values.py`, que é caminho HTTP), mas `exclude_settled_from` e `replay` só migram junto com o provider na fase 2, quando `checks` plugáveis existirem.

**V5 e V8 são de fase 2** (campanha e eixos são agnósticos, mas a matriz de casos é conteúdo). **E3/`ui.*` inteiro é fase 3** — e o plano já registrou que é seguro deixá-lo no provider até lá, porque nenhum outro provider depende dele.

---

## 3. Nome

**Heimdall QA.** Decidido. O que o F8 tem de resolver é a mecânica da troca.

| Artefato | Valor |
|---|---|
| distribuição | `heimdall-qa` |
| import | `heimdall_qa` |
| CLI | `heimdall-qa` |
| variáveis de ambiente | `HEIMDALL_QA_*` |
| distribuição do provider | `heimdall-qa-nokr` |
| import do provider | `heimdall_qa_nokr` |
| skill do agente | `heimdall-qa-round` |
| chave de `localStorage` da UI | `heimdall-qa-queue-width` |

**Disponibilidade verificada em 22/09/2026:**

```
heimdall-qa   HTTP 404  LIVRE
heimdall      HTTP 200  TOMADO  (heimdall 0.0.6 — biblioteca de screenshot, abandonada)
heimdallqa    HTTP 404  livre
```

**A ressalva que precisa ser dita.** `heimdall-qa` está livre, mas *Heimdall* é um nome saturado em software — há um dashboard de auth em Go, projetos de observabilidade, e um `heimdall` já ocupado no PyPI por uma biblioteca abandonada de screenshot (ironicamente, de outra época deste projeto). O nome é **disponível, não distintivo**. Duas consequências práticas:

1. **Registrar `heimdall-qa` no PyPI cedo**, mesmo sem release. Custa minutos e elimina o risco de squatting justamente na véspera da publicação — que é onde o P4 do estudo §11 mora.
2. **Não brigar pelo nome curto.** Nunca usar `heimdall` como import ou distribuição; o sufixo `-qa` é o que dá alguma distintividade e é ele que fica.

Se a distintividade incomodar depois, a troca de nome é barata **agora** (0 usuários) e caríssima depois do P4. O F8 registra a decisão e o custo de mantê-la, sem reabrir a discussão: o plano já fixou "Heimdall QA".

### 3.1 A ordem do rename

O plano já decidiu: **"aplicar o rename por último, quando o comportamento já estiver provado"**. O F8 concorda e acrescenta a razão mecânica: o rename toca **1.432 ocorrências** (§0) — 313 em `src/`, 445 em `tests/` — e fazê-lo antes do corte do provider obrigaria a revisar o mesmo diff duas vezes, misturando ruído mecânico com mudança de comportamento em arquivos que o provider também vai reescrever. Sequência:

1. corte do provider (comportamento provado verde) →
2. rename do pacote (`nokr_qa` → `heimdall_qa`), commit isolado, diff puramente mecânico →
3. rename dos tokens de produto (F-a a F-g), cada família em um commit →
4. reescrita da documentação do núcleo em inglês (§4).

O passo 2 não deve conter **nenhuma** mudança semântica, para que `git diff --color-moved` seja legível e um erro de digitação apareça. O passo 3 é onde o comportamento pode mudar, e por isso vem depois do passo 2.

---

## 4. Idioma da documentação

### 4.1 O número que o plano usa está errado, e isso muda o custo

O plano diz: *"hoje a prosa é português e o código é inglês; OSS pede inglês, e essa decisão precede reescrever 1.271 linhas."* As 1.271 linhas são `docs/nokr-qa.md` — que é a **spec do contrato da NokrAPI**, não documentação do harness:

```startLine:1:4:docs/nokr-qa.md
# Nokr QA
```

Depois do corte do provider, esse arquivo vai para **o provider**, junto com `emenda-11-ui-browser.md` (536 linhas). Ambos descrevem produto. Não há razão para traduzir: o provider Nokr é interno, os leitores são o time Nokr, e traduzir perderia a correspondência direta com o vocabulário do produto (`Trilho A`, `P-live`, `S-bola`) que o time usa para falar.

**A conta real do que o núcleo precisa em inglês:**

| Arquivo | Linhas | Camada | Destino |
|---|---:|---|---|
| `docs/nokr-qa.md` | 1.271 | provider | **português, fica** |
| `docs/emenda-11-ui-browser.md` | 536 | provider (pausada) | **português, fica** |
| `docs/estudo-heimdall/*.md` (9 arquivos) | 2.876 | artefato de estudo | **não traduzir** — é insumo, não entrega |
| `docs/estudo-harness-agnostico.md` | 354 | núcleo | **inglês** — vira a semente do `docs/` definitivo |
| `README.md` | 334 | núcleo | **inglês** |
| `docs/README.md` | 12 | núcleo | **inglês** |
| `AGENTS.md` | 17 | núcleo | **inglês** |
| skills (2 cópias: 91 + 61) | 152 | metade núcleo, metade provider | **dividir** |
| docstrings e comentários em `src/` | 158 | núcleo | **inglês** |
| `docs/notes-0109.md` | 2 | — | **apagar** |

**Bill de tradução do núcleo: ~875 linhas**, das quais 354 são o `estudo-harness-agnostico.md` — que não deve ser traduzido literalmente, e sim usado como **origem** do `docs/architecture.md` definitivo em inglês. Trabalho líquido de prosa nova em inglês: **~500-600 linhas**, menos da metade do que as 1.271 do plano sugeriam.

Duas correções ao enunciado do plano, ambas por medição: as 1.271 linhas **não são do núcleo** (são a spec do produto e ficam em português), e o alvo "3 arquivos de estudo" era um arquivo só.

### 4.2 Política

| Superfície | Idioma | Razão |
|---|---|---|
| Código (nomes, logs, mensagens de erro) | **inglês** | já é, e a constituição da NokrAPI (§34) exige |
| README e docs de arquitetura do núcleo | **inglês** | porta de entrada do OSS |
| ADRs do núcleo | **inglês** | são o contrato público do desenho |
| Spec e docs do provider Nokr | **português** | leitor interno; vocabulário de produto |
| README do provider Nokr | **inglês** | é publicado junto com o Nokr, que é o exemplo canônico |
| Issues e PRs do núcleo | **inglês** | OSS |
| Issues e PRs do provider | **português** | time interno |

O único ponto de atrito é o provider Nokr, que é ao mesmo tempo interno (prosa) e o exemplo canônico (leitor externo). A saída: **prosa em português, README em inglês**, e o README aponta para o `docs/project.yaml` — que é a peça que o leitor externo realmente precisa copiar, e é YAML, portanto sem idioma.

### 4.3 `AGENTS.md` e as duas cópias da skill

F6 encontrou duas cópias divergentes da skill — 8.068 e 7.499 bytes, 91 e 61 linhas. Traduzir e renomear 152 linhas em duas cópias que já divergiram consolida o erro: primeiro a fonte única, depois a tradução. **Uma fonte, duas gerações** (§5.4), e a divisão:

- **`heimdall-qa-round` (núcleo, inglês):** como escrever caso, contract, rodada; o que o `validate` cobra; onde ler o run.
- **`nokr-round` (provider, português):** o vocabulário do produto, o Trilho A, os dados de sandbox, os oráculos de domínio.

Hoje isso está misturado num arquivo só, e é por isso que o agente paga ~27k tokens de partida (F6). A divisão por camada é o que torna o alvo de leitura do F6 alcançável.

---

## 5. Empacotamento

### 5.1 Duas distribuições, um repo

| | `heimdall-qa` | `heimdall-qa-nokr` |
|---|---|---|
| conteúdo | núcleo: runner, packs genéricos, logs, review, validate, schema | descriptor Nokr, oráculo de dinheiro, fixtures locais, campaigns, cases, contracts, baselines |
| deps | §5.2 | `heimdall-qa` + nada |
| licença | Apache-2.0 | Apache-2.0 |
| versiona | independente | independente, com faixa de compatibilidade |

```
heimdall-qa/                       # repo (hoje: nokr-qa)
├── src/heimdall_qa/               # núcleo
│   ├── runner.py  packs/  logs/  serve/  schema/
│   └── provider.py                # o Protocol (§5.5)
├── providers/
│   └── nokr/                      # distribuição separada, mesmo repo
│       ├── pyproject.toml
│       └── src/heimdall_qa_nokr/
│           ├── descriptor.yaml    # o que hoje é config.yaml
│           ├── money.py           # o que hoje é oracle/money.py
│           ├── campaign/  cases/  contracts/  baselines/
├── examples/
│   └── toy-provider/              # §9
└── tests/
```

**Por que um repo e não dois.** O estudo §13.3 pede núcleo e provider **separados**, e a §11 P3 quer o provider de segunda UI provando o desenho. Nenhum dos dois exige dois repos: exige duas **distribuições**, porque é a distribuição que o T3 (§1) testa. Um repo mantém o custo de desenvolvimento baixo (um CI, um `git log`) enquanto a fronteira permanece falsificável. Extrair o provider para repo próprio é uma operação de `git filter-repo` de custo baixo, disponível no P4, se o provider Nokr virar privado.

**Por que `providers/nokr` e não `src/heimdall_qa_nokr`.** Dentro de `src/` da mesma distribuição, o T3 não consegue provar nada: o import resolveria localmente e o acoplamento ficaria invisível. A separação física do diretório é o que dá dentes ao gate.

### 5.2 Dependências: medido, e a intuição estava errada em dois de quatro

A hipótese inicial era "mover browser, axe, faker e validate-docbr para extras". Medido, o corte só se paga para **um** deles, e por uma razão que só aparece quando se mede o binário em vez do pacote:

| Dep | Tamanho | Deps duras | Bloqueada, o núcleo importa? | Veredito |
|---|---:|---:|---|---|
| `playwright` | 39 KB | 2 | **23/23 OK** | **sai para `[browser]`** |
| `axe-playwright-python` | 6 KB | 1 (`playwright`) | **23/23 OK** | sai junto |
| `faker` | 140 KB | 1 (`tzdata`) | 7 módulos quebram | **fica** |
| `validate-docbr` | 9 KB | 0 | 7 módulos quebram | sai, mas **para o provider** |

**O número que decide não é o tamanho do pacote: é o binário.** `du -sh ~/.cache/ms-playwright` = **1,3 GB**. Nenhuma das outras três passa de 150 KB somadas. Um visitante que instala o núcleo para testar HTTP paga 1,3 GB e dois imports que nunca serão executados; e como a fase 1 é declaradamente "só HTTP", esse é literalmente o primeiro comando que ele roda.

**E o `playwright` já está pronto para sair.** Verificado com os dois módulos bloqueados em `sys.meta_path` — não só no import, na **suíte inteira**:

```
block active: playwright unimportable
257 passed, 3 skipped in 6.44s          # --ignore nos 8 arquivos test_ui_*
```

Os 257 testes de caminho HTTP passam com `playwright` e `axe_playwright_python` **inexistentes no ambiente**. Os imports reais estão protegidos em `browser.py:229`, `browser.py:597` e `browser.py:709`, todos dentro de `try/ImportError`, e nunca no topo do módulo. Zero mudança de código — e a própria suíte é a prova de não-quebra do corte, então ela vira o gate da mudança (§5.3).

**O `faker` é o caso em que medir evitou trabalho inútil.** Ele é 140 KB e uma dependência (`tzdata`), não tem dependência transitiva, e `generate:` é capacidade **do núcleo**, não de produto (§2.2 F-d: o núcleo tem o motor, o provider tem os tipos). Tirá-lo exigiria adiar o import de `fixtures.py:3` — que não é impossível (testado: com os quatro bloqueados e 3 linhas adiadas, **16/16 módulos importam OK**), mas compra 140 KB. Não se paga.

**O `validate-docbr` sai por camada, não por peso.** CPF e CNPJ são documento **brasileiro** — é o mesmo vazamento de `Faker("pt_BR")` que a lista de edge cases do plano já registra. A fronteira correta não é "extra opcional" e sim "provider": um produto que emite nota fiscal quer CPF; um que testa billing em dólar não quer. Vai junto com `tests/test_fixtures.py`.

```toml
dependencies = [                      # núcleo: HTTP + geração de dado genérico
    "pydantic>=2.10", "pyyaml>=6", "httpx>=0.27",
    "fastapi>=0.115", "jinja2>=3.1", "uvicorn>=0.32",
    "python-multipart>=0.0.9",
    "faker>=30",                      # 140 KB; `generate:` é do núcleo
]
[project.optional-dependencies]
browser = ["playwright>=1.63", "axe-playwright-python>=0.1.8"]   # 1,3 GB, fase 3
dev = ["pytest>=8"]
# validate-docbr NÃO aparece aqui: vai para a distribuição heimdall-qa-nokr (§5.1)
```

**A regra que fica para o P4**, porque ela generaliza: ao decidir se uma dep vira extra, medir **o que ela obriga a baixar**, não o que ela ocupa no site-packages. Os dois números diferem por quatro ordens de magnitude neste caso.

### 5.3 Consequência para a fase 1

O corte de deps **é** fase 1 e não depende de nada: é a mudança de maior impacto de onboarding e menor risco do projeto inteiro. Vale ir antes do descriptor.

A prova de não-quebra do corte já existe e é a própria suíte:

```
PYTHONPATH=/tmp:src python -m pytest -q --ignore=tests/test_ui_*.py    →  257 passed, 3 skipped
```

com `playwright` bloqueado no `sys.meta_path`. É a rara situação em que o teste de regressão da mudança é executável **antes** da mudança: hoje ele passa e continuará passando, e passa a ser falsificável quando o `pyproject.toml` declarar `[browser]` (aí a ausência do browser deixa de ser hipótese e vira o estado normal da instalação).

E ele tem um efeito colateral útil para o gate §9.4: com o browser em extra, o passo 1 do gate (instalar num venv limpo e afirmar que `playwright` **não** importa) passa a ser um teste com sentido, em vez de uma tautologia. Antes do corte, a asserção seria sempre falsa.

### 5.4 Fonte única para as cópias duplicadas

Há três duplicações hoje, e todas as três se resolvem com geração:

| Duplicado | Hoje | Vira |
|---|---|---|
| skill | `.agents/skills/nokr-qa-round/SKILL.md` + `.cursor/skills/...` (divergiram) | fonte única + `bin/sync-skills` |
| `.bru` → request | Bruno é a fonte | adapter (§2.1 V11) |
| `rounds/` × `suites/` | duplicação apontada pelo estudo §5 | `flows/` no provider, referenciados |

Não é trabalho de fase 1 além do rename da skill; o resto é fase 2 (F4 consolidou casos, F6 consolidou o contrato do agente).

### 5.5 A superfície pública

O estudo §9 propõe `typing.Protocol` + entry points `harness_qa.providers`. Com o nome decidido, o grupo vira **`heimdall_qa.providers`**. F8 confirma o desenho e acrescenta o que o torna não-frágil:

- **Protocol, não classe base.** Coerente com o estudo §9 e com a constituição da NokrAPI (§15: interface só existe com N implementações previsíveis — aqui há o Nokr e o de brinquedo, então a abstração se paga).
- **Congelado por teste de contrato.** O risco listado no estudo §14 ("scanners e outros plugins quebrando a API") se mitiga publicando um teste que instancia um provider mínimo e exercita cada método. Esse provider mínimo **é** o de brinquedo (§9) — o mesmo artefato serve de exemplo e de contrato.
- **`schema_version` no descriptor e no `run.json`**, separados: o descriptor versiona o que o provider declara, o run versiona o que o núcleo grava. Um pode mudar sem o outro.

---

## 6. Compatibilidade e ordem do rename: o que o `import` precisa garantir

Se o núcleo e o provider são distribuições separadas mas o provider declara `heimdall-qa` como dependência, então toda mudança do núcleo é potencialmente breaking para o provider. Três regras:

1. **O núcleo não importa o provider, nunca** (T1/T2/T3, §1). Direção única.
2. **O provider declara faixa, não pin**: `heimdall-qa>=1,<2`. O núcleo só quebra contrato em major.
3. **Deprecação: 1 minor de aviso + 1 minor de carência**, nada removido em patch. Concretamente, um `DeprecationWarning` emitido pelo núcleo na primeira minor, remoção na terceira contando a partir do aviso. É o suficiente para o P3 do estudo §11 (segundo provider) sobreviver sem sincronizar releases.

---

## 7. Licença

**Apache-2.0 no núcleo.** A escolha entre Apache-2.0 e MIT para uma ferramenta de CI é quase empírica, e os dois critérios que decidem são:

- **Concessão de patente explícita.** MIT é silencioso sobre patentes: um contribuidor pode conceder permissão autoral e ainda assim deter patente sobre o mesmo código. Apache-2.0 fecha isso com uma concessão irrevogável e uma cláusula de retaliação. Num projeto que empresas vão rodar em pipeline — e cujo público provável inclui gente com portfólio de patentes — essa é a diferença prática.
- **Clareza de contribuição.** Apache-2.0 exige marcar arquivos modificados. Custo irrelevante, e ajuda o §5.1 (dois pacotes, histórico compartilhado).

MIT continua sendo a alternativa válida e **mais simples**: se a prioridade virar adoção máxima por projeto pequeno e a preocupação com patente não existir, MIT. O F8 fixa Apache-2.0 e registra o critério de reversão: se o atrito for de licença em adoção corporativa, o problema não é MIT-vs-Apache (ambos são permissivos e compatíveis com produto proprietário) — então não reabrir.

### 7.1 O achado de licença que precisa de ação

`axe-playwright-python` é **MIT**, mas **vendoriza `axe.min.js`, que é MPL-2.0** (Deque Systems). Verificado no header do arquivo instalado:

```
/*! axe v4.12.1
 * Copyright (c) 2015 - 2026 Deque Systems, Inc.
 * Your use of this Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. ...
```

MPL-2.0 é **copyleft fraco, no nível de arquivo**. Consequências, todas gerenciáveis mas nenhuma automática:

| Ponto | Situação | Ação |
|---|---|---|
| O núcleo pode ser Apache-2.0? | **Sim.** MPL-2.0 e Apache-2.0 são compatíveis: o arquivo MPL fica MPL, o resto fica Apache | nada |
| Precisamos relicenciar? | **Não.** MPL não contamina arquivos novos | nada |
| Precisamos **divulgar**? | **Sim, mas não no `NOTICE` do núcleo** — a obrigação é de quem redistribui o arquivo MPL, e a nossa wheel não o contém (§ linha seguinte) | seção de terceiros em `docs/`, por transparência; `NOTICE` só quando houver bundled bits |
| O arquivo é modificado? | **Não** — vem do pacote, sem patch | não há obrigação de publicar fonte própria; a do `axe-core` já é pública |
| O arquivo está na **nossa** wheel? | **Não.** `package-data` só inclui `serve/templates/*.html` e `serve/static/*`; o `axe.min.js` vem do `axe-playwright-python` em runtime | a wheel fica limpa; a obrigação viaja na cadeia de dependência |

**O que isso muda na prática:** o `axe.min.js` sai do caminho crítico da fase 1 (ele nem é instalado, §5.2), e como a nossa wheel não o contém, a obrigação de aviso **não** recai sobre o núcleo — ela aparece onde o pacote é redistribuído, isto é, no ambiente de quem instala o extra `[browser]`, já coberto pela licença do próprio `axe-playwright-python`. Ainda assim o F8 recomenda uma seção de terceiros no `docs/`: é uma linha de prosa que evita a pergunta "e o axe?" no dia da publicação.

A lição que vale registrar para o P4: **`pip install` é redistribuição**, e uma auditoria de licenças por dependência (FOSSA ou equivalente) é o passo que precede a publicação do estudo §11 P4. Hoje as deps são todas permissivas (MIT/BSD-3/Apache-2.0) **exceto** essa, e ela não é bundled. Nenhum bloqueio — mas é exatamente o tipo de detalhe que aparece no dia da publicação se ninguém olhar antes.

**Playwright** não declara `License` no `dist-info` (é Apache-2.0 no repositório). Não é bloqueio; entra na mesma auditoria do P4.

---

## 8. Versionamento de schema e política de deprecação

O estudo §8 diz: *"JSON é a fonte e markdown é uma renderização"*, com `run.json` versionado por `schema_version`. **Isso não existe hoje** — verificado:

```
runs/latest/
  book.json   evidence.md   round.yaml   steps/   summary.json
```

Não há `run.json`, e `evidence.md` **é a fonte**, não a renderização. O `book.json` guarda o livro-razão do run, `summary.json` o veredito, `round.yaml` uma cópia do input. Três formatos parciais em vez de um documento.

**Decisão de F8:** implementar `run.json` como documento canônico (`schema_version: 1`), com `evidence.md` passando a ser **gerado a partir dele**. Isso é pré-requisito de qualquer integração externa (o próprio estudo §8 diz que ninguém integra sem isso) e é o que permite ao review UI de F7 ler um artefato estável em vez de montar a view a partir de arquivos dispersos.

| Artefato | Versão | Quem rompe |
|---|---|---|
| `run.json` | `schema_version: 1` | o núcleo; mudança de versão = entrada de migração |
| `qa/project.yaml` | `version: 1` | o provider |
| `Provider` Protocol | congelado por teste | o núcleo, só em major |
| `summary.json` / `book.json` | **entram em `run.json`** e saem como arquivos autônomos | — |

**A regra que o F6 motivou.** F6 mostrou que 9 de 21 regras em prosa não são aplicadas por código. Um `schema_version` que existe no papel e não é lido por ninguém é a mesma classe de problema. Portanto: o núcleo **deve recusar** um `run.json` com `schema_version` desconhecida, com mensagem acionável, em vez de tentar ler. É barato e é o que transforma o número em contrato.

---

## 9. O provider de brinquedo

O estudo §11: *"deve ser deliberadamente barato e burro, não um segundo produto real. Um alvo de 20 telas que ninguém mantém é melhor evidência arquitetural que um produto inteiro que ninguém consegue rodar."*

E o estudo §14: *"o risco maior é abrir o projeto antes de o segundo provider existir. Isso transforma 'agnóstico' em slogan."*

### 9.1 Desenho

```
examples/toy-provider/
├── qa/project.yaml        # 12 linhas — descriptor nível 0 (F2 §5.3)
├── mock/main.py           # FastAPI, 3 rotas, ~50 linhas, sem browser, sem Nokr
├── contracts/things.yaml  # 1 contract
├── cases/things/things.yaml  # 3 casos (F4: consolidado num arquivo)
└── suites/smoke.yaml
```

O mock tem **três rotas e três casos**, escolhidos para exercitar o núcleo sem exercitar domínio:

| Caso | O que prova do núcleo |
|---|---|
| `POST /v1/things` → 201 | happy path, envelope de resposta declarado no descriptor |
| `POST /v1/things` sem campo → 400 | `errors.validation_status` **declarado** (o plano já registrou: 400 vs 422 varia por framework) |
| `GET /v1/things/{id}` sem API key → 401 | `routes[].auth` declarado |

Nada de dinheiro, nada de escala, nada de `HALF_EVEN`. Se o provider de brinquedo precisar de qualquer um desses para rodar, o núcleo ainda tem domínio dentro — é precisamente o que ele existe para detectar.

**O mock é local e não usa rede.** Um alvo público (`httpbin`, `petstore`) seria mais barato de escrever e pior como gate: introduz rede, flakiness e uma dependência de terceiros no CI do exemplo. FastAPI já é dependência do núcleo (§5.2), então o mock custa zero dependência nova.

### 9.2 Por que ele é também o teste de contrato

`mock/main.py` + `qa/project.yaml` são, ao mesmo tempo:

- o **exemplo** que um visitante copia para escrever o próprio provider (o estudo §12: "o exemplo canônico");
- o **provider mínimo** que o teste de contrato do `Provider` instancia (§5.5);
- o **alvo do T3** (§1): instalar só o núcleo e rodar contra ele.

Um artefato, três papéis. É o que justifica escrevê-lo cedo em vez de depois.

### 9.3 O que ele **não** é

Não é o segundo provider real. O estudo §11 P3 quer "um provider de segunda UI (o site, ou um app de brinquedo)" — e a recomendação explícita é o de brinquedo. O `nokr-website` continua existindo como alvo real futuro, mas ele não pode ser o gate: ele tem manutenção, tem estado, e um gate que depende de um produto real não é executável por um terceiro.

### 9.4 Gate

```
# 1. wheel limpa, sem Nokr em disco, sem browser instalado
python -m venv /tmp/gate && /tmp/gate/bin/pip install dist/heimdall_qa-*.whl
test ! -d ../NokrAPI                       # nada de produto no ambiente
/tmp/gate/bin/python -c "import playwright" && exit 1   # browser NÃO instalado

# 2. o exemplo roda
/tmp/gate/bin/heimdall-qa validate
/tmp/gate/bin/heimdall-qa run examples/toy-provider/suites/smoke.yaml
test -f examples/toy-provider/runs/latest/run.json           # §8: a fonte é JSON
rg -q 'verdict' examples/toy-provider/runs/latest/summary.json

# 3. o run é navegável sem o produto
/tmp/gate/bin/heimdall-qa last-run
```

Falsificado por: o passo 1 falhar (núcleo depende do provider — §1 T3), ou o passo 2 falhar (o núcleo não é utilizável sozinho — §1.1).

**Status do gate: ele é o gate de *saída* da fase 1, e não roda hoje.** Verificado: os três subcomandos existem (`validate`, `run`, `last-run` — `nokr-qa --help`), mas `config.py:50-51` ainda tem `nokr_web`/`nokr_admin` com defaults de produto, e o descriptor de F2 não existe. O passo 2 falharia agora por construção — o que é exatamente o ponto: o gate mede o trabalho que a fase 1 tem de fazer, e hoje ele é vermelho.

**E o gate complementar, que é o que fecha o desenho:** rodar o provider Nokr **sobre o núcleo novo** com os 539 cases verdes e `git diff campaigns/` vazio — que é a prova de não-quebra da fase 1 no plano. Os dois juntos são a definição operacional de "genérico": o núcleo roda sem o Nokr, e o Nokr roda sobre o núcleo.

---

## 10. ADRs desta frente

| ADR | Decisão | O que a falsifica |
|---|---|---|
| **ADR-05** | Duas distribuições (núcleo + provider) num repo, com o provider em `providers/nokr/` fora do `src/` do núcleo | um terceiro provider precisar de código no núcleo para existir |
| **ADR-06** | O gate de genericidade é T3 (wheel limpa num venv sem produto), não o grep | T3 passar e o núcleo ainda depender conceitualmente do Nokr (pego por §1.1) |
| **ADR-07** | Nome: `heimdall-qa`, registrado no PyPI antes do P4; nunca o nome curto | colisão de nome ou reclamação de marca na publicação |
| **ADR-08** | Inglês no núcleo, português no provider, README do provider em inglês | um terceiro contribuidor travar em documentação do núcleo |
| **ADR-09** | Apache-2.0 | exigência de compatibilidade com GPL-2.0 (o estudo §14 não prevê) |
| **ADR-10** | `run.json` com `schema_version` é a fonte; `evidence.md` é renderização; versão desconhecida é recusada | nenhum consumidor externo precisar do `run.json` |
| **ADR-11** | `playwright` + `axe-playwright-python` saem para o extra `[browser]` (1,3 GB, zero mudança de código); `faker` **fica** (140 KB, sem ganho mensurável); `validate-docbr` vai para o provider por camada, não por peso. Critério: medir o download obrigado, não o pacote | o caminho HTTP precisar do browser, ou um caso genérico precisar de CPF |

---

## 11. O que F8 não decide

- **Onde o descriptor mora** no repo do produto e como ele é descoberto — é F2, e o F8 só assumiu `qa/project.yaml`.
- **A forma do `run.json`** — o F4 desenhou índice e navegação; o F8 só fixou que ele existe e é versionado. O schema literal é implementação.
- **O desenho do review** — é F7, e o único ponto de contato é que o review lê `run.json` (§8).
- **Se o repo é renomeado.** O F8 decide nome de pacote, binário e distribuição. Renomear o repo `nokr-qa` → `heimdall-qa` é operação de publicação (P4) e não muda nada na fase 1; o provider pode continuar morando no repo atual.
- **A extração do provider para repo próprio.** Deliberadamente adiada ao P4, com o custo registrado em §5.1.
