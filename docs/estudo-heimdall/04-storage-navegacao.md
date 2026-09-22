# F4 — Storage e navegação, com números

> **Frente F4 do estudo Heimdall QA.** Documento de estudo, não spec.
>
> Pergunta desta frente: *o que exatamente torna o projeto pesado, e qual é o desenho mais
> leve que ainda responde "esse pack já falhou alguma vez?" em milissegundos?*
>
> Protótipos: [`prototipos/index_vs_grep.py`](prototipos/index_vs_grep.py) ·
> [`prototipos/consolidate_cases.py`](prototipos/consolidate_cases.py)

---

## 1. Veredito

A hipótese do plano — *índice SQLite com FTS5 para busca sub-segundo* — **não se sustenta na
medição**. A busca já é sub-segundo hoje, por duas ordens de magnitude, sem índice nenhum.

O que é pesado é outra coisa: **granularidade de arquivo**. São 45 arquivos para responder
"o que cobre o `POST /api/ingest`". Isso não se resolve indexando; resolve-se consolidando.

| Hipótese do plano | Medição | Veredito |
|---|---|---|
| Busca é lenta e precisa de índice | ripgrep: **30–48 ms**; corpus em memória: **3–6 ms** | **falso** |
| Agregado entre runs é lento | walk de 837 `packs.json`: **55 ms** | **falso** |
| O projeto é difícil de navegar | **45 arquivos** para uma pergunta sobre um contrato | **verdadeiro** |
| FTS5 resolve | índice de **18,9 MB** (73 % do corpus) para salvar ~15 ms | **piora** |

---

## 2. Medições

### 2.1 Navegação no corpus de casos (539 arquivos, 2,3 MB)

| Pergunta | Ferramenta | Tempo |
|---|---|---|
| listar os 539 casos | `rg --files-with-matches ''` | 8 ms |
| "qual caso tem `kind: I-replay`?" | `rg -l` | 7 ms |
| "onde aparece `MISSING_PROPERTY`?" | `rg -l` | 8 ms |

Tudo já em milissegundos de um dígito. **Não há problema de latência.**

### 2.2 Navegação nos runs (6.072 arquivos, 26 MB, 89.404 linhas)

| Pergunta | Ferramenta | Tempo |
|---|---|---|
| "onde tem `trace_id`?" | `rg` | 48 ms |
| "onde tem `nk_test_`?" | `rg` | 35 ms |
| ler o corpus inteiro para memória | walk Python | 202 ms |
| **"qual pack já falhou?"** (837 `packs.json`, 6.567 resultados de passo) | walk + `json` | **55 ms** |
| idem, sem parse (só grep de `"status": "fail"`) | `rg -c` | 15 ms |

O agregado entre runs — a pergunta que o plano usou como critério de aceite — responde em
**55 ms**, com 79 runs. O resultado real dessa consulta:

| Pack | Status | Passos |
|---|---|---|
| `http.baseline` | fail | 31 |
| `business.rule` | fail | 14 |
| `http.error` | waived | 9 |
| `observability` | fail | 4 |
| `auth.surface` | fail | 4 |
| `http.error` | fail | 4 |
| `observability` | waived | 3 |
| `http.baseline` | waived | 2 |
| `http.success` | fail | 1 |
| `business.rule` | waived | 1 |

9 packs distintos, 6.567 resultados de passo. É esta tabela que `heimdall-qa query packs
--failed` deve produzir (§4, D4).

### 2.3 O índice FTS5, medido

| Métrica | Valor |
|---|---|
| Linhas indexadas | 89.404 |
| Custo de build (walk + insert) | **578 ms** |
| Tamanho do índice em disco | **18,9 MB** |
| Tamanho do corpus indexado | 26 MB |
| **Razão índice/corpus** | **0,73** |
| Latência de query | **0,2–0,7 ms** |

O índice é rápido. E é inútil, por três motivos que só aparecem quando se mede:

**(a) Ele custa 73 % do corpus para economizar ~15 ms.** A query já roda em 30–48 ms com
`rg` e em 3–6 ms com o corpus em memória. Não existe orçamento de latência que justifique
manter 19 MB de estado derivado.

**(b) A semântica é diferente, e a diferença mente.**

| Query | `rg` (linhas) | Walk (linhas) | FTS5 (tokens) |
|---|---|---|---|
| `trace_id` | 1.978 | 1.974 | **3.625** |
| `MISSING_PROPERTY` | 15 | 7 | **403** |
| `com.nokr` | 7 | 7 | 7 |

FTS5 tokeniza: `MISSING_PROPERTY` casa como três tokens e devolve **403** onde existem **15**
linhas. Num harness cuja função é dizer a verdade sobre o que aconteceu, um índice que
**sobre-conta por 26×** é pior que nenhum índice. Um revisor que busca uma linha de log e
recebe 403 resultados desiste.

**(c) Ele cria dois problemas que hoje não existem.** Estado derivado precisa ser
invalidado quando um run novo chega (o `runs/` muda a cada execução) e precisa de
concorrência segura entre `run` e `serve`. Sem banco, não há problema de concorrência
nenhum: `runs/` é uma árvore de arquivos imutáveis por run.

**Nota de honestidade sobre o `walk` mais rápido que o `rg`:** a diferença é cache. Com o
corpus em memória (202 ms de leitura), a query é 3–6 ms; o `rg` paga a travessia de disco a
cada chamada. Nenhum dos dois precisa de índice — e é isso que invalida a hipótese.

### 2.4 O que o crescimento faz com isso

| Medida | Hoje | Por run | Projeção a 1.000 runs |
|---|---|---|---|
| Tamanho | 26 MB | ~320 KB | ~320 MB |
| Arquivos | 6.238 | ~75 | ~75.000 |
| Walk do corpus | 202 ms | — | ~2,4 s |
| Walk dos `packs.json` | 55 ms | — | ~660 ms |

**Aqui sim há um número que se degrada**: o walk linear cresce com o número de runs. Aos
1.000 runs, "qual pack já falhou" vai a ~660 ms. Ainda sub-segundo, mas a curva é o argumento
honesto a favor de *algum* índice no futuro — desde que seja um índice **semântico**
(`run → round → passo → pack`), não um índice de texto.

**Retenção.** Os 10 anos são requisito da **NokrAPI** (constituição §32), não do harness. O
harness não tem por que herdar isso; um default de retenção é parte do descriptor, não do
núcleo.

---

## 3. O problema real: granularidade

Medido nos 539 casos:

| Medida | Valor |
|---|---|
| Diretórios de caso | 48 |
| Casos por contrato | máx **45**, mediana **8**, mín 1 |
| Arquivos para ver a cobertura de `ingest` | **45** |
| Arquivos para ver a cobertura de `platform-billable-metrics-post` | **33** |

A dor de navegação não é "achar um arquivo" — é **reconstruir um conjunto**. Para responder
"a cobertura do `POST /api/ingest` está completa?" um humano ou agente abre 45 arquivos, e um
agente paga 45 leituras em tokens. Nenhum índice conserta isso: o problema é que a unidade de
leitura (o caso) não é a unidade da pergunta (o contrato).

### 3.1 Consolidação, medida

`consolidate_cases.py` funde cada diretório num arquivo com os casos inline:

| Medida | Antes | Depois |
|---|---|---|
| Arquivos | 539 | **47** |
| Bytes | 132.238 | 147.687 (**1,12×**) |
| Maior arquivo | — | `ingest.yaml`, 653 linhas / 13 KB |
| Arquivos para ver a cobertura de `ingest` | 45 | **1** |

**O custo de bytes é 12 %.** O YAML indenta mais, e nada mais.

### 3.2 O diff de review, medido

Este era o risco da consolidação: trocar um problema de navegação por um problema de review.
Simulei a menor edição realista (um campo em um caso) em `ingest.yaml`:

```diff
@@ -568,6 +568,7 @@
     status: 400
   generate:
     timestamp: now_iso
+  note: 'study prototype: simulated one-field edit'
 - id: ingest-N-pattern-event_type
```

**10 linhas de diff, 1 linha alterada.** O diff de linha é preservado. O risco não se
materializou.

### 3.3 Uma armadilha que a medição descobriu

Os `id` dos casos **não estão na ordem dos nomes de arquivo**: em `ingest`, 2 de 44 casos
mudariam de posição se o arquivo consolidado fosse ordenado por `id`.

```
ids already sorted by filename? False
-> 2 of 44 would change position if sorted by id
```

Ordenar por `id` na consolidação produz um **reshuffle diff**: renomear um caso faz dezenas de
linhas aparecerem como alteradas. O formato consolidado **tem de preservar a ordem em disco**.
É um requisito de desenho que só aparece se você medir — e a alternativa (ordenar por `id`,
que é o instinto) degrada silenciosamente todo PR futuro.

---

## 4. Decisões

### D1 — Sem índice FTS5 na v1

A hipótese não se sustenta (§2.3). O critério de aceite do plano ("busca sub-segundo") já é
satisfeito por `rg` sem estado derivado. Adotar o índice adicionaria 19 MB, uma semântica que
sobre-conta por 26× e dois problemas novos (invalidação e concorrência) para economizar 15 ms.

**Gatilho de revisão:** quando o walk dos `packs.json` passar de ~500 ms (≈ 750 runs), reabrir
a decisão. Nesse ponto o índice deve ser **semântico** — tabelas `run`, `round`, `step`, `pack`
— e alimentado por um comando explícito, nunca por um daemon.

### D2 — Consolidar os casos: 539 → 47 arquivos

É a única mudança que ataca a dor medida (§3). Custo de bytes de 12 %, diff de linha
preservado.

**Requisito de desenho descoberto por medição:** preservar a ordem em disco, nunca ordenar por
`id` (§3.3).

**O que a consolidação não faz:** não é "menos conteúdo", é a **mesma** informação em 47
unidades de leitura em vez de 539. Se o `validate` e o `run` passarem a ler 47 arquivos em vez
de 539, o ganho aparece também no tempo de `validate` (hoje 1,35 s no Trilho A).

**Migração:** a consolidação é transformação mecânica e reversível — o protótipo prova que
`cases/<dir>/*.yaml` ⇄ `cases/<dir>.yaml` é função. O gate é a suíte continuar verde e
`campaign status` produzir o mesmo JSON.

### D3 — O critério de aceite muda

O plano dizia "busca sub-segundo". Isso já é verdade e não mede nada. O critério passa a ser
**estrutural**:

| Critério antigo | Critério novo |
|---|---|
| busca em run é sub-segundo | **1 arquivo** responde "o que cobre este contrato?" |
| índice pode ser apagado sem perda | **não existe índice** para apagar |
| conteúdo revisável em PR | **diff de 1 caso ≤ 15 linhas** |

O segundo e o terceiro são verificáveis por comando, o que o critério antigo também era — mas
o antigo já passava antes do trabalho começar.

### D4 — O agregado entre runs vira comando, não banco

"Qual pack já falhou?" responde em 55 ms com um walk. Isso vira `heimdall-qa query packs
--failed`: sem estado, sem build, sem invalidação. Se algum dia precisar de velocidade, o
caminho é cache em memória por invocação, não banco em disco.

---

## 5. Consequências para o plano de implementação

| Item | Efeito |
|---|---|
| F4 entra na fase 2, não na 1 | não toca o caminho quente; a fase 1 não depende dela |
| A consolidação é o item de maior valor da fase 2 | ataca a dor medida, com custo de 12 % de bytes |
| O índice sai do escopo | remove a dependência de SQLite/FTS5 e o problema de WAL `run` × `serve` |
| O `walk` linear fica registrado como gatilho | 750 runs; medido, não chutado |
| `schema_version` no `run.json` continua necessário | é o que permite um índice futuro sem reprocessar tudo |
| Retenção vira campo do descriptor | os 10 anos são da NokrAPI, não do harness |

---

## 6. O que esta frente prova

1. **A hipótese do plano estava errada, e a medição mostrou em que direção.**
   Busca não é o problema (30–48 ms); granularidade é (45 arquivos por pergunta).
2. **O índice não só é desnecessário — é contraproducente.** 19 MB de estado derivado,
   semântica que sobre-conta por 26× e dois problemas novos, para 15 ms.
3. **A consolidação se paga e é segura.** 539 → 47 arquivos, 12 % de bytes, diff de 1 linha — e
   uma armadilha (ordem por `id`) que só a medição revelou.
4. **O critério de aceite original media algo que já era verdade.** Foi trocado por um critério
   estrutural, verificável, que ainda não é verdade.
5. **O numero que se degrada está identificado e tem nome:** walk linear, gatilho em ~750 runs,
   e o sucessor já está especificado (índice semântico, não de texto).
