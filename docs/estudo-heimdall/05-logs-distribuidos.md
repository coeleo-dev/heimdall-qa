# F5 — Logs distribuídos: modelo de fontes

> **Frente F5 do estudo Heimdall QA.** Documento de estudo, não spec.
>
> Pergunta desta frente: *como correlacionar log de N serviços, em N linguagens, sem depender
> de relógio nem de formato?*
>
> Baseline: `logs/collector.py`, 102 linhas, 2 arquivos fixos.

---

## 1. Veredito

O plano chamou o collector de "a peça mais frágil e a menos visível". A medição confirma, e
aponta **um defeito pior do que o suspeitado**: o collector **abandona a espera pelo log do
worker** assim que encontra a linha do web. Provado por execução (§3.1).

Consequência medida: dos 829 passos com veredito de `observability` no baseline, **208 (25,1 %)
estão `skipped`** com a razão "worker logs incomplete". E **1.656 passos** gravam
`logs_incomplete: false` enquanto o pack que confere os logs diz que não conseguiu conferir.

O defeito que o plano previa (fallback por relógio local, ±1 s) é real, mas **nenhum dos 208
skips vem dele**. O perigo estava noutro lugar, e num lugar que reporta sucesso.

---

## 2. O que a pesquisa mostra

| Sistema | Como correlaciona | O que ensina |
|---|---|---|
| **SigNoz** | campos padrão `trace_id` + `span_id`, injetados pelo SDK OTel; senão, *Trace Parser* extrai de atributos | correlação é **campo**, não texto |
| **SigNoz** (multiline) | `line_start_pattern` recombina **antes** do pipeline; `force_flush_period` para a última entrada | stack trace precisa ser agrupado antes de casar |
| **SigNoz** (timestamp) | *Timestamp Parser* com formato explícito (`strptime`) | formato de tempo é **declarado**, não adivinhado |
| **Loki** | precisa de label / derived field | correlação nativa exige o campo na origem |
| **Tracetest** | manda o mesmo OTLP para o SigNoz e valida comportamento distribuído | validação distribuída se apoia em trace real |

E uma confirmação que vale ouro, porque vem de quem construiu o sistema: **o próprio SigNoz teve
um bug de faixa de consulta por `trace_id` que descartava logs posteriores ao span** (PR #11800
corrigiu com padding). Ou seja: **a janela por tempo é um modo de falha conhecido, até em
sistemas dedicados**. Não é preciosismo remover a nossa janela de ±1 s.

Três lições que o desenho herda: correlação por campo; multiline antes do match; tempo declarado,
nunca inferido.

---

## 3. O collector atual, medido

### 3.1 Defeito A — a espera pelo worker é descartada (provado)

`collect()` faz:

```python
while True:
    web_slice = _read_tail(web_log, web_start_offset)
    worker_slice = _read_tail(worker_log, worker_start_offset)
    web_hit = [...]; worker_hit = [...]
    if web_hit:
        return LogCollection(web_hit, worker_hit, False, None)   # <-- sai pelo web
    if wait_logs_ms <= 0 or monotonic() >= deadline:
        break
    sleep(_POLL_S)
```

Retorna na **primeira** vez que o web casa — com o `worker_hit` que existir naquele instante.
Experimento: worker escreve 100 ms depois do web, `wait_logs_ms=500`:

```
worker file NOW contains 'worker-hit': True
snapshot.worker_lines: []
snapshot.logs_incomplete: False
-> policy asked to wait 500ms for the async worker; collected: 0 worker line(s)
```

E isto é o **caminho normal**, não a exceção: `ingest-H01` tem `wait_logs_ms: 2000` e o contrato
tem `async: worker`. O web escreve na hora (202); o consumidor RabbitMQ escreve depois, sempre.

Onde dói:

| Passo | `observability` | `logs_incomplete` |
|---|---|---|
| Passo síncrono | pass | false |
| **Passo assíncrono (`/api/ingest`)** | **skipped — "worker logs incomplete"** | **false (mentira)** |

O pack que existe para provar a correlação entre serviços é **sistematicamente pulado
exatamente nos endpoints onde a correlação entre serviços é o ponto**. E o campo que deveria
denunciar isso diz que está tudo completo.

### 3.2 O mesmo defeito torna duas causas indistinguíveis

Há dois motivos possíveis para `worker_lines = []`:

1. o worker **não emitou** linha para aquele `trace_id` (defeito de produto — o que o pack
   deveria pegar);
2. o harness **não esperou** o suficiente (defeito de instrumento).

Hoje o veredito é o mesmo (`skipped`) e o campo `logs_incomplete` é `false` nos dois casos.
**A causa 1 é uma falha de observabilidade do produto, e o harness a reporta como "não
apliquei".** É o pior arranjo possível: o falso negativo que esconde o falso positivo.

### 3.3 Quantificado no baseline

| Métrica | Valor |
|---|---|
| Passos com veredito de `observability` | 829 |
| `pass` | 614 (74,1 %) |
| **`skipped`** | **208 (25,1 %)** — todos "worker logs incomplete" |
| `fail` | 4 |
| `waived` | 3 |
| Passos com `logs_incomplete: false` **e** `observability: skipped` | **1.656** |

Os 1.656 excedem os 208 porque contam todos os passos cujo `packs.json` traz o campo — inclusive
os anteriores à inclusão da política. O que importa é a combinação: o campo afirma completude
enquanto o pack afirma que não pôde conferir.

### 3.4 Defeito B — o fallback por relógio local (o que o plano previa)

`_window(lines, request_at)` compara o timestamp **da linha** com `datetime.now(timezone.utc)`
capturado **na máquina do harness** (`runner.py:193`). Se o serviço roda em container com
relógio deslocado, a janela de ±1 s erra — e erra para os dois lados:

- **falso positivo:** pega linhas de outro passo que caíram na mesma janela;
- **falso negativo:** perde a linha correta.

Além disso `_parse_ts` só reconhece `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}` — sem timezone, sem
milissegundos. Qualquer log com `2026-09-01T14:30:00.123Z` não tem timestamp, e linha sem
timestamp é **descartada** pelo `_window` (`if stamp is None: continue`).

Isso cria uma inconsistência silenciosa entre os dois modos: no modo marcador, o stack trace
sobrevive (a busca não precisa de timestamp); no modo janela, **as linhas de stack trace são
descartadas** porque não começam com data. O mesmo erro aparece ou desaparece conforme o caminho.

### 3.5 Defeito C — leitura sem limite

`_read_tail` faz `seek(offset)` e `read()` até o fim, **a cada poll de 20 ms**. Num passo que
gera muito log (um teste de carga, um `burst`, um laço de 7 ingests), isso relê o arquivo
inteiro repetidamente. Não há `max_bytes`. Há reset de offset quando o arquivo encolhe
(rotação), o que preserva o *fim* do arquivo e perde o *começo* — sem avisar.

### 3.6 Onde a política de espera vive hoje

Três lugares, e eles não conversam:

| Onde | O quê |
|---|---|
| `case.wait_logs_ms` | quanto esperar (por caso) |
| `contract.async: worker` | se o endpoint é assíncrono |
| `_require_worker_logs()` | se o pack deve exigir linha do worker |

O collector recebe só `case.wait_logs_ms or 0` (`runner.py:191`). Então um contrato `async: worker`
cujo caso **não** declare `wait_logs_ms` faz o collector não esperar nada — e o pack, que sabe
que é async, pulou. Dois módulos, duas opiniões, nenhuma autoridade.

---

## 4. O descriptor de fontes

Formato proposto, substituindo `LogFiles` (três strings fixas) e o marcador literal.

```yaml
log_sources:
  - id: web
    path: ../NokrAPI/logs/nokr-web.log
    format: text                     # text | json-lines
    marker: 'trace_id: \[{trace_id}\]'   # regex; {trace_id} é substituído literal
    role: sync                       # sync | async  (substitui require_worker_logs)
    timestamp: { format: "%Y-%m-%d %H:%M:%S", timezone: local }
    multiline:
      start: '^\d{4}-\d{2}-\d{2} '   # primeira linha de uma entrada
    max_tail_bytes: 2097152          # teto por leitura; trunca e marca incomplete

  - id: worker
    path: ../NokrAPI/logs/nokr-worker.log
    format: text
    marker: 'trace_id: \[{trace_id}\]'
    role: async
    timestamp: { format: "%Y-%m-%d %H:%M:%S", timezone: local }
    multiline:
      start: '^\d{4}-\d{2}-\d{2} '
    max_tail_bytes: 2097152
```

E o mesmo mecanismo para um serviço que loga JSON estruturado — o caso que o exemplo Nokr não
exercita:

```yaml
  - id: billing
    path: ./logs/billing.jsonl
    format: json-lines
    marker_field: traceId            # caminho no objeto, não regex
    role: async
    timestamp: { field: time, format: "%Y-%m-%dT%H:%M:%S%z" }
    multiline: { start: '^\{' }
```

Cinco decisões embutidas, uma por defeito:

| Decisão | Defeito que fecha |
|---|---|
| `role: sync \| async` na fonte, e espera por fonte | **A** — o collector espera *cada* fonte declarada, não a primeira que responder |
| `marker` / `marker_field` declarados | formato deixa de ser literal do Logback |
| `multiline.start` agrupado antes do match | stack trace deixa de se perder ou depender do modo |
| `timestamp.format` + `timezone` declarados | tempo deixa de ser adivinhado |
| `max_tail_bytes` | leitura limitada por construção |

---

## 5. Cobertos e não cobertos na v1

| Caso | v1 | Como |
|---|---|---|
| Marcador Logback (`trace_id: [x]`) | **coberto** | `marker` regex |
| JSON-lines com `traceId` | **coberto** | `marker_field` |
| W3C `traceparent` | **coberto** | é só outro `marker` regex (`00-{trace_id}-`) |
| N arquivos por serviço | **coberto** | lista de fontes |
| Múltiplos serviços assíncronos | **coberto** | `role: async` por fonte; espera por fonte |
| Multiline / stack trace | **coberto** | `multiline.start` |
| Timestamp não-padrão / com `T` e `Z` | **coberto** | `timestamp.format` explícito |
| Leitura limitada | **coberto** | `max_tail_bytes` |
| **Skew de relógio entre containers** | **não coberto — removido** | o fallback por janela **sai** (ADR-03) |
| Rotação com perda do início | **parcial** | offset reseta, mas o começo se perde; marcar `truncated: true` explicitamente |
| Trace id não propagado a jusante | **parcial** | declarar `propagate: false` na fonte; ausência vira `skipped` **com razão de produto**, não de instrumento |
| Duas instâncias do mesmo serviço | **não coberto** | dedupe por `(fonte, linha)` é trivial, mas não há caso real hoje |
| Docker / k8s / ssh / HTTP | **fora da v1** | o descriptor já acomoda (`path` vira `kind: docker` + `container`); implementar quando houver uso |

O item mais importante da tabela é o penúltimo. Hoje, "não propaga" e "não esperei" são o mesmo
`skipped`. A v1 exige que o descriptor **declare** a propagação, para que:

- `propagate: true` + linha ausente ⇒ **`fail` de produto** (o serviço deveria ter logado);
- `propagate: false` (declarado) ⇒ `skipped` de instrumento, aceito e explicado;
- harness não esperou o suficiente ⇒ **`fail` de instrumento** com hint, nunca `skipped`.

Isso é a mesma regra que a emenda 11 já usa para o browser: **não conseguir medir nunca é `pass`**.
Hoje é exatamente ao contrário no caminho assíncrono.

---

## 6. ADR-03: o fallback por janela de tempo é removido

**Contexto.** `logs/collector.py:57-68` implementa um fallback: se o marcador de `trace_id` não
aparece, ele devolve as linhas cujo timestamp está a ±1 s de `datetime.now()` do host do harness,
e marca `logs_incomplete: true`.

**Decisão.** O fallback é **removido**. Se o marcador não aparece na fonte, o resultado é:

- `logs_incomplete: true`, com `reason` explícito (`marker_not_found` / `source_unreadable` /
  `truncated`);
- **zero linhas fabricadas** a partir de proximidade temporal;
- o veredito do pack é `fail` de instrumento (ou `skipped` com razão, se o projeto declarou
  `propagate: false`).

**Fundamento.**

1. **O fallback troca precisão por silêncio.** ±1 s é enorme num hot path com orçamento de 50 ms
   (`config.yaml: budgets_ms.hot_path`). Um passo de 30 ms e outro de 800 ms caem na mesma janela.
2. **O modo de falha é conhecido e não é exclusivo daqui.** O SigNoz corrigiu exatamente isso
   (janela por `trace_id` descartando logs posteriores, PR #11800). Quem já construiu um sistema
   dedicado de trace concluiu que a janela por tempo é um defeito, não uma mitigação.
3. **A ausência fica informativa.** Hoje, "não achei a linha" e "a linha não existe" são o mesmo
   veredito. Sem o fallback, "não achei" é uma afirmação forte e acionável: ou o produto não
   logou (bug de observabilidade) ou o harness não esperou (bug de instrumento) — e a v1 passa a
   distinguir os dois.
4. **O tempo não some.** `timestamp.format` continua no descriptor, com um uso honesto: ordenar
   linhas de fontes diferentes dentro de um passo, e preencher a `timeline` do artefato. Não serve
   para *selecionar* linhas.

**Alternativas consideradas.**

| Alternativa | Por que não |
|---|---|
| Manter ±1 s, mas com `request_at` da linha | não resolve skew entre hosts; resolve só formato |
| Aumentar a janela para ±10 s | aumenta o falso positivo proporcionalmente; não endereça nada |
| Só desligar por default, com flag para reativar | uma flag que existe é uma flag que alguém liga na pressa; e o problema é o veredito, não o valor |
| Adotar OTel de verdade agora | resolve o problema certo, mas é projeto próprio (a NokrAPI não tem instrumentação OTel) e está fora do escopo de v1 |

**Consequências.**

- Positivas: 208 passos deixam de ser `skipped` ambíguo — passam a `fail` de produto ou
  `skipped` declarado; `logs_incomplete` volta a significar o que o nome diz.
- Negativas: passos que "passavam" com o fallback podem virar `fail` no primeiro dia. Isso é o
  comportamento correto, mas precisa ser **esperado** e não descoberto em CI — por isso a
  migração tem de vir com o baseline dos 4 `fail` atuais como referência, e com a lista de
  `waive` revisada.
- Risco: projetos que hoje dependem do fallback para ter *alguma* linha não terão nenhuma. É
  intencional.

**O que falsifica esta decisão.** Se, depois da remoção, a maioria dos `fail` novos for de
*instrumento* (o harness não esperou) em vez de *produto* (o serviço não logou), então a espera
por fonte ainda está errada — e o problema não era o fallback, era o Defeito A. Medir a
distribuição dos `fail` novos nas três primeiras campanhas.

---

## 7. O que esta frente prova

1. **O defeito real não era o previsto.** O plano suspeitava do fallback por relógio. O defeito
   que morde é o **abandono da espera pelo worker**, provado em 4 linhas de experimento, e ele
   **reporta sucesso enquanto falha**.
2. **25,1 % dos passos têm `observability` pulado**, e o pack que existe para provar correlação
   entre serviços é pulado justamente nos passos assíncronos.
3. **1.656 passos afirmam `logs_incomplete: false` enquanto o pack diz que não pôde conferir.**
   Um campo cujo nome promete mais do que ele mede é um gerador de confiança falsa.
4. **A correção é estrutural e pequena:** esperar *por fonte declarada*, distinguir "não
   propagado" de "não esperei", e remover o fallback. O descriptor de §4 tem os cinco campos que
   fazem isso.
5. **A pesquisa confirma a direção:** correlação é campo declarado, multiline vem antes do match,
   tempo é formato explícito — e janela por relógio é um defeito que até o SigNoz teve de
   corrigir.
