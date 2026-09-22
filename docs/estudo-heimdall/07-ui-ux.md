# F7 — UI/UX: decisão de arquitetura

> **Frente F7 do estudo Heimdall QA.** Documento de estudo, não spec.
>
> Pergunta desta frente: *a UI de review fica server-rendered, vira export estático ou vira SPA?
> E qual é o ganho real de remover a regra da porta 7878?*
>
> Escopo: **estudo agora, execução por último** (fase 3). O que se decide aqui serve para não
> construir a fase 1 de um jeito que a fase 3 precise desfazer.

---

## 1. Veredito

A pergunta "server-rendered vs export estático vs SPA" **já está respondida no código**, e a
resposta é boa:

- a UI é **server-rendered com zero dependências externas** — `base.html` carrega apenas
  `/static/style.css` e um `<script>` inline para largura de coluna. Nenhum CDN, nenhum HTMX,
  nenhum framework JS. O §5.4 da emenda ("nenhuma dependência de serviço externo") já está
  satisfeito.
- **O export estático não é alternativa ao server-rendered — é o mesmo renderizador com outro
  transporte.** Provado em §4: os mesmos templates Jinja renderizam para arquivo com
  `request=None`.

E o "ganho de remover a regra da porta 7878" é menor do que parece, mas por um motivo melhor: a
regra não é sobre a porta. Repetida **25 vezes em 7 arquivos**, ela é sintoma de uma invariante
que a arquitetura não consegue expressar. Formulada como invariante de camada, ela **vira um
teste** — e o número da porta desaparece da regra.

---

## 2. Inventário dos blocos de review

O painel de review vive em `_detail.html` (127 linhas). Oito blocos de conteúdo e um de ação:

| # | Bloco | Fonte no run dir | Fase |
|---|---|---|---|
| 1 | Packs (alertas + passados, colapsável) | `packs.json` | **2** |
| 2 | Esperado vs lido (tabela de 6 colunas) | `oracle.json` | **2** (superfícies `from: api`) |
| 3 | Linha HTTP (status · ms) | `response.json` + `timing.json` | **2** |
| 4 | Body da request (com aviso de REDACTED) | `request.json` | **2** |
| 5 | Headers da request | `request.json` | **2** |
| 6 | Body da response | `response.json` | **2** |
| 7 | Logs web | `logs-web.txt` | **2** |
| 8 | Logs worker | `logs-worker.txt` | **2** |
| A | Formulário de veredito (comentar + aprovar/reprovar/seguir) | — (ação) | **2** |

O que o **E6** acrescenta, pelo próprio texto do entregável ("ARIA lido, esperado vs lido por
superfície, violações axe com o nó apontado e erros de console com stack, além do screenshot"):

| # | Bloco | Fonte no run dir |
|---|---|---|
| 9 | Árvore ARIA lida | `ui/aria.yml` |
| 10 | Esperado vs lido **por superfície da tela** | `ui/ui-values.json` |
| 11 | Violações axe (regra + nó apontado) | `ui/a11y.json` |
| 12 | Erros de console com stack | `ui/console.log` |
| 13 | Screenshot (quando `ui.visual` não estiver waivado) | `ui/screenshot.png` |

**Leitura estrutural:** o E6 não é uma reforma, é um **acréscimo**. O bloco 2 já existe e serve
os dois casos; o E6 adiciona 4 blocos e estende 1. E `_pane_extra` já é uma função que devolve
`dict[str, Any]` — plugar os blocos de `ui/` é adicionar chaves ao mesmo dicionário.

Isso responde à parte "separar o review de HTTP (fase 2) do review de tela (fase 3)": **a
separação é por bloco, e a arquitetura já a suporta.** Nada na fase 1 ou 2 precisa ser
construído pensando no E6, porque o ponto de extensão é um dicionário.

### 2.1 Onde a fase 3 realmente custa

| Item | Custo |
|---|---|
| 4 blocos novos em `_detail.html` | ~60 linhas de template |
| 4 chaves em `_step_payload` | ~25 linhas de leitura de JSON |
| `a11y.json` no `run_store` | já existe (E3) |
| `_pane_extra` (E6 §7.9) | já existe |
| Teste do ramo novo no `serve` | 1 teste |

O E6 é uma das menores etapas da emenda — o que é uma boa notícia para a fase 3, porque ela
chega depois de todo o resto.

---

## 3. Os três caminhos

| Critério | Server-rendered (atual) | Export estático | SPA |
|---|---|---|---|
| Dependências | **0** | 0 | build toolchain + framework |
| Offline / arquivável | não | **sim** | não sem server |
| Veredito interativo (POST) | **sim** | não | sim |
| Linhas de código hoje | 386 + 353 + templates | +~20 | reescrita |
| Risco de divergir do render | — | **0 se reusar Jinja** | alto |
| Atende §5.4 (sem serviço externo) | **sim** | sim | sim, mas com ecossistema pesado |

**O SPA não se justifica por nenhum requisito.** Não há interatividade rica (o total de JS hoje
é um `localStorage.getItem`); não há tempo real; e a única vantagem real — arquivamento — é
melhor servida por export estático, que não reescreve nada.

**O export estático não é um terceiro caminho, é uma segunda saída do primeiro.** O teste de §4
mostra por quê: o contexto de render é um `dict` puro, sem `url_for`, sem CSRF, sem sessão.

### 3.1 Por que o export estático importa (e não é só conveniência)

O E6 exige que "o painel renderize os quatro blocos a partir da pasta do run, **sem reenviar
HTTP**". Um export estático transforma essa exigência em algo verificável por comando: renderiza
a partir da pasta e compara com o esperado. Sem export, "sem reenviar HTTP" é uma afirmação sobre
o código que só se verifica lendo o código.

E ele destrava um ganho concreto para o **agente** (§5): o agente pode renderizar o painel
localmente e verificar que a superfície de review que o humano verá está correta — sem servidor,
e sem violar a invariante de camada.

---

## 4. O teste que decide: os templates renderizam sem servidor

Construí o mesmo contexto que a rota `_render` constrói, a partir de um run dir real, e renderizei
`_detail.html` com Jinja puro, passando `request=None`:

```
step dir: runs/latest/steps/001-ui-login exists: True
payload keys: ['awaiting_verdict', 'case_label', 'comment_required_error', 'elapsed_ms',
               'has_http', 'http_status', 'is_probe', 'logs_web', 'logs_worker',
               'pack_alerts', 'pack_ok', 'packs', 'pause_reason', 'probe'] ...
rendered bytes: 2057
first 160 chars: <section class="detail">   <h1>001-ui-login</h1>       <h3>Packs</h3>
                 <details class="pack-ok" open>     <summary>Packs que passaram (3)</summary>
VERDICT: the same Jinja templates render to a FILE with request=None.
```

Consequências:

1. **`request` é decorativo no contexto.** Ele é passado em `_render` mas nenhum template o usa
   (não há `url_for`; os formulários postam para URLs literais). Dá para removê-lo.
2. **`_step_payload` é a fronteira correta.** Ela lê de `step_dir` e devolve dados; não conhece
   HTTP. É exatamente o que um export precisa.
3. **Um export custa ~20 linhas:** montar o contexto via `_step_payload`, renderizar, escrever.
   Sem segundo renderizador, sem drift.

---

## 5. A regra da porta 7878, contada e reformulada

### 5.1 O número

| Arquivo | Ocorrências de `7878` |
|---|---|
| `docs/nokr-qa.md` | **10** |
| `.cursor/skills/…/SKILL.md` | 4 |
| `.agents/skills/…/SKILL.md` | 3 |
| `README.md` | 3 |
| `docs/emenda-11-ui-browser.md` | 2 |
| `AGENTS.md` | 2 |
| `config.yaml` | 1 |
| **Total** | **25** |

E, no `AGENTS.md` do `nokr-qa`, a regra aparece em dois parágrafos separados. `bind.py` —
o módulo que de fato guarda a porta — tem **16 linhas** e **não menciona 7878**: ele só exige
`127.0.0.1`/`localhost`.

**Uma regra repetida 25 vezes em 7 arquivos é uma regra que a arquitetura não consegue
expressar.** A repetição é o sintoma; o problema é não haver onde colocá-la.

### 5.2 O que a regra realmente protege

A formulação atual (duas versões, convivendo):

> "Não chame `http://127.0.0.1:7878`."
> "O agente **não** chama a porta 7878. A pasta do run (`runs/latest`) é a API."

A segunda forma diz o motivo. O que ela protege **não é a porta** — é uma invariante de
dependência:

```
        run dir  ──────►  é o contrato
        ╱        ╲
   UI (humano)   agente
   consumidores irmãos, não em cadeia
```

Se o agente dirigisse a UI, ele passaria a depender de uma **renderização** do run dir em vez do
run dir. A renderização é lossy (só os blocos que alguém escolheu), muda quando o CSS muda, e
acrescenta um processo móvel ao caminho do agente. O agente passaria a não conseguir responder
"o que este run contém" sem um servidor de pé.

### 5.3 A reformulação

> **Invariante.** O run dir é o contrato de leitura. A UI de review e o agente são consumidores
> irmãos dele; nenhum é pré-requisito do outro.

Vantagens sobre "não chame 7878":

| | Regra hoje | Invariante |
|---|---|---|
| Números frágeis | porta 7878 | **nenhum** |
| Sobrevive a mudar a porta | não | sim |
| Explica o porquê | parcialmente | sim |
| Cobre casos novos (`serve --json`, export, API) | não | **sim** |
| Verificável por teste | não | **sim** (§5.4) |
| Custo de propagação | 25 lugares | 1 nota + 1 teste |

### 5.4 A invariante vira teste (o elo com F6)

Esta é a parte de maior valor, e ela conecta com F6: **a regra em prosa pode virar uma regra de
camada.** Um teste pode afirmar que o caminho do agente não importa nem requisita `serve`:

```
tests/test_layering.py
  - nenhum módulo fora de serve/ importa nokr_qa.serve.*
  - nenhum módulo do núcleo referencia a porta da UI
  - serve/ lê `runs/` via run_store, nunca o contrário
```

Com isso, as 25 ocorrências viram **uma** nota de arquitetura e um teste que falha o build. É a
mesma conversão de F6 §5: prosa que não aplica nada → código que aplica sempre.

**E o ganho que sobra é melhor que "chamar o painel".** Com o export de §4, o agente pode
renderizar o painel a partir do run dir e conferir o artefato do humano — localmente, sem
servidor, e **sem violar a invariante**: ele está consumindo o run dir, apenas com um renderizador
a mais. O ganho não é acesso à UI; é o agente poder verificar o que o humano vai ver.

---

## 6. ADR-04: um renderizador, dois transportes

**Contexto.** A UI de review é server-rendered (FastAPI + Jinja), com zero dependências externas.
O E6 vai acrescentar 4 blocos de tela. Existe pressão para "modernizar" (SPA) e necessidade
potencial de arquivamento offline (export).

**Decisão.**

1. **O renderizador continua server-rendered (Jinja).** O SPA não se justifica: não há
   interatividade rica, e o custo é um build toolchain + framework.
2. **O export estático é uma segunda saída do mesmo renderizador**, nunca um segundo
   renderizador. `serve --export RUN` renderiza os mesmos templates para arquivo.
3. **`request` sai do contexto de render** (é decorativo) e `_step_payload` fica sendo a
   fronteira de dados.
4. **A regra da porta é substituída pela invariante de §5.3**, com um teste de camada.

**Alternativas consideradas.**

| Alternativa | Por que não |
|---|---|
| SPA (React/Vue) | reescreve 739 linhas por interatividade que não existe; traz toolchain para dentro do harness |
| Export como renderizador próprio | dois renderizadores divergem; o E6 garante que o painel lê do run dir, e isso se perde |
| Manter só server-rendered | não dá evidência arquivável nem permite ao agente conferir a superfície de review |
| Guardar a regra da porta como está | 25 cópias, e nenhuma verificável |

**Consequências.**

- Positivas: fase 3 é acréscimo (~85 linhas), não reforma; o painel fica arquivável; a regra da
  porta vira teste; o agente ganha verificação da superfície de review sem servidor.
- Negativas: dois transportes implicam que o export precisa ser exercitado em CI, senão
  apodrece; um teste de camada pode gerar falso positivo se o núcleo precisar legitimamente de
  um utilitário que hoje mora em `serve/` (ex.: `pretty_json`) — nesse caso o utilitário se
  move para o núcleo, não se abre exceção.
- Risco: o export congela um layout que vai mudar na fase 3 (os 4 blocos novos). Mitigação: o
  export exporta o que existe; nenhum gate depende dele antes da fase 3.

**O que falsifica esta decisão.** Se o export nunca for usado por humano nem por agente em três
campanhas, ele é peso morto e deve sair. Medir: quantas vezes alguém abre um arquivo exportado.

---

## 7. O que esta frente prova

1. **A pergunta de arquitetura já tem resposta no código** e ela é boa: server-rendered, zero
   dependências externas. §5.4 da emenda já está satisfeito.
2. **Export estático não é um terceiro caminho** — é o mesmo Jinja com outro transporte.
   Provado: renderiza para arquivo com `request=None`, 2.057 bytes a partir de um run real.
3. **A separação fase 2 / fase 3 é por bloco, e a arquitetura já a suporta.** O E6 é ~85 linhas
   de acréscimo, e o ponto de extensão (`_pane_extra` → `dict`) já existe.
4. **A regra da porta não é sobre a porta.** Repetida 25 vezes em 7 arquivos, ela é sintoma de
   uma invariante de camada sem lugar para morar. Reformulada, vira **um teste** — e conecta
   diretamente com o princípio de F6: prosa que não aplica nada deve virar código que aplica.
5. **O ganho real de destravar o agente é conferir o artefato do humano**, não "chamar o painel" —
   e ele vem do export, sem quebrar a invariante.
