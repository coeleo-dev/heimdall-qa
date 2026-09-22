# F3 — Auto-discovery: a fronteira do gerável

> **Frente F3 do estudo Heimdall QA.** Documento de estudo, não spec.
>
> Pergunta desta frente: *o que dá para gerar sem IA, e o que só o humano/agente sabe?*
>
> Protótipo executável: [`prototipos/gen_contract.py`](prototipos/gen_contract.py) +
> [`prototipos/openapi-ingest-3.1.yaml`](prototipos/openapi-ingest-3.1.yaml).

---

## 1. Dois achados que mudam a pergunta

Antes da matriz, duas verificações que reordenam a frente inteira.

### Achado A — a NokrAPI não tem OpenAPI

```
rg -i 'springdoc|openapi|swagger' NokrAPI/pom.xml   -> 0 resultados
rg -l 'io.swagger|@Operation|@Schema' NokrAPI/src   -> 0 arquivos
```

**Não existe documento de contrato na origem.** O `pom.xml` tem `spring-boot-starter-actuator`
e mais nada. Ou seja: auto-discovery a partir de `GET /v3/api-docs` não é uma capacidade a
construir no harness — é uma capacidade que **o alvo ainda não tem**.

Consequência direta para o plano de implementação: a fase 2 tem uma dependência externa
(*adicionar springdoc à NokrAPI*) que não é trabalho do harness e não pode estar no caminho
crítico da fase 1. A fase 1 usa `contracts/*.yaml` na mão, como hoje.

### Achado B — o `dto` é decorativo, mas obrigatório

O `dto` aparece em exatamente três lugares no código:

| Onde | O quê |
|---|---|
| `schema/models.py:88` | `Contract.dto: str` — **obrigatório, sem default** |
| `schema/models.py:156` | `CampaignRound.dto: str` — obrigatório |
| `campaign.py:78` | `"dto": entry.dto` — copiado para o relatório |

Nenhum outro ponto do harness lê esse campo. Não há parsing de Java, não há resolução de
classe, não há validação contra o DTO. **Verificado por execução:**

```
>>> Contract.model_validate(endpoint=..., auth=..., baseline=..., fields={})
contract WITHOUT dto: REJECTED -> ["('dto',): missing"]
```

Duas consequências, e a segunda é a que dói:

1. A regra do `AGENTS.md` — *"fonte do contract = DTO Java, não o exemplo do playbook"* — é
   uma regra de **fluxo humano de trabalho**, não um contrato de código. O humano olha o DTO;
   a ferramenta nunca olha.
2. **Nenhuma API que não seja Java pode ser descrita hoje.** Um projeto Node não tem
   `com.exemplo.dto.Thing` para pôr ali, e o modelo recusa o contrato sem ele. Isto é um
   bloqueio duro ao requisito principal, e é o tipo de coisa que passa despercebida porque a
   regra "a fonte é o DTO" faz o campo parecer significativo.

### O que os dois achados fazem com a frente

Auto-discovery deixa de ser "gerar casos a partir do schema que já existe" e passa a ser
**"gerar casos a partir do schema que o alvo precisa aprender a produzir"**. Isso não
invalida nada — mas muda o dono da tarefa e a ordem.

---

## 2. Matriz eixo × derivabilidade

Todos os eixos vêm de [`coverage.py`](../../src/nokr_qa/coverage.py), que é o gerador de
cobertura do harness. A coluna "casos" é a contagem **real** nos 539 cases do baseline —
não uma estimativa.

| Eixo | Casos | Derivável de JSON Schema? | Fonte no schema | Observação |
|---|---|---|---|---|
| `N-omit-{field}` | 86 | **sim** | `required` ausente | 1:1 |
| `O-omit` / `O-set-{field}` | 68 | **sim** | `required` presente | 1:1 |
| `H01` | 46 | **sim (forma)** | `required` + tipos | precisa de gerador de valor válido |
| `B-max-{field}` | 24 | **sim** | `maxLength` / `maximum` | 1:1 |
| `N-over-{field}` | 24 | **sim** | `maxLength` / `maxItems` | 1:1 |
| `N-pattern-{field}` | 15 | **sim** | `pattern` | 1:1 |
| `B-max-{field}-keys` | — | **sim** | `maxProperties` | incluído em `B-max` acima |
| `N-over-{field}-keys` | — | **sim** | `maxProperties` | incluído em `N-over` acima |
| `N-auth` | 48 | **parcial** | `security` | o schema diz *que existe* auth, não como ela falha |
| `N-notfound` | 14 | **parcial** | path param | o schema diz que o path tem param, não que é um ID de recurso |
| `N-denylist-{key}` | 13 | **parcial** | `not: {enum: …}` | tecnicamente exprimível, mas é **política de PII**, não contrato |
| `B-max-{field}-future` / `B-min-{field}-past` | — | **não** | `x-` (não padrão) | janela temporal não existe em JSON Schema |
| `N-over-{field}-future` / `-past` | — | **não** | `x-` | idem |
| `O-alias-{field}` | — | **não** | `x-json-alias` | alias de serialização é decisão do DTO |
| `I-replay` / `I-new-key` / `I-missing` / `I-format` | 71 | **não** | — | OpenAPI não tem padrão de idempotência |
| `N-rule-{id}` | 71 | **não** | — | regra de negócio; o schema não a conhece |
| `S-bola` (round-trip) | 28 | **não** | — | exige sequência e estado entre passos |
| `H-setup-*` | 10 | **não** | — | ordenação de setup é domínio |
| `P-{rule}` (live-only) | 10 | **não** | — | regra que só existe em produção |
| `E-isolate` / `E-conflict` | 10 | **não** | — | isolamento de ambiente é arquitetura, não contrato |

### O número

| Balde | Casos | Fração |
|---|---|---|
| **Derivável** | **263** | **48,8 %** |
| Parcialmente derivável (precisa de supplement declarado) | 75 | 13,9 % |
| **Não derivável** (domínio) | 201 | 37,3 % |

Se os 75 "parciais" receberem o mínimo declarado (a forma da falha de auth, a marca de que um
path param é um ID de recurso), o teto sobe para **338 casos (62,7 %)** — mas esse teto exige
declaração do projeto, então não é "de graça".

**A leitura honesta:** o gerável é a **metade mecânica** — presença/ausência de campo, limite,
padrão. O não-gerável é a **metade que interessa**: regra de negócio, idempotência, isolamento
de ambiente, round-trip de recurso. É exatamente onde mora o bug de produção.

**Efeito sobre R2:** o agente deixa de escrever ~49 % do YAML que escreve hoje. O ganho não é
"o agente escreve menos" — é que ele para de gastar atenção no mecânico e passa a gastar no
semântico. O custo de tokens cai menos que a linha do `H01` sugere, porque o agente ainda
precisa ler o que já existe para não duplicar (ver [06-contrato-agente.md](06-contrato-agente.md)).

---

## 3. O protótipo, e o que ele provou

`gen_contract.py` lê um OpenAPI 3.1 e emite o shape do `contracts/*.yaml`. Rodado contra um
fragmento escrito à mão para `POST /api/ingest` (porque **não existe** documento real — Achado A):

```
=== DIFF vs contracts/api-ingest-post.yaml ===
  [SAME] endpoint: generated='POST /api/ingest' human='POST /api/ingest'
  [SAME] auth: generated='api_key' human='api_key'
  [DIFF] idempotency: generated='TODO not in schema' human='header_uuid_v4'
  [DIFF] dedup: generated='TODO not in schema' human='transaction_id'
  [DIFF] async: generated='TODO not in schema' human='worker'
  fields: generated=5 human=5
    [SAME] customer_id
    [SAME] event_type
    [SAME] transaction_id
    [DIFF] properties
      generated={'required': True, 'json': 'properties', 'max_keys': 32}
      human={'required': True, 'json': 'properties', 'flat': True, 'max_keys': 32,
             'denylist': ['cpf','cnpj','cpf_cnpj','ssn','passport','biometric',
                          'face_id','geolocation','lat','lon','latitude','longitude',
                          'precise_location']}
    [DIFF] timestamp
      generated={'required': True, 'json': 'timestamp'}
      human={'required': True, 'json': 'timestamp',
             'window': {'future_minutes': 5, 'past_hours': 48}}
  [DIFF] rules: generated=0 human=3
  [DIFF] p_gaps: generated=0 human=2
```

O que o gerador acertou **sem nenhuma IA**: endpoint, esquema de auth, presença/ausência de
todos os 5 campos, `max_length` de três, `max_keys` e 2 `pattern`.

O que ele não tinha como saber:

| Perdido | Natureza | Como resolver |
|---|---|---|
| `idempotency`, `dedup` | política de API | declaração do projeto |
| `async` | infraestrutura | declaração do projeto |
| `window` (5 min futuro, 48 h passado) | regra de negócio temporal | `x-` no schema, ou declaração |
| `flat` | semântica da API (mapa plano) | sem equivalente em JSON Schema |
| `denylist` (13 campos de PII) | **política de compliance** | jamais gerar; é decisão humana |
| `rules` (3 regras com `status`/`code`) | domínio | autoria |
| `p_gaps` | governança do harness | autoria |

**O caso da `denylist` merece destaque.** É o único item em que gerar seria *perigoso*: uma
lista de campos proibidos por LGPD não pode ser inferida de um schema, porque a ausência da
lista parece igual a "não há restrição". Um gerador que "preenche o que falta" produziria aqui
um case verde que não verifica nada. Isso define uma regra de desenho: **o gerador emite `TODO`
explícito no que não sabe, e o `validate` recusa `TODO`** — exatamente o que o harness já faz
com `expect.status: TODO` (`validate.py`).

---

## 4. Desenho do gerador

Três decisões, cada uma ancorada num resultado acima.

**D1 — Cobertura, não exaustão.** O gerador emite *um* caso por eixo por campo, com nome
derivado (`{area}-N-over-{field}`), nunca uma explosão de valores. O motivo não é elegância:
o fluxo de review exige caso **nomeado** e estável entre runs. Property-based puro (Hypothesis,
Schemathesis) gera o caso que acha o bug, mas o revisor humano não consegue dizer "esta
execução é a mesma de ontem". Ver a comparação com Schemathesis em
[02-descriptor-projeto.md](02-descriptor-projeto.md) §1.

**D2 — `TODO` explícito e `validate` que recusa.** O que não é derivável sai como `TODO`, e o
`validate` falha. Sem isso, o gerador produz contratos que *parecem* completos e não cobrem
nada — o pior resultado possível, porque é silencioso.

**D3 — A origem é o descriptor, não o detector.** O gerador não tenta adivinhar se a API tem
OpenAPI. O descriptor (F2) declara `contract: { source: openapi | dto | bru | inline }`, e o
gerador falha com `CONTRACT_SOURCE_UNAVAILABLE` quando não consegue ler. Isso mantém a escolha
onde ela é auditável.

### Orçamento de geração

Um OpenAPI de 46 rotas com este desenho produz, no máximo, `rotas × eixos_deriváveis` casos.
Medido pelo corpus: 263 casos para 46 rotas e 37 DTOs ⇒ **~5,7 casos por rota**, contra a média
atual de **11,7 casos por rota** (539/46). O gerador cobre a metade mecânica e o humano cobre o
resto — o total não cai, mas a divisão do trabalho muda.

---

## 5. ADR-02: a fonte do contrato passa a ser o schema, com o DTO como fonte adicional

**Contexto.** A regra vigente (`AGENTS.md`, skill `nokr-qa-round` passo 1) diz: *"a fonte do
contract = DTO Java, não o exemplo do playbook"*. Ela existe para impedir que alguém descreva
o contrato a partir de um exemplo de request — que é sempre incompleto. O objetivo é bom.

Mas (Achado B) o harness **nunca lê o DTO**: o campo `dto` é `str` obrigatório e decorativo, e
sua obrigatoriedade torna o formato impossível de usar fora do Java. A regra funciona como
disciplina humana e falha como contrato de ferramenta.

**Decisão.** Três níveis, com precedência:

1. **`source: openapi`** — OpenAPI 3.1 / JSON Schema, quando o alvo publica. Fonte primária.
2. **`source: dto`** — o DTO Java (ou equivalente do projeto), como **fonte adicional** que
   enriquece o que o schema não carrega: `window`, `flat`, `denylist`, `json_alias`. Nunca
   mais obrigatório.
3. **`source: inline`** — contrato escrito à mão. É o modo do baseline e continua válido.

`dto: str` passa a ser **opcional**, e ganha um papel declarado: `dto` sozinho não é fonte;
`dto` + schema é enriquecimento.

**Alternativas consideradas.**

| Alternativa | Por que não |
|---|---|
| Manter DTO como fonte única | exclui toda API não-Java; é o bloqueio do Achado B |
| Só OpenAPI, sem DTO | perde `window`/`flat`/`denylist`, que são exatamente o semântico |
| Extrair o DTO por reflexão/AST Java | casa o harness a Java de novo; resolve o Java e piora a generalidade |
| Manter tudo à mão | mantém a metade mecânica como custo de token, que é o alvo de R2 |

**Consequências.**

- Positivas: qualquer API com schema entra; ~49 % do corpus passa a ser gerável; a disciplina
  "não invente a partir de um exemplo" é preservada, agora reforçada por *fonte declarada* em
  vez de por prosa.
- Negativas: o schema pode divergir da realidade (schema drift) ⇒ precisa de `validate --drift`
  contra o alvo vivo. E a NokrAPI precisa adicionar springdoc (**Achado A**), que é trabalho no
  repo do alvo, não no harness.
- Neutras: os 539 cases existentes não mudam. `source: inline` é o modo deles, e continua válido.

**O que falsifica esta decisão.** Se, ao gerar sobre um OpenAPI real e *completo*, a fração
derivável ficar muito abaixo dos 48,8 % medidos aqui, o gerador não paga o próprio custo e a
frente F3 perde para a alternativa "scaffold melhor, sem geração". O número a bater é 263 casos
de 539.

---

## 6. O que esta frente prova

1. **A fronteira é nítida e medida: 48,8 % gerável.** Não é "quase tudo" nem "quase nada".
   Presença, limite e padrão saem do schema; regra, idempotência, ambiente e round-trip não.
2. **O bloqueio principal não é o gerador — é o `dto` obrigatório.** Um campo decorativo que
   nenhum código lê hoje proíbe qualquer projeto não-Java de existir. Corrigi-lo é uma linha de
   schema e destrava mais que a frente inteira.
3. **A NokrAPI não tem schema para gerar.** Isso tira o auto-discovery do caminho crítico da
   fase 1 e o coloca na fase 2, dependente de uma mudança no repo do alvo.
4. **O gerador tem uma regra de segurança:** `TODO` explícito no que não sabe. Gerar silêncio
   sobre a `denylist` de PII seria um case verde que não verifica nada — o pior resultado
   possível num harness de review.
