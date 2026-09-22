# F9 — Consolidação: ADRs, edge cases e o plano de implementação

> **Frente:** F9 do [plano de estudo](../../../../../../../.cursor/plans/heimdall_qa_plano_de_estudo_eb1f8620.plan.md).
> **Pergunta:** qual é a sequência de PRs que entrega tudo isso sem quebrar o uso atual?
> **Entregáveis:** o plano de implementação, as ADRs de F1–F8 reunidas e o registro de edge cases.
> **Método:** a disciplina do estudo §11 — cada fase aditiva e reversível, com prova de não-quebra por comando.
> **Data:** 22/09/2026.

---

## 0. O que este documento é

É a síntese das nove frentes. Não introduz nenhuma decisão nova: reúne as que já foram tomadas, indexa cada uma pelo que a **falsifica**, e ordena o trabalho em fases em que cada gate é um comando que passa ou não passa.

Três regras de leitura:

- **Nada aqui é "olhar e achar bonito".** Cada gate é um comando, uma fixture ou um número medido. Onde não consegui expressar um gate assim, está dito.
- **Onde uma frente contradisse o plano, vale a frente.** As medições corrigiram o enunciado do plano em quatro pontos (§3.1), e a correção está registrada em vez de silenciada.
- **O que ficou em aberto está em §6**, com o motivo de não ter sido decidido aqui.

As nove frentes: [00-baseline](00-baseline.md) · [01-mapa-acoplamento](01-mapa-acoplamento.md) · [02-descriptor-projeto](02-descriptor-projeto.md) · [03-auto-discovery](03-auto-discovery.md) · [04-storage-navegacao](04-storage-navegacao.md) · [05-logs-distribuidos](05-logs-distribuidos.md) · [06-contrato-agente](06-contrato-agente.md) · [07-ui-ux](07-ui-ux.md) · [08-rename-provider](08-rename-provider.md).

---

## 1. As ADRs

Onze decisões, cada uma com a frente que a produziu e o que a **falsifica**. Uma decisão sem falsificador é uma opinião; as colunas da direita são a parte útil.

| ADR | Frente | Decisão | O que a falsifica |
|---|---|---|---|
| **01** | F2 | O descriptor é **do alvo**, com precedência sobre o do harness: `<alvo>/qa/project.yaml` vence, `providers/<id>/` é fallback | um projeto precisar de dois descriptors para rodar, ou o harness precisar de campos que só o alvo sabe |
| **02** | F3 | A fonte do contract passa a ser o **schema** (OpenAPI 3.1), com o DTO como fonte adicional — não obrigatória | a derivabilidade real ficar muito abaixo dos 48,8% medidos (§3.1) e o gerador não pagar o próprio custo |
| **03** | F5 | O **fallback por janela de tempo é removido**. Marcador ausente ⇒ `logs_incomplete` com razão explícita, zero linhas fabricadas | aparecer um caso legítimo em que a correlação por proximidade temporal é a única disponível |
| **04** | F7 | **Um renderizador, dois transportes**: Jinja server-rendered; o export estático é uma segunda saída do mesmo renderizador | a interatividade exigida pelo review (F7 §3) não caber em HTML estático |
| **05** | F8 | **Duas distribuições num repo**: `heimdall-qa` (núcleo) + `heimdall-qa-nokr` (provider), com o provider em `providers/nokr/` **fora** do `src/` do núcleo | um terceiro provider precisar de código dentro do núcleo para existir |
| **06** | F8 | O gate de genericidade é **T3** (wheel limpa num venv sem produto), não o grep | T3 passar e o núcleo ainda depender conceitualmente do Nokr (§1.1 de F8) |
| **07** | F8 | Nome: **`heimdall-qa`**, registrado no PyPI antes do P4; nunca o nome curto `heimdall` | colisão de nome ou reivindicação de marca na publicação |
| **08** | F8 | **Inglês no núcleo, português no provider**, README do provider em inglês | um contribuidor externo travar na documentação do núcleo |
| **09** | F8 | **Apache-2.0** | exigência real de compatibilidade com GPL-2.0 (que o Apache-2.0 não satisfaz) |
| **10** | F8 | `run.json` com `schema_version` é **a fonte**; `evidence.md` é renderização; versão desconhecida é **recusada** | nenhum consumidor externo precisar do `run.json` |
| **11** | F8 | `playwright`+`axe` → extra `[browser]` (1,3 GB, zero mudança de código); `faker` **fica**; `validate-docbr` vai para o provider | o caminho HTTP precisar do browser, ou um caso genérico precisar de CPF |

**As três ADRs que carregam o desenho inteiro.** 05 (a fronteira), 02 (a fonte do contract) e 10 (a saída como dado). As outras oito são consequência ou infraestrutura.

**A que tem o falsificador mais fraco.** ADR-09 (licença). Nenhum cenário previsto produz uma exigência de compatibilidade com GPL-2.0, o que na prática significa que ela não será reaberta — e uma decisão que não pode ser falsificada é uma decisão que não pode ser revisada. Fica registrada assim mesmo, porque o custo de mudar depois é maior que o de mantê-la.

### 1.1 Ordem de dependência entre as ADRs

```
01 descriptor ──> 02 fonte do contract ──> 03 logs
   │                     │
   └──> 05 duas distribuições ──> 06 gate T3 ──> 07 nome
              │                        │
              └──> 11 corte de deps    └──> 10 run.json
04 renderizador (independente)      08 idioma    09 licença
```

Quatro raízes: 01, 04, 09 e 07. Nenhuma ADR depende de 04 — a UI é folha em todas as direções, que é o que o F7 concluiu (o review não bloqueia nada).

---

## 2. As fases

> **O plano executável é [`../plano-implementacao-heimdall.md`](../plano-implementacao-heimdall.md).** Esta seção é o resumo de origem — a tabela por onde as fases nasceram. O documento irmão expande cada passo no formato da casa (Objetivo / Arquivos / Não fazer / Aceite / Verificar / Gate), nomeia os arquivos que mudam em cada um e traz a tabela de PRs. **Para executar, use o irmão; para entender a decisão, use esta seção.**

A ordem do plano é **REST primeiro, navegador por último**. As fases são aditivas e reversíveis; a coluna de gate é um comando.

### Fase 1 — Núcleo agnóstico HTTP

| # | Movimento | Gate (comando) |
|---|---|---|
| 1.0 | **Corte de deps** — `playwright`+`axe` → `[browser]` | `pytest --ignore=tests/test_ui_*.py` com `playwright` bloqueado ⇒ **257 passed, 3 skipped** (§5.2 de F8) |
| 1.1 | **Descriptor** (ADR-01): formato, dois níveis, resolução | `heimdall-qa validate` sobre o descriptor do **provider de brinquedo** (API não-Nokr) resolve base_url, auth e rotas |
| 1.2 | **Adapter HTTP sobre o descriptor**: V1, V2, V3, V6, V7, V9, V10, V11 de F1 §2.1 | os 539 cases rodam pelo caminho novo |
| 1.3 | **Migrar o provider Nokr** | idem, com `git diff campaigns/` **vazio** |
| 1.4 | **Partir o pacote** (ADR-05): `providers/nokr/` fora do `src/` | `heimdall-qa` instala num venv sem o provider e `--help` funciona |
| 1.5 | **Rename por último** (`nokr_qa` → `heimdall_qa`, 761 ocorrências) | diff mecânico; suíte verde sem mudança de comportamento |
| 1.6 | **Docs do núcleo em inglês** (ADR-08), ~500-600 linhas | README + `docs/architecture.md` legíveis sem PT-BR |

**Gate de saída da fase 1:** os dois lados de F8 §9.4 — o **provider de brinquedo** roda numa wheel limpa sem Nokr em disco (§9.4), **e** o provider Nokr roda sobre o núcleo novo com `git diff campaigns/` vazio.

**Por que 1.0 vem antes de 1.1.** Não é ordem lógica, é ordem de valor: é a mudança de maior impacto de onboarding e menor risco do projeto inteiro, e o gate dela **já passa hoje e continuará passando** (§5.3 de F8). Não depende de nenhuma decisão de desenho, então não há razão para esperar.

**Por que 1.5 vem depois de 1.4.** O rename toca 1.432 ocorrências (§0 de F8). Fazê-lo antes obrigaria a revisar o mesmo diff duas vezes, misturando ruído mecânico com mudança de comportamento nos arquivos que o provider também vai reescrever.

### Fase 2 — As alavancas

| # | Movimento | Gate (comando) |
|---|---|---|
| 2.1 | **Auto-discovery** (ADR-02): fonte OpenAPI, `dto` opcional | um round gerado do `openapi-ingest-3.1.yaml` roda **sem o agente escrever YAML mecânico** |
| 2.2 | **Storage** (F4): consolidação de casos, na ordem do disco | 539 arquivos → 47; busca permanece sub-segundo sem índice |
| 2.3 | **Logs** (ADR-03): `log_sources[]`, `role: sync\|async`, fallback de relógio **removido** | nenhum fallback por relógio local no código; o defeito de F5 (worker async) coberto por teste |
| 2.4 | **Contrato do agente** (F6): 9 regras → `validate`, 5 apagadas | partida de **27.442 → ~2.195 tokens**; `validate --explain` com arquivo, linha e correção |
| 2.5 | **`run.json`** (ADR-10) | o review UI lê `run.json`; `evidence.md` gerado a partir dele |

**Gate de saída da fase 2:** um round gerado de OpenAPI roda; a busca em run é sub-segundo; **nenhum fallback por relógio local** existe no código.

**2.4 é a de maior retorno e a mais fácil de deixar para depois** — é só mover regra em prosa para código. E é a que mais muda a experiência do agente, porque 43% das regras acionáveis estão hoje no balde errado (são verificáveis por máquina e não são verificadas).

### Fase 3 — Superfície de navegador (por último)

| # | Movimento | Gate (comando) |
|---|---|---|
| 3.1 | Portar `ui.*` e packs estruturais para o núcleo novo | `NOKR_QA_SLOW=1 pytest -m slow tests/test_ui_slow.py` ⇒ **7 verdes** |
| 3.2 | Redesenho de UI/UX (F7 §3), com os 4 blocos de tela do E6 | review de tela renderizado; review de HTTP **não regride** |
| 3.3 | Retomada da **emenda 11** | condicionada a 3.1 |

**Gate de saída da fase 3:** `NOKR_QA_SLOW=1 pytest -m slow tests/test_ui_slow.py` com **7 verdes** sobre o núcleo novo, e o review de tela renderizado.

**O que a fase 3 não pode fazer:** nem 3.1 nem 3.2 no caminho crítico das fases 1–2 (anti-escopo do plano). E o alvo de 3.1 é **portar**, não reescrever — o risco registrado em §4.7 é o navegador apodrecer na fila e alguém decidir que é mais rápido reescrever `browser.py` (735 linhas) e `ui_step.py` (383).

### 2.1 O que cada fase prova, em uma linha

| Fase | A promessa | A prova |
|---|---|---|
| 1 | o núcleo roda sem o Nokr, e o Nokr roda sobre o núcleo | toy provider numa wheel limpa **+** `git diff campaigns/` vazio |
| 2 | o agente deixa de escrever YAML mecânico e o run fica navegável | round gerado de OpenAPI + busca sub-segundo + zero fallback de relógio |
| 3 | o review de tela funciona sobre o núcleo novo | 7 testes `slow` de navegador verdes |

---

## 3. A folha de números

Tudo que as nove frentes mediram, num lugar só. É o que permite dizer "a fase está pronta" sem reputação.

### 3.1 Onde as medições corrigiram o plano

Quatro correções. Registradas porque o plano é a referência e alguém vai lê-lo primeiro.

| O plano dizia | A medição diz | Frente |
|---|---|---|
| "1.271 linhas de prosa a traduzir" | as 1.271 são a spec **do produto** e ficam em PT-BR; o núcleo precisa de **~500-600** | F8 §4.1 |
| "índice FTS5 para busca sub-segundo" | a busca **já é** sub-segundo (30-48 ms); o FTS5 custa 18,9 MB e **piora** | F4 §1 |
| "251 ocorrências de produto no harness" | 1.432 ocorrências, das quais **671** são produto; `src/` é só **17%** | F8 §0 |
| "E3 em curso" | o E3 está **completo e verde**; o que falta é a verificação `slow`, nunca executada | F0 §4.1 |
| "os **quatro** testes `slow`" | são **7** em `tests/test_ui_slow.py` (mais 4 arquivos com o marker `slow`, 21 testes no total) | §3.2 |

Nenhuma delas inverte uma decisão do plano. Três **reduzem o custo** do que ele previu (idioma, índice, rename no núcleo), uma **aumenta o risco** (o E3 está mais pronto do que se pensava, o que torna a espera da fase 3 mais cara do que parecia) e uma **aumenta o gate** (a prova de não-quebra da fase 3 é 75% maior do que o plano contava).

### 3.2 A prova de não-quebra da fase 3 é maior do que o plano diz

O plano escreve "os quatro testes `slow` verdes sobre o núcleo novo" como gate de saída da fase 3. Contado:

```
tests/test_ui_slow.py       7 testes   ← browser + NokrAPI vivos  (o gate real)
tests/test_values_packs.py  7 testes   ┐
tests/test_pilot_ingest.py  6 testes   ├ marker `slow`, não são de navegador
tests/test_live_optional.py 1 teste    ┘
```

O gate de navegador são **7 testes**, não 4, e todos requerem stack vivo (`NOKR_QA_SLOW=1`) — nenhum roda em CI hermético, como o F0 registrou. Isso não muda o desenho: muda o **custo da fase 3**, que passa a ser 75% maior do que o plano estimava, e reforça o risco já registrado em §2 (o navegador apodrecer na fila e alguém decidir reescrever `browser.py`). O registro correto importa aqui porque "4 verdes" soa como uma tarde de trabalho e "7 testes que exigem stack vivo e nunca rodaram nesta máquina" não soa.

### 3.3 As medições por frente

| Frente | Número | Onde |
|---|---|---|
| F0 | `src/` 8.463 linhas / 37 arquivos; `tests/` 8.491 / 51 | §F0 |
| F0 | suíte `slow` de navegador: **nunca executada** nesta máquina | §F0 |
| F1 | 11 vazamentos nomeados, classificados em descriptor / núcleo / vazamento | §F1 |
| F3 | **48,8% derivável** (263 de 539); 13,9% parcial; 37,3% domínio | §F3 |
| F4 | ripgrep 30-48 ms; corpus em memória 3-6 ms; 837 `packs.json` em 55 ms | §F4 |
| F4 | FTS5: 18,9 MB (73% do corpus) para salvar ~15 ms — **rejeitado** | §F4 |
| F4 | consolidação: 539 → **47** arquivos, 1,12× em bytes | §F4 |
| F5 | defeito do coletor: retorna ao achar `web_hit` e **descarta** `wait_logs_ms` do worker | §F5 |
| F6 | partida **27.442 tokens**, 68% num arquivo só (`docs/nokr-qa.md`) | §F6 |
| F6 | **9 de 21** regras acionáveis (43%) estão no balde errado | §F6 |
| F8 | 1.432 ocorrências `nokr*`: 761 harness / **671 produto** (47 tokens) | §F8 |
| F8 | `~/.cache/ms-playwright` = **1,3 GB** | §F8 |
| F8 | `heimdall-qa` **livre** no PyPI (HTTP 404) | §F8 |

---

## 4. O registro de edge cases

Consolidado das nove frentes. Cada item é uma pergunta que a implementação não pode descobrir em produção. **Resolvido** = a frente decidiu; **aberto** = vai para §6.

### 4.1 Acoplamento e rename

| Item | Estado |
|---|---|
| `=8` na raiz — arquivo órfão de 0 bytes, acidente de shell | **resolvido** — apagar no 1.5 |
| `secrets.local.yaml` existe em disco e está gitignored | **resolvido** — `.example` + detecção de ausência + recusa de commit (o `.example` ainda tem chave `nokr_user_id`, que sai no 1.1) |
| `config.py` com default `../NokrAPI/logs/nokr-web.log` | **resolvido** — o descriptor substitui; default de produto some no 1.1 |
| Bruno como fonte de request | **resolvido** — vira adapter (V11, ADR-02); OpenAPI/Postman/Insomnia como alternativas |
| 539 cases e 48 contracts: o que é do núcleo? | **resolvido** — nada é do núcleo; são conteúdo do provider (F8 §2.2 F-f) |
| `packs/__init__.py:15` casa `com.nokr` | **resolvido** — vira `errors.product_packages[]` (V9) |
| `session_validate.py:30-35` tem seis rotas `/platform/*` | **resolvido** — vira `routes[]` do descriptor (V2) |
| `fixtures.py`: `Faker("pt_BR")` + CPF/CNPJ | **resolvido** — motor no núcleo, `validate-docbr` no provider (ADR-11) |
| Falta teste que **prove** que o núcleo não importa provider | **resolvido** — T3, o gate de ADR-06 (§1 de F8) |
| `nokr-qa-queue-width` é identidade de harness, não de produto | **resolvido** — rename mecânico, não descriptor (F8 §2.2 F-g) |

### 4.2 Auto-discovery

| Item | Estado |
|---|---|
| OpenAPI não carrega regra de negócio | **resolvido por fronteira** — o não-derivável sai como `TODO` e o `validate` recusa (D2 de F3) |
| Conflito com "fonte do contract = DTO Java" | **resolvido** — ADR-02; o DTO vira fonte adicional, `dto` deixa de ser obrigatório |
| Endpoint sem schema (só Bruno/Postman) | **resolvido** — adapter de request source (V11); o caminho existe |
| Status de validação 400 vs 422 | **resolvido** — `errors.validation_status` por projeto |
| Envelope de erro varia (RFC 7807, `{code,message}`, Spring) | **aberto** — o F2 desenhou `errors.envelope: code_message`, mas o inventário de formatos reais não foi feito |
| Geração ingênua produz milhares de casos | **resolvido** — teto `rotas × eixos_deriváveis` + orçamento (F3 §6) |
| Review precisa de caso **nomeado**, não seed aleatória | **resolvido** — property-based puro não serve ao fluxo de review |
| Schema drift: OpenAPI muda e os cases ficam órfãos | **aberto** — `validate --drift` está proposto, não desenhado |

### 4.3 Storage e navegação

| Item | Estado |
|---|---|
| Índice binário não pode ir para o git | **resolvido** — derivado, gitignored, reconstruível; e na prática desnecessário (F4) |
| Consolidação 539 → 47 muda a granularidade de review | **resolvido** — ordem do disco preservada, nunca ordenar por ID (diff churn) |
| Retenção e pruning dos runs | **aberto** — "10 anos" é requisito da NokrAPI, não do harness; a política do núcleo não foi decidida |
| Concorrência entre `run` e `serve` | **aberto** — WAL proposto, não medido |
| `schema_version` no `run.json` | **resolvido** — ADR-10; e `run.json` **não existe hoje** |
| `junit.xml` para CI de terceiros | **aberto** — mencionado no estudo §8, não desenhado |

### 4.4 Logs distribuídos

| Item | Estado |
|---|---|
| Skew de relógio quebra o fallback de ±1 s | **resolvido** — o fallback é removido (ADR-03) |
| Log JSON de outra linguagem, campos diferentes | **resolvido** — `marker_field` + `format: json-lines` |
| Multiline e stack trace | **resolvido** — `multiline.start` |
| Rotação e truncamento | **parcial** — offset reseta mas o início se perde; marca `truncated: true` |
| Volume: nunca ler o arquivo inteiro por passo | **resolvido** — `max_tail_bytes` |
| Trace id não propagado a jusante | **resolvido** — `propagate: false` declarado; ausência vira `skipped` **de instrumento**, não de produto |
| W3C `traceparent` vs header próprio | **aberto** — só o header próprio está desenhado |
| Duas instâncias do mesmo serviço | **aberto** — dedupe por `(fonte, linha)` é trivial, mas não há caso real |
| Fontes remotas (docker, k8s, ssh, HTTP) | **fora da v1** — o descriptor acomoda (`kind: docker`); implementar quando houver uso |

### 4.5 Contrato do agente

| Item | Estado |
|---|---|
| Skill duplicada em duas pastas, já divergentes | **resolvido** — fonte única + geração; a fonte é **pré-requisito** da tradução (F8 §4.3) |
| Regras que só existem em prosa deveriam ser erro de `validate` | **resolvido** — 9 para `validate`, 5 apagadas (F6 §3) |
| Não existe teste de que um agente executa a skill em projeto **novo** | **aberto** — e é o teste que o gate do toy provider (§9.4 de F8) parcialmente substitui |
| Expor o harness como **servidor MCP** em vez de exigir leitura de docs | **aberto** — ideia forte, não estudada; ataca R2 e R6 diretamente |

### 4.6 UI

| Item | Estado |
|---|---|
| Export estático remove a regra "não chame a porta 7878" | **resolvido** — a regra morre; vira invariante de camada testável (F7 §5) |
| Screenshot e ARIA lado a lado no review | **resolvido** — 4 blocos de fase 3 no inventário de F7 |
| A própria UI precisa passar em a11y | **aberto** — não testado |

### 4.7 Prioridade e sequenciamento

| Item | Estado |
|---|---|
| Risco do registro de E3 ficar genérico demais | **resolvido** — o F0 nomeia arquivo, teste e estado por item, não "packs prontos" |
| A suíte `slow` não é gate das fases 1–2, mas não pode ser apagada | **resolvido** — é a prova de não-quebra da fase 3 |
| O passo `ui` é consumidor do núcleo, então refatorar por baixo dele custa duas vezes | **resolvido** — é o argumento técnico da ordem REST-primeiro |
| `browser.py` (735) + `ui_step.py` (383) são a maior superfície de regressão | **resolvido** — é o risco de §2, fase 3 |
| Risco do navegador apodrecer na fila | **resolvido** — alvo é **portar**, não reescrever |
| O `serve/` antigo fica de pé durante a fase 2? | **aberto** — fatiar a UI em duas fases exige decidir isso |

### 4.8 Meta

| Item | Estado |
|---|---|
| Zero commits | **resolvido** — F0 commitou os três repos; baseline existe |
| Contrato do `Provider` congelado por teste | **resolvido** — o provider de brinquedo é o teste de contrato (F8 §9.2) |
| Sem segundo provider, abstração é dívida | **resolvido** — é o gate do desenho (ADR-06) |

**Contagem: 48 edge cases — 35 resolvidos e 13 não totalmente resolvidos** (1 parcial, 1 fora da v1 por decisão, 11 questões abertas). As 11 abertas estão em §6; o parcial é a rotação de log com perda do início, que perde dado mas declara `truncated: true` em vez de mentir.

---

## 5. O gate de saída do estudo, item a item

O plano listou 9 entregáveis. Verificação honesta:

| # | Entregável | Estado | Onde |
|---|---|---|---|
| 1 | ADRs de F1–F8, com decisão e falsificador | **entregue** — 11 ADRs | §1 |
| 2 | Orçamento de tokens de partida e alvo de F6 | **entregue** — 27.442 → ~2.195 | F6 §5.4 |
| 3 | Matriz de derivabilidade de F3 | **entregue** — 48,8% derivável | F3 §4 |
| 4 | Medições de F4, índice e consolidação | **entregue** — FTS5 rejeitado por medição; 539 → 47 | F4 |
| 5 | Descriptor de logs de F5, coberto e não coberto | **entregue** | F5 §5 |
| 6 | Critério de corte de F8 aplicado item a item | **entregue** — 11 V's + 47 tokens em 7 famílias | F8 §2 |
| 7 | Descriptor de F2 com 3 exemplos | **entregue** | F2 §4 |
| 8 | Plano de implementação com gate por fase | **entregue** — §2 | §2 |
| 9 | Registro de congelamento do E3 | **entregue** — arquivo, teste e estado por item | F0 §4 |

**Nove de nove.** O estudo fechou.

Mas duas ressalvas, porque "9 de 9" esconde duas coisas:

**O item 9 é o único cuja qualidade não foi testada pela realidade.** O registro nomeia arquivo, teste e estado, mas o E3 está congelado desde então e ninguém voltou a tocá-lo. O teste real do registro é a fase 3: se ao retomar o E3 alguém precisar reabrir arquivo por arquivo para entender o estado, o registro era genérico demais — e a única forma de saber é chegar lá.

**O item 8 tem gates, mas nem todos são gates.** Os das fases 1 e 3 são comandos. Dois da fase 2 não são:

- **2.1** ("o agente deixa de escrever YAML mecânico") é medível, mas o número-alvo não foi fixado. O F3 deu 48,8% de derivabilidade, que é medida **de uma rota** no protótipo, não do corpus.
- **2.3** ("zero fallback de relógio") é `rg` — esse é um gate de verdade.

O gate que falta declarar é o de 2.1. Fica registrado em §6 como pendência de número, não de desenho.

---

## 6. O que o estudo não resolveu

Onze edge cases abertos, agrupados por quando precisam de resposta. Nenhum bloqueia a fase 1.

### 6.1 Bloqueiam a fase 2

| # | Pergunta | Por que ficou aberta |
|---|---|---|
| 1 | Formato do **envelope de erro** e inventário de variantes | o F2 desenhou `errors.envelope`, mas não inventariou RFC 7807 / Spring / `{code,message}` em APIs reais |
| 2 | **`validate --drift`**: schema muda, cases ficam órfãos | proposto, não desenhado; depende de ADR-02 estar implementada |
| 3 | Alvo numérico do gate de 2.1 | a medição de 48,8% é de uma rota no protótipo; falta medir no corpus |
| 4 | **Política de retenção** dos runs no núcleo | "10 anos" é requisito da NokrAPI; o harness não herdou a decisão |
| 5 | **`junit.xml`** para CI de terceiros | mencionado no estudo §8; não desenhado |
| 6 | **Concorrência** `run` × `serve` | WAL proposto, não medido |

### 6.2 Bloqueiam a fase 3

| # | Pergunta | Por que ficou aberta |
|---|---|---|
| 7 | O `serve/` antigo fica de pé durante a fase 2? | é decisão de sequenciamento, não de desenho |
| 8 | A própria UI passa em a11y? | nunca testado — e é irônico para um harness que testa a11y |
| 9 | W3C `traceparent` vs header próprio | só o header próprio está desenhado |

### 6.3 Não bloqueiam nenhuma fase

| # | Pergunta | Por que ficou aberta |
|---|---|---|
| 10 | **Servidor MCP** como interface primária do agente | ideia forte que ataca R2 e R6; não estudada. Merece frente própria |
| 11 | Fontes de log remotas (docker, k8s, ssh) | o descriptor já acomoda; implementar quando houver uso (F5) |

**O item 10 é o mais interessante da lista.** Se o harness expuser tools MCP, o agente deixa de ler 2.160 linhas de prosa e passa a chamar `validate`, `run` e `last-run` como tools. É a mesma alavanca do F6 (mover regra de prosa para código), aplicada à interface inteira. Não foi estudado porque não estava no plano — e é o candidato natural a próxima frente.

---

## 7. Regras que valem para as três fases

O anti-escopo do plano, transformado em critério de review. Uma PR que viole qualquer destas é recusada, independentemente de estar verde.

1. **O núcleo não importa provider, em nenhuma fase.** Gate: T3 (ADR-06).
2. **Nada de navegador nas fases 1 e 2.** Nem no caminho crítico, nem "só um pouquinho".
3. **O E3 não avança nem é revertido** durante as fases 1–2. Congelado não é abandonado.
4. **O Trilho A não muda durante a refatoração.** Gate: `git diff campaigns/` vazio.
5. **Não reescrever.** Aditivo e reversível, com prova de não-quebra por fase (estudo §11).
6. **Não generalizar antes do segundo provider existir** (ADR-06).
7. **Não trocar a fonte de verdade do conteúdo para binário** (F4).
8. **Não deixar o E3 pela metade sem registro** enquanto o Heimdall QA avança.

**A regra 7 é a que mais tenta ser quebrada.** O F4 mostrou que um índice binário não se paga, e a tentação de "indexar para ficar rápido" volta sempre que alguém mede uma busca lenta — que será quase sempre uma busca sobre um corpus maior do que o medido. O F4 deixou o número (18,9 MB para salvar 15 ms) exatamente para que a discussão seguinte comece por ele.

---

## 8. Resumo

| | |
|---|---|
| **Frentes** | 9 de 9 fechadas |
| **ADRs** | 11, com falsificador |
| **Edge cases** | 48 — 35 resolvidos, 13 abertos (nenhum bloqueia a fase 1) |
| **Fases** | 3, com gate por comando |
| **Começa por** | o corte de deps (1.0): maior impacto de onboarding, menor risco, gate já verde |
| **O gate que importa** | o provider de brinquedo + `git diff campaigns/` vazio |
| **A maior alavanca** | F6: 27.442 → ~2.195 tokens de partida |
| **O risco maior** | o navegador apodrecer na fila da fase 3 |
| **A decisão mais reversível** | o nome (0 usuários hoje) |
| **A menos reversível** | a licença (ADR-09) |

O estudo fez o que se propôs: transformou "vamos abrir o projeto" em nove conjuntos de números, onze decisões com falsificador, e um plano em que cada fase tem uma prova. O que ele **não** fez foi implementar nada — as fases acima são a fila, e a primeira delas começa por uma mudança de uma linha no `pyproject.toml` que já passa nos 257 testes.
