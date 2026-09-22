# Harness agnóstico de superfície — estudo para produto open source

> **Status: ESTUDO.** Não é spec vigente, não é emenda, e nada aqui está implementado.
> Complementa — não substitui — [`emenda-11-ui-browser.md`](emenda-11-ui-browser.md), que continua sendo a instanciação Nokr.
> Registrado em 15/09/2026.

---

## 0. Duas perguntas diferentes

A emenda 11 responde: *como testar a superfície do dashboard Nokr dentro deste harness.*

Este documento responde outra: *como fazer este harness servir para **qualquer** superfície, de qualquer produto.*

A segunda é super-sintética da primeira. Mas isso **não** significa começar por aqui. Ver §11: a ordem barata é implementar a emenda 11 primeiro e generalizar depois, cortando pela costura que a implementação revelar. Cortar uma abstração ao longo de uma linha que já existe é ordens de magnitude mais barato que projetá-la no escuro.

O motivo de existir é o objetivo declarado de abrir o projeto. Hoje o harness é excelente num domínio que não é o dele: ele sabe o que é `POST /api/ingest`, o que é `nk_test_`, o que é rate card e o que é escala 5 com `HALF_EVEN`. Nada disso é conhecimento de harness — é conhecimento do produto, e está no caminho crítico.

### O que existe de comparável

| Projeto | O que ensina para este desenho |
|---|---|
| **Maestro** (mobile + web, OSS) | Dirige o app **a distância**, pela árvore de acessibilidade, sem instrumentar o produto. É a prova de que "agnóstico de stack" não é retórica: o mesmo Flow roda em nativo, React Native e Flutter. A decisão de usar ARIA como base é a mesma. |
| **Tracetest** | Um teste, N ambientes, via *environments* + *variable sets*. É o modelo de §7: o ambiente é dado, não código. |
| **pytest / pluggy** | Como um harness Python se deixa estender por terceiros sem virar framework: entry points e hooks. É o modelo de §9. |

---

## 1. Princípio: três camadas, uma regra

| Camada | Responsabilidade | Quem escreve |
|---|---|---|
| **Núcleo** | Executar passos, correlacionar `trace_id`, gravar artefatos, avaliar packs, emitir veredito, revisar | O projeto |
| **Provider** | Como falar com a superfície de *um* produto; o que conferir nele | Quem integra o produto |
| **Conteúdo** | Os fluxos, os dados e os oráculos de *uma* instalação | O time que usa o harness |

**A regra que sustenta tudo:** o núcleo não conhece **nenhum** identificador do produto. Hoje o núcleo conhece `ingest`, `metering`, `nk_test_`, `X-Nokr-Environment` e `HALF_EVEN` — todos vazamentos de camada.

Corolário prático: se remover o provider deixar o núcleo sem conseguir importar, houve vazamento.

A generalização do passo `ui` é o teste de fogo dessa regra. Um `CaseFile` que declara `path: /overview` já está acoplado a um produto: o caminho é dado do produto, não do harness. O que o núcleo precisa saber é "abra a **referência** `overview`", e quem resolve `overview` → `/overview` é o provider.

---

## 2. Front matter (`UI.md`)

Front matter YAML, não bloco de configuração do núcleo. Razão: **versionamento**. O descriptor é artefato do produto testado; precisa de história git própria, revisão de diff e `CODEOWNERS` do time dono. Se ele mora dentro da config do harness, o harness passa a versionar junto com o produto — acoplamento de release.

Colocação:

```
<repo do front>/
  UI.md                  # front matter + Mapa de superfícies
  src/...
```

O harness lê `provider:*` do front matter e `provider:*` do alvo (round, campanha ou `.bru`). **Provider vence** sobre o front matter, para que (a) um round possa apontar para outro provider e (b) uma `.bru` — que já é por endpoint — resolva o provider dela mesma, possibilitando **um round único cobrindo vários repos**, que é exatamente o caso Nokr (`nokr-ui-lib` hoje, `nokr-website` e outros amanhã).

Esse é o mesmo mecanismo que permite campanhas de superfície multi-app sem inflar o YAML: as `.bru` em pastas de fluxos diferentes carregam front matter diferente.

---

## 3. O que é core e o que é provider

| Concern | Núcleo | Provider |
|---|---|---|
| Resolver um passo para uma operação | ✅ | |
| Executar (HTTP, browser, SQL, fila) | ✅ | |
| Correlacionar `trace_id` e coletar logs | ✅ | |
| Gravar a pasta do run | ✅ | |
| Avaliar packs | ✅ | |
| Veredito, review, análise | ✅ | |
| Fetch de fact, cache, TTL | ✅ | |
| Gerar dado sintético | ✅ (motor) | ✅ (tipos e validações) |
| **URL e rotas** | | ✅ |
| **Auth e formatos de resposta** | | ✅ |
| **Como ler um valor renderizado** | | ✅ |
| **O que significa "bateu"** | parcialmente (§6) | ✅ |
| **Limpeza e setup de estado** | | ✅ |

Nenhuma dessas linhas é "o núcleo chama o provider". Todas são "o provider **contribui** e o núcleo consome", o que preserva a direção de dependência: **o núcleo nunca importa código de produto; o produto importa o núcleo.**

---

## 4. O descriptor de superfície (o contrato do provider)

```yaml
# front matter de UI.md
provider: acme-billing
kind: web                      # web | http_only | api | mobile | desktop
base_url: ${env:ACME_URL}
trace:
  header: X-Trace-Id
  propagation: header          # header | query | none
  sources: [web, worker]       # arquivos de log correlacionáveis
auth:
  strategy: form               # form | storage_state | bearer | api_key | basic | custom
  flow: login                  # ref para um fluxo nomeado (§5)
  bootstrap_local_storage:
    acme_selected_environment: sandbox
  secrets: [email, password]
pages:
  overview:
    path: /overview
    waits_for: { method: GET, path: /api/dashboard/metrics }
    regions:
      kpis: { role: main }     # recorte do ARIA snapshot
    values:
      gross_volume: { selector: "[data-kpi='gross_volume']" }
    reveal:
      withdraw: { action: click, target: { role: button, name_key: financial.withdraw } }
  login:
    path: /auth/login
    fields:
      email:    { label_key: auth.email }
      password: { label_key: auth.password }
runtime:
  browser: chromium
  viewport: { width: 1440, height: 900 }
  timeouts: { page_ms: 15000, value_ms: 30000 }
checks:
  money_debit: { type: decimal_delta, scale: 5, rounding: HALF_EVEN }
fixtures:
  - facts
  - faker
```

Pontos de projeto:

**`kind` dirige a **decoração**, não o núcleo.** Um provider `kind: web` registra os packs `ui.*`; um `kind: mobile` registraria `mobile.*`. O núcleo executa o passo igual nos dois casos.

**`waits_for` substitui o `sleep`.** É o achado do Maestro que mais vale copiar: esperar pela condição observável, nunca por tempo. Condição de rede quando há rede; seletor quando não há.

**`name_key`, não `name`.** Os fluxos nunca casam texto renderizado, e sim a **chave de i18n**. Assim o mesmo fluxo roda em `pt-BR` e `en-US` sem duplicação — e a tradução deixa de ser motivo de quebra de teste. A resolução de chave → texto vem do adapter, que pode carregar o dicionário do produto ou casar por regex tolerante.

**`reveal` é declarativo.** Onde o valor está atrás de clique (drawer, modal, aba), o mapa diz como revelá-lo. Sem isso, a leitura vira roteiro de cliques dentro do YAML de teste — que é o que se quer evitar.

**`bootstrap_local_storage` explícito.** Hoje o Nokr guarda `nokr_selected_environment` em `localStorage`. Isso é dado do produto e pertence ao descriptor.

---

## 5. Passos: fluxos nomeados e referências

Nenhum nome de produto no YAML de conteúdo:

```yaml
steps:
  - ui: { open: login, as: session }                          # fluxo nomeado, provider resolve
  - ui: { open: catalog, capture: { metric_id: "@metric_id" } }
  - ref: <provider>.<flow>                                     # refs para fluxos reusáveis
  - http: { ref: ingest.accept, body: {...} }
  - ui: { open: overview, read: { gv: "@gross_volume" } }
  - assert:
      left: "@gv"
      right: { api: overview.gross_volume }
      check: equal
```

Três decisões:

**`open` / `read` em vez de `path` / `jsonpath`.** O fluxo nomeia o que quer; o provider sabe onde está. Um rename de rota deixa de quebrar 40 arquivos de teste.

**Refs de fluxo** (`flows/` no provider) eliminam a duplicação que hoje existe entre `cases/` do Trilho A e `steps/` das suites. Um fluxo é definido uma vez e referenciado por N rounds.

**`check:` é nome de verificação registrada** (§6), não um operador embutido. `equal`, `delta_exact`, `unchanged` são do núcleo; `money_debit` é do provider.

---

## 6. Verificação: a parte que realmente não generaliza

Esta é a seção mais importante do documento, porque é onde a generalização ingênua quebra.

O harness hoje tem um oráculo **de dinheiro**: arredondamento `HALF_UP` e `HALF_EVEN` na escala 5, exclusão de replay, exclusão de 402, exclusão de `INSUFFICIENT`. Isso é *conhecimento do produto*. Um harness genérico **não pode** embutir isso, e também não pode fingir que "o número mudou" é uma verificação.

A saída é separar **aritmética** de **domínio**:

**Núcleo — verificações genéricas:**

| `check` | Semântica |
|---|---|
| `equals` | igualdade exata |
| `delta_exact` | `lido − antes == esperado`, com escala declarada |
| `delta_positive` | `lido > antes` (opcionalmente `>= min_delta`) |
| `unchanged` | `lido == antes` |
| `present` / `absent` | existência |
| `matches_schema` | conformidade estrutural |
| `count_delta` | variação de cardinalidade |

**Provider — verificações de domínio:** registradas por nome, com três exigências não negociáveis:

1. **Puras.** Não fazem I/O, não leem relógio, não consultam banco. Recebem números, devolvem veredito. Testáveis sem subir nada.
2. **Sobre valores coletados.** A coleta é do núcleo; a interpretação é do provider.
3. **Declaradas no descriptor, não no código de fluxo.** Assim a mesma verificação vale para todos os fluxos do produto.

Exemplo: `money_debit: { type: decimal_delta, scale: 5, rounding: HALF_EVEN }` é o pack `values.oracle` do Nokr, agora parametrizado em vez de codificado. Um produto com estoque escreveria `stock_delta: { type: integer_delta, allow_negative: false }`; um com pontos de fidelidade, `points_balance: { type: decimal_delta, scale: 2, rounding: HALF_UP }`.

**O que o núcleo retém:** a *mecânica* do oráculo — foto antes, lote, espera com retry, timeout que **falha** em vez de pular, e a lista de exclusões como dado. Isso é infraestrutura de teste e vale para qualquer domínio. O que sai é o significado.

---

## 7. Multi-provider e multi-ambiente

Um run pode tocar mais de um provider — o backend pelo provider `http`/`api`, o front pelo provider `web`. Isso não é complicação: é a **premissa** do produto (§0), porque a pergunta que ele responde ("a tela mostra o que a API respondeu?") é por natureza atravessa-camadas.

O ambiente é **dado**, não código:

```yaml
# environments/sandbox.yaml
provider: acme-billing
base_url: http://localhost:4200
api_url:  http://localhost:8080
secrets:  [email, password]
```

O mesmo fluxo roda em `sandbox` e `production` trocando o environment — o modelo do *variable set* do Tracetest. Uma instalação do harness pode hospedar N produtos × M ambientes sem que nenhum YAML de fluxo mencione host.

**Isolamento de namespace é regra do núcleo, não do provider.** Se o produto tem sandbox e produção na mesma conta, quem garante que o dado não cruza é o harness — e ele deve falhar duro quando cruza.

---

## 8. Saída como dado

Hoje o veredito sai como markdown mais pastas. Para um produto, **JSON é a fonte e markdown é uma renderização**.

- `run.json` — o run inteiro num documento, versionado por um `schema_version` explícito.
- `junit.xml` — para CI de terceiros consumir sem conhecer o harness.
- `evidence.md` / `analysis.md` — ficam como renderização do flagship; o core não os exige.
- Link de trace do Playwright quando existir.

Sem isso nenhuma integração externa acontece sem parsing de markdown, que é onde projetos de teste morrem.

---

## 9. Extensão por terceiros

API de plugin na v1, para não virar pluggy às pressas na v2:

```toml
# pyproject.toml de um provider de terceiro
[project.entry-points."harness_qa.providers"]
acme = "acme_harness.provider:AcmeProvider"
```

```python
class Provider(Protocol):
    id: str
    kind: str                                        # web | http_only | mobile | desktop
    def resolve(self, ref: str) -> PageRef | FlowRef | HttpRef: ...
    def checks(self) -> dict[str, CheckFn]: ...
    def fixtures(self) -> dict[str, Callable[..., Any]]: ...
    def decorate(self, packs: PackRegistry) -> None: ...
```

Tudo em `typing.Protocol` — sem classe base obrigatória, sem herança. É a única forma de o núcleo definir contrato sem acoplar tipos, e resolve o problema que a constituição da NokrAPI já reconhece: a proibição de interface com uma implementação só. Aqui há N implementações previsíveis, então a abstração se paga.

**Superfície pública, garantida por teste:** as assinaturas acima ficam cobertas por teste de contrato. Publicar uma promessa que quebra em duas versões é pior que não publicar.

---

## 10. O que muda no núcleo atual

| Peça | Hoje | Vira |
|---|---|---|
| `schema/models.py` | pydantic por tipo de conteúdo | modelos de conteúdo sem identificador de produto + `ProviderRef` |
| `runner.execute_step` | sabe resolver `.bru`, contrato e rota | despacha por `kind` para o adapter |
| `packs/__init__.py` | lista fixa, ~460 linhas | `PackRegistry` com contribuição do provider |
| `oracle/` | `money.py` com `HALF_UP`/`HALF_EVEN` | aritmética genérica; o domínio vai para o provider Nokr |
| `fixtures.py` | `Faker("pt_BR")`, CPF/CNPJ | motor de dado sintético + geradores contribuídos |
| `config.py` | `nokr_web`, `nokr_admin`, log files da Nokr | descritores de superfície; paths resolvidos pelo provider |
| `logs/collector.py` | assume dois arquivos da Nokr | fontes declaradas em `trace.sources` — **já é quase agnóstico** |
| `serve/` | workspace da coleção Nokr | navegador de run multi-conteúdo |
| nome | `nokr-qa` | §15 |

O ponto encorajador: `logs/collector.py` já é agnóstico de fato. A correlação por `trace_id` nunca soube o que era a Nokr.

O ponto caro: `packs/` e `oracle/` são onde o domínio está mais entranhado, e é por isso que a emenda 11 deve ser implementada antes — ela obriga a separar "avaliar" de "significar" no único lugar onde isso é difícil.

---

## 11. Caminho de migração (conviver, não reescrever)

Nenhuma fase quebra o uso atual. Cada uma é reversível.

| Fase | Movimento | Prova de que não quebrou |
|---|---|---|
| **P0** | Extrair o descriptor: a Nokr vira o **primeiro provider**, com o comportamento atual congelado | Suíte de ouro: os runs de hoje reproduzem byte a byte |
| **P1** | Trocar identificadores internos por refs (`overview`, não `/overview`) | Os YAML de fluxo perdem os primeiros nomes de produto |
| **P2** | Verificações plugáveis; `money_debit` sai do núcleo e vira provider | `values.oracle` continua verde com a aritmética que já existe |
| **P3** | Registry por entry point + um provider de segunda UI (o site, ou um app de brinquedo) | Um segundo provider passa sem alterar o núcleo |
| **P4** | Empacotar e publicar | Instalação limpa roda o provider de brinquedo |

**P3 é o gate de verdade.** Enquanto só existir o provider Nokr, "agnóstico" é aspiração. O segundo provider é o que prova — e por isso ele deve ser deliberadamente **barato e burro**, não um segundo produto real. Um alvo de 20 telas que ninguém mantém é melhor evidência arquitetural que um produto inteiro que ninguém consegue rodar.

**Critério de reversão:** se P0 não conseguir reproduzir a suíte de ouro, o desenho está errado e o caminho para.

---

## 12. Nokr como implementação de referência

O valor comercial não muda com a abertura: o projeto continua sendo o que prova que o Nokr funciona de ponta a ponta. O que muda é que ele deixa de ser um caso especial e passa a ser o **exemplo canônico** — a instanciação que qualquer pessoa copia para escrever o próprio provider.

Isso tem uma consequência de projeto útil: sempre que houver dúvida sobre onde uma peça deve morar, a resposta é "onde o exemplo canônico fica mais claro".

E uma consequência de disciplina: enquanto o Nokr for o único provider, o desenho não deve crescer em abstração. Abstração sem segundo consumidor é dívida, não arquitetura.

---

## 13. Decisões em aberto

| # | Decisão | Opções | Recomendação |
|---|---|---|---|
| 1 | Sintaxe de fluxo | Payload aninhado por tipo de step vs **lista plana de steps tipados** | Lista plana. Renderiza melhor no review, valida melhor no `validate`, não precisa de regra de mutual exclusion (que já existe no `SuiteStep` atual). |
| 2 | Aritmética das verificações | Funções vs **dado declarativo** no descriptor | Dado. `validate` consegue checar antes de executar; `+1/-1` como símbolo é lido por humano. |
| 3 | Granularidade de pacote | Monolito com extras vs **núcleo + provider separado** | Núcleo + provider separado. É o que torna a fronteira falsificável. |
| 4 | Como o provider cruza as camadas | Reimplementar vs **adapter que delega** | Delegar. Reimplementar duplica (e o Nokr já tem ~2200 linhas de harness). |
| 5 | BDD/Gherkin | Traduzir para Gherkin vs **YAML tipado** | YAML tipado. O valor do projeto é o artefato do run e os packs, não a sintaxe; um `kind: bdd` pode virar provider depois. |
| 6 | Escopo do passo | Só `ui` vs **`mobile` e `desktop` também** | `ui` só na v1, com `kind` já no descriptor para não fechar a porta. |

---

## 14. Riscos

| Risco | Por que dói | Mitigação |
|---|---|---|
| Generalizar antes do segundo consumidor | Abstração que ninguém validou; o caso Nokr fica mais lento sem ganho | P3 é gate: sem segundo provider, não sobe abstração |
| Projeto genérico com a primeira feature ainda frágil | Publicar e queimar reputação | P4 só depois de P3 |
| O core herda o domínio por osmose | Um `if kind == "money"` no núcleo e a fronteira morreu | Teste de arquitetura: o core não pode importar nenhum provider |
| Custo de manter N produtos | O harness genérico é mais caro que o específico, por definição | Um produto pago (Nokr) e N gratuitos; a abstração tem de pagar por si no primeiro |
| Scanners e outros plugins quebrando a API | Contrato público sem invariante | `Provider` congelado por teste de contrato; `schema_version` na saída |

O risco maior não está na lista: é abrir o projeto antes de o segundo provider existir. Isso transforma "agnóstico" em slogan.

---

## 15. Nome

`nokr-qa` é um nome de produto amarrado a outro produto. Serve enquanto for interno; não serve como projeto aberto, porque sugere ao visitante que o harness só funciona com o Nokr — exatamente o contrário do objetivo.

Três saídas, em ordem de preferência:

1. **Pacote neutro + `nokr-qa` como provider.** O núcleo tem nome neutro (`harness-qa` ou equivalente) e a Nokr vira um provider publicado à parte, em `nokr-qa`. É a opção que faz o README provar a tese por si só: o exemplo canônico é um provider externo.
2. **Renomear o pacote e manter o repo.** Mais barato, e o repo continua o ateliê.
3. **Manter o nome.** Aceitável só se a intenção de abertura for de longo prazo e não houver pressa.

A escolha 1 é a única que obedece à §1 sem exceção. As outras duas deixam o núcleo com o nome do primeiro consumidor.

---

## 16. O que este documento não faz

- Não altera `emenda-11-ui-browser.md`. Essa continua a instanciação Nokr e é o insumo deste estudo.
- Não propõe reescrever o harness. A §11 é aditiva e reversível.
- Não decide se o projeto será aberto. Ele assume a intenção declarada e desenha para ela.
- Não cobre mobile e desktop além da porta deixada em `kind`.
