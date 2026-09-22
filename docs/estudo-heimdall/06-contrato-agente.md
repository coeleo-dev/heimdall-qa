# F6 — Contrato do agente: medir a partida

> **Frente F6 do estudo Heimdall QA.** Documento de estudo, não spec.
>
> Pergunta desta frente: *quanto custa, em tokens, começar? E quais regras são regras de
> verdade — isto é, quais o código aplica?*
>
> Baseline: `nokr-qa@616cf6e`.

---

## 1. Veredito

A partida custa **~27.400 tokens**. Desse total, **68 % é um único arquivo**
(`docs/nokr-qa.md`, 18.234 tokens), e a skill manda o agente lê-lo inteiro para executar um
protocolo que ocupa **825 tokens** (A.14).

Pior que o custo é a **fração não aplicada**: das 21 regras acionáveis em prosa na skill,
**8 estão no balde "validação"** — deveriam ser erros de `validate`. Testei a mais destacada
(*"Não inventar 422 sem `rules[]`"*): um caso que inventa um 422 sem regra que o declare passa
com **zero menções** ao problema.

A regra que a skill lidera é exatamente a que não tem código atrás — e é uma regra sobre **não
fabricar evidência**, que é o que um harness de review existe para impedir.

---

## 2. A partida, medida

Conjunto que o agente precisa ler para começar, conforme o `AGENTS.md` e a skill:

| Arquivo | Chars | ~Tokens | Fração |
|---|---|---|---|
| `docs/nokr-qa.md` | 74.268 | **18.567** | **67,7 %** |
| `README.md` | 16.182 | 4.045 | 14,7 % |
| `.cursor/skills/…/SKILL.md` | 7.499 | 1.874 | 6,8 % |
| `.agents/skills/…/SKILL.md` | 8.068 | 2.017 | 7,4 % |
| `AGENTS.md` | 1.574 | 393 | 1,4 % |
| `config.yaml` | 1.180 | 295 | 1,1 % |
| `docs/README.md` | 649 | 162 | 0,6 % |
| `secrets.example.yaml` | 358 | 89 | 0,3 % |
| **Total** | **109.778** | **~27.442** | 100 % |

(heurística de 4 chars/token; a ordem de grandeza é o que importa)

### 2.1 O ponteiro que puxa o arquivo inteiro

A skill diz: *"Leia `docs/nokr-qa.md` (A.14)"*. A.14 são 50 linhas / 825 tokens. Mas A.14 está
num arquivo de 1.271 linhas com 38 seções, e um agente que abre o arquivo lê o arquivo.

O que o trabalho realmente exige, medido por seção:

| Seção | ~Tokens | Necessária para "crie uma rodada"? |
|---|---|---|
| A.14 Protocolo do agente | 825 | **sim** |
| A.10 Schemas | 1.203 | **sim** |
| A.7 Packs automáticos | 1.335 | **sim** |
| A.6 Camadas YAML | 708 | **sim** |
| A.9 Protocolo da matriz | 1.646 | às vezes (campanha) |
| A.18 Validação cruzada | 1.553 | às vezes (10+7) |
| A.19 Superfície de browser | 624 | às vezes (Trilho C) |
| *(outras 31 seções)* | ~9.500 | **não** |

Mínimo real para uma rodada HTTP: **~4.071 tokens** de spec. O ponteiro para o arquivo inteiro
custa **18.567** — **4,6×**.

### 2.2 A skill está duplicada, e já divergiu

Há duas skills (`/.cursor/` e `/.agents/`), e o `diff` entre elas tem **87 linhas**:

| Divergência | Detalhe |
|---|---|
| Título | `# Nokr QA — rodadas` vs `# Nokr QA — rodadas (Antigravity & Cursor)` |
| Links | caminhos relativos diferentes (`../../../docs/` vs `docs/`) |
| Seção extra | `.agents/` tem "Invocação de Comandos CLI no Antigravity" (19 linhas) que `.cursor/` não tem |

Não é duplicação cosmética: o conteúdo **já é diferente**, e nada garante que as duas versões
digam a mesma regra. São 3.891 tokens de quase-duplicata que podem contradizer em silêncio.

### 2.3 A densidade da prosa

Sete linhas da skill passam de 300 caracteres:

| Chars | Conteúdo |
|---|---|
| **988** | passo 5 inteiro, num parágrafo |
| **575** | passo 6 inteiro, num parágrafo |
| 537 | passo 3 da campanha |
| 445 | passo 6 da campanha |
| 433 | passo 5 da superfície |
| 411 | passo 2 da rodada |

O passo 5 tem uma linha de **114 palavras** e outra de 80. Não é um problema estético: uma
linha de 988 caracteres com ~10 regras dentro não é verificável nem por humano nem por agente,
e é por isso que as regras de dentro dela não viraram código.

---

## 3. Inventário das regras em prosa

Extraí as 28 sentenças imperativas da skill. Descontadas as que são metadados, links ou
instruções de análise (7), sobram **21 regras acionáveis**. Classificação:

### 3.1 Validação — deve virar erro de `validate` (9)

| Regra | Estado hoje |
|---|---|
| Não inventar 422 sem `rules[]` | **não aplicada** — provado em §4.1 |
| `rules[]` precisa de `code` ou `error` | **aplicada** — provado em §4.2 |
| N-over: semente `example`/`Aa1x`, nunca `'x'*n` em password | não aplicada |
| Não hardcodar `ext-ta-001` | não aplicada |
| Segredos só em `secrets.local.yaml` (nunca JWT/`nk_test_` no YAML da rodada) | parcial (redact existe para artefato) |
| P-live não misturar no sandbox | não aplicada |
| Não misturar `values-10m-7i` / Trilho C no Trilho A | parcial (é o default de `CampaignExclude`) |
| Passo `ui` exige âncora declarada | não aplicada |
| Âncora ausente ⇒ avisar, não inventar seletor | não aplicada |

### 3.2 Default — deve ser o comportamento padrão, não uma regra a lembrar (7)

| Regra | Default que a substitui |
|---|---|
| Abrir o DTO Java antes de escrever o contract | `contract.source` no descriptor (F3, ADR-02) |
| Escrever contract + baseline | gerador de `scaffold` (F3) |
| Login/refresh reusa o `capture` do `register-H01` | auto-wiring de `captures` |
| JWT/`api_key` vêm do `capture_response` | auto-wiring de `captures` |
| Duplicado no run: `capture` + `generate: captured.*` | auto-wiring de `captures` |
| Login lockout: `waive business.rule` (P-GAP-8) | template de waive no provider |
| Kind D / A5 / A6 fora do Trilho A | default do provider, não do núcleo (V5 de F1) |

### 3.3 Morre — deve ser apagada (5)

| Regra | Por quê |
|---|---|
| Não chamar a porta 7878 | dita **três vezes** na mesma página; se F7 tirar a necessidade, morre sozinha |
| Se não existe no Bruno, criar `.bru` **e** o case | F1 V11 torna a fonte de request plugável |
| "Register: o harness já espera `register_gap_ms`; 429 é bucket" | **explica o interno do harness para quem usa o harness**; é comentário de código, não regra |
| Não devolver uma fila de `nokr-qa serve rounds/<id>.yaml` | etiqueta de interação; 1 linha basta, ou nada |
| Se o pedido vier antes das Fases 11–12, entregar o YAML e dizer o que falta | fica obsoleto no dia em que a fase fechar |

**A distribuição é o achado:** 9 de 21 regras (43 %) estão no balde errado — são verificáveis e
não são verificadas. E 5 (24 %) não deveriam existir.

---

## 4. As duas provas

### 4.1 A regra principal não é aplicada

Criei um caso que **inventa** um `expect.status: 422` num contrato cujos `rules[]` não declaram
422 nenhum. `validate_round` devolveu 43 erros — todos de cobertura de eixo — e **nenhuma
menção ao 422 inventado**:

```
errors mentioning the invented 422: NONE
total errors: 43
```

A regra está em prosa há tempo suficiente para a skill liderar com ela. O código não a conhece.

### 4.2 A regra vizinha é aplicada, e a diferença é instrutiva

*"Cada `rules[]` precisa de `code` ou `error`"* é a mesma família de regra, escrita na linha
seguinte do mesmo parágrafo. Ela **é** aplicada — `validate.py:100-120`:

```
contract .../api-ingest-post.yaml: rule UNKNOWN_CUSTOMER needs code or error
```

O que separa as duas não é importância, nem dificuldade: é que alguém escreveu 20 linhas de
`_rule_identity_errors`. **Uma regra em prosa sem check correspondente é indistinguível de uma
regra que não existe.**

### 4.3 Uma armadilha de medição, registrada

No primeiro teste carreguei o contrato direto pelo modelo (`load_contract`) e concluí que o
`RuleSpec` era permissivo demais. Errado: `RuleSpec.code` e `RuleSpec.error` são opcionais **de
propósito**, porque a checagem roda em `validate_round` (o ponto certo — é onde o contrato é
usado *junto* com os casos). Vale registrar porque é fácil "consertar" o modelo e mover a regra
para o lugar errado: um validador de modelo não vê o caso, e a regra dos 422 precisa dos dois.

---

## 5. Desenho: `validate` é o contrato

A conclusão de §3 não é "escrever prosa melhor". É que **a prosa deve migrar para o código, e o
agente deve descobrir regras executando, não lendo**.

### 5.1 A inversão

| Hoje | Proposto |
|---|---|
| O agente lê 18.567 tokens de spec | O agente escreve YAML e lê erros de `validate` |
| Regra em prosa, aplicada por disciplina | Regra é um erro com código (`422_WITHOUT_RULE`) |
| Esquecer uma regra é silencioso | Esquecer uma regra é um erro de uma linha |
| A spec é a fonte de verdade | O **validador** é a fonte; a spec explica o porquê |

Isso não elimina a spec: ela continua explicando *por que* cada regra existe, o que um agente
precisa para decidir em caso ambíguo. Elimina a **inversão**: hoje a spec é o mecanismo de
aplicação, e ela não consegue aplicar nada.

### 5.2 O que cada erro precisa carregar

Para que o agente feche o ciclo sem ler a spec, todo erro de `validate` precisa de:

1. **código estável** — `422_WITHOUT_RULE`, não uma frase;
2. **o fix** — o que exatamente colocar no YAML;
3. **apontador** — arquivo:caso (`cases/ingest/ingest-N-rule-X.yaml`);
4. **razão em uma linha** — por que isso importa.

Exemplo de contrato de erro:

```
422_WITHOUT_RULE
  cases/ingest/ingest-N-rule-INVENTED.yaml: expect.status 422
  fix: declare a rule with status 422 in contracts/api-ingest-post.yaml
       rules[], or change expect.status to the real status.
  why: a 422 that no rule explains is a fabricated expectation.
```

Quatro linhas que substituem um parágrafo de 988 caracteres — e que o agente **não pode
ignorar**, porque `validate` é gate, não leitura.

### 5.3 `validate --explain`

Complemento barato: `heimdall-qa validate --explain` lista os **códigos de erro** com a razão de
uma linha. É o índice de regras que substitui a leitura da spec inteira. Custo de leitura: ~60
linhas (~600 tokens) contra 18.567.

### 5.4 Alvo de leitura

| Caminho | Tokens |
|---|---|
| SKILL (deduplicada, prosa enxuta) | ~900 |
| `AGENTS.md` | 393 |
| Descriptor do projeto (recorte do que importa) | ~500 |
| Contrato em trabalho | ~200 |
| Saída de `validate` | ~200 |
| **Total** | **~2.200** |

Contra **27.442** de hoje: **12,5× menos**, e o que sobra é específico do trabalho em curso em
vez de ser a spec inteira.

O item 5.4 não depende de escrever mais nada. Depende de **mover as 9 regras de §3.1 para o
validador e apagar as 5 de §3.3** — e depois medir de novo.

---

## 6. Consequências para o plano de implementação

| Item | Efeito |
|---|---|
| As 9 regras de §3.1 | itens de trabalho da fase 1/2, cada uma com código de erro |
| As 5 regras de §3.3 | deleção pura; nenhum trabalho |
| Deduplicar as duas skills | item de fase 1; hoje divergem em 87 linhas |
| `validate --explain` | item de fase 2; é o que permite o alvo de §5.4 |
| `docs/nokr-qa.md` | fica como spec do **provider** (F8); o núcleo ganha um doc próprio por seção |
| O ponteiro "Leia `docs/nokr-qa.md`" | sai da skill na fase 1; é a maior alavanca isolada |
| A armadilha de §4.3 | nota de implementação, para ninguém mover as regras para o modelo |

---

## 7. O que esta frente prova

1. **A partida custa ~27.400 tokens, e 68 % é um arquivo só.** A skill aponta para o arquivo,
   não para a seção: 4,6× de desperdício num único ponteiro.
2. **43 % das regras acionáveis estão no balde errado.** São verificáveis, não são verificadas, e
   a mais destacada — *não inventar 422 sem `rules[]`* — é justamente a que não tem check.
3. **Uma regra em prosa sem código é indistinguível de uma regra inexistente.** A prova está nas
   duas vizinhas: mesma linha do mesmo parágrafo, uma aplicada e outra não, e a diferença é 20
   linhas de validador.
4. **A skill está duplicada e já divergiu** (87 linhas de `diff`). Não há como garantir coerência
   entre duas cópias de uma regra.
5. **O alvo é 12,5× menor e não exige desenho novo:** mover 9 regras para código, apagar 5,
   deduplicar a skill, e deixar o agente descobrir regras por `validate` em vez de por leitura.
