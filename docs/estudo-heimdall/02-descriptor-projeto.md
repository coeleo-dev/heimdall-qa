# F2 — Setup por projeto: o descriptor

> **Frente F2 do estudo Heimdall QA.** Documento de estudo, não spec.
>
> Pergunta desta frente: *qual é o mínimo que torna o harness utilizável numa API REST
> qualquer, sem plugin?*
>
> Insumo direto: [01-mapa-acoplamento.md](01-mapa-acoplamento.md), itens V1–V11.

---

## 1. O que a pesquisa mostra

Comparação do que cada ferramenta exige para descrever "onde está a API, como autenticar e
onde ficam os segredos":

| Ferramenta | Onde mora a config | Camadas de ambiente | Segredos | Papel núcleo × projeto |
|---|---|---|---|---|
| **Bruno** | `.bru` no git, com bloco `vars` | `vars` + `.env` por ambiente | `.env` **fora do git** (`.gitignore`) | projeto declara tudo |
| **Postman** | cloud (workspace) | *environments* nomeados | *secret variables* (não sincronizam) | projeto declara tudo |
| **Insomnia** | cloud ou git sync | local/cloud environments | *secret vars* (referência sincroniza, valor não) | projeto declara tudo |
| **Karate** | código/DSL + variáveis de CI | `karate-config.js` por ambiente | variável de CI | projeto declara em código |
| **Schemathesis** | CLI + env vars | `--base-url` por invocação | env var | quase tudo no núcleo; nada por projeto |
| **Hurl** | `.hurl` | variáveis por arquivo | shell injeta | projeto declara no arquivo |
| **Dredd** | OpenAPI + host | `--server` / env | env var | spec carrega quase tudo |
| **Postman CLI / Newman** | collection + `-e env.json` | arquivo de environment | secret vars | projeto declara tudo |

Três convergências robustas, e são elas que o desenho herda:

1. **Config declarativa versionada; segredo fora do git.** Bruno, Postman e Insomnia
   convergem nisso por caminhos diferentes. É o único ponto em que *todos* acertam.
2. **Camadas nomeadas**: base → ambiente → valor local. Ninguém tem um arquivo único achatado.
3. **Ninguém tenta inferir.** Schemathesis é a exceção que confirma a regra: como não tem
   projetos, não tem o que inferir — e por isso não cobre nada que o schema não descreva.

E uma divergência que importa: **Schemathesis não tem noção de projeto**. Ele recebe um
schema e uma URL. É por isso que ele acha bugs de contrato melhor que todos os outros e
mesmo assim não substitui o harness: ele não sabe autenticar numa cadeia, não sabe
correlacionar log, não sabe comparar tela com ledger. O descriptor existe para cobrir o que
o schema não cobre.

---

## 2. Princípios de desenho

Cinco regras, cada uma ancorada em um achado:

| # | Princípio | Origem |
|---|---|---|
| P1 | **Núcleo não infere política.** Se o comportamento muda com a rota, a rota é declarada. | V3 é o vazamento mais caro |
| P2 | **Papel, não nome de produto.** `targets.api`, nunca `nokr_web`. | V1, V2, e todo o §5 de F1 |
| P3 | **Declarativo e versionado; segredo por referência.** | convergência de Bruno/Postman/Insomnia |
| P4 | **Progressivo.** Um descriptor mínimo funciona; cada bloco é opcional e aditivo. | a rampa de entrada é o ponto de F2 |
| P5 | **O alvo vence o harness.** Se o repo do alvo declara, ele ganha do front matter do harness. | estudo §2 (`estudo-harness-agnostico.md`) |

---

## 3. Onde o descriptor mora

Duas localizações válidas, e a resolução é por precedência:

```
<repo do alvo>/qa/project.yaml      # declarado pelo time da API  -> VENCE
<repo do harness>/providers/<id>/project.yaml   # declarado pelo QA -> fallback
```

**Critério (versionamento + CODEOWNERS):** o descriptor muda quando a **API** muda. Quem
revisa mudança de contrato de API é o time da API. Colocá-lo no repo do alvo faz o revisor
certo aparecer no PR por construção; colocá-lo no harness garante que ele vai apodrecer.

**Por que o alvo vence.** É o que permite um round único cobrir vários repos. O mesmo
`project.yaml` que descreve a API pode ser lido por um round que também toca o dashboard —
e nenhum dos dois precisa conhecer o outro. Sem precedência, um round multi-repo exigiria um
descriptor central que conhece todos os produtos, que é exatamente o acoplamento que F1
mandou remover.

**Onde fica o harness hoje.** `config.yaml` é um arquivo único, achatado, no repo do harness,
com as chaves do produto. Ele é literalmente o anti-descriptor: mistura núcleo (porta da UI,
timeouts) com produto (URLs, arquivos de log) e não tem noção de precedência.

---

## 4. O formato

### 4.1 Nível 0 — o mínimo que funciona

```yaml
# qa/project.yaml
version: 1
project:
  id: minha-api
environments:
  local:
    base_url: http://localhost:8080
```

Nove linhas. É o que responde "funciona em qualquer API REST". Sem auth, sem logs, sem
discovery — o harness roda casos `H01` triviais e os packs de transporte (`http.success`,
`observability` sem correlato de log).

### 4.2 Nível 1 — auth e roteamento

```yaml
auth:
  # Nome do esquema -> como montar o header. O valor vem de segredo, nunca daqui.
  api_key:
    header: Authorization
    scheme: bearer
    prefixes:                     # V6: o formato de credencial é dado do produto
      sandbox: "nk_test_"
      production: "nk_live_"
  jwt:
    header: Authorization
    scheme: bearer
  admin:
    header: X-Nokr-Admin-Secret   # V1: declarado, não fabricado no núcleo

environment_header:               # V1
  name: X-Nokr-Environment
  values:
    sandbox: sandbox
    production: production

routes:                           # V2 + V3: uma tabela, duas decisões resolvidas
  - prefix: /platform/
    auth: jwt
    require_environment_header: true
  - prefix: /admin/
    auth: admin
  - prefix: /api/ingest
    auth: api_key
    budget: fast                  # V3: SLA declarado
    async: true                   # V3: esperar o log do worker
  - prefix: /auth/
    auth: none
    budget: long                  # ex.: onboarding/KYC

budgets:                          # nomes neutros; o valor é do projeto
  fast: { budget: 50, fail: 1500 }
  default: { budget: 1500, fail: 1500 }
  long: { budget: 8000, fail: 8000 }
```

Este bloco resolve **V1, V2, V3 e V6** — os quatro vazamentos que bloqueiam a fase 1. Note
que nada foi inferido: `/api/ingest` é rápido e assíncrono porque está escrito, não porque
o núcleo reconheceu a string.

### 4.3 Nível 2 — erros, trace e logs

```yaml
errors:
  validation_status: 422          # 400 no Express/Django, 422 no Spring/Rails
  envelope: rfc7807               # rfc7807 | spring | code_message | none
  product_packages: ["com.nokr"]  # V9: para decidir se o stack trace é do produto
  redact:                         # V6: o que nunca pode ir para disco
    - "nk_test_*"
    - "nk_live_*"

trace:                            # V10
  header: X-Trace-Id
  prefix: "nokrqa-"               # renomeia junto com o harness
  propagate_to_worker: true       # declarado, não presumido

log_sources:                      # V10 — ver 05-logs.md para o formato completo
  - id: web
    path: ../NokrAPI/logs/nokr-web.log
    marker: 'trace_id: \[{trace_id}\]'
  - id: worker
    path: ../NokrAPI/logs/nokr-worker.log
    marker: 'trace_id: \[{trace_id}\]'
    async: true                   # V3: só este serviço é assíncrono
  - id: admin
    path: ../NokrAPI/logs/nokr-admin.log
    marker: 'trace_id: \[{trace_id}\]'
```

### 4.4 Nível 3 — discovery, fixtures, superfícies

```yaml
contract:
  source: openapi                 # openapi | dto | bru | postman | inline
  location: http://localhost:8080/v3/api-docs   # ou um caminho de arquivo
  # `dto` carrega a lista de raízes do projeto Java; ver 03-auto-discovery.md

fixtures:
  locale: pt_BR
  email_domain: qa.nokr.dev
  generators:
    cpf: { kind: validate_docbr_cpf }
    cnpj: { kind: validate_docbr_cnpj }
  field_kinds:                    # V: _SENTINEL_FIELD_KINDS
    document_number: cnpj
    tax_id: cnpj

targets:
  dashboard:
    base_url: http://localhost:4200
    environment_storage_key: nokr_selected_environment   # V
    primary_response_prefixes: [/platform/]              # V: "a tela carregou"
  admin:
    base_url: http://127.0.0.1:9090

request_source:                   # V11
  kind: openapi                   # openapi | bru | postman | inline
  # kind: bru
  # collection: /caminho/para/colecao
```

### 4.5 Segredos: referência, nunca valor

```yaml
# qa/project.yaml  (versionado)
secrets:
  jwt:            { from_env: NOKR_QA_JWT }
  api_key:        { from_env: NOKR_QA_API_KEY }
  admin_secret:   { from_file: .secrets.local.yaml, key: admin_secret }
```

Resolução, em ordem: **env var → arquivo local gitignored → erro**. Nunca um default
silencioso, nunca um placeholder que "funciona".

```yaml
# .secrets.local.yaml  (GITIGNORED, com .example versionado)
admin_secret: replace-with-real-value
```

Isso é o padrão que Bruno (`.env`), Postman e Insomnia (secret vars) já validaram, e é o que
o repo já faz com `secrets.local.yaml`. A diferença é que hoje o `secrets.example.yaml` não
diz **qual** chave é obrigatória para **qual** fluxo — o descriptor passa a dizer.

---

## 5. Três exemplos preenchidos

### 5.1 API Spring (a Nokr), reduzida ao essencial

```yaml
version: 1
project: { id: nokr, name: Nokr }
environments:
  sandbox:    { base_url: http://127.0.0.1:8080 }
  production: { base_url: https://api.nokr.com }
auth:
  jwt:     { header: Authorization, scheme: bearer }
  api_key: { header: Authorization, scheme: bearer, prefixes: { sandbox: "nk_test_", production: "nk_live_" } }
  admin:   { header: X-Nokr-Admin-Secret }
environment_header:
  name: X-Nokr-Environment
  values: { sandbox: sandbox, production: production }
routes:
  - { prefix: /platform/, auth: jwt, require_environment_header: true }
  - { prefix: /admin/,    auth: admin }
  - { prefix: /api/ingest, auth: api_key, budget: fast, async: true }
  - { prefix: /api/metering, auth: api_key, budget: fast }
  - { prefix: /auth/, auth: none, budget: long }
budgets:
  fast:    { budget: 50, fail: 1500 }
  default: { budget: 1500, fail: 1500 }
  long:    { budget: 8000, fail: 8000 }
errors:
  validation_status: 422
  envelope: spring
  product_packages: ["com.nokr"]
trace: { header: X-Trace-Id }
contract: { source: openapi, location: http://127.0.0.1:8080/v3/api-docs }
```

Observação: o `X-Nokr-Environment` **sobrevive**, mas como *valor declarado* em vez de literal
no núcleo. A generalização não é apagar a identidade do produto — é tirá-la do núcleo.

### 5.2 API Node/Express, sem nada de Nokr

```yaml
version: 1
project: { id: billing-demo, name: Billing Demo }
environments:
  local: { base_url: http://localhost:3000 }
auth:
  api_key: { header: X-Api-Key, scheme: raw }
routes:
  - { prefix: /v1/charges, auth: api_key, budget: fast }
  - { prefix: /v1/, auth: api_key }
budgets:
  fast:    { budget: 200, fail: 1000 }
  default: { budget: 2000, fail: 5000 }
errors:
  validation_status: 400
  envelope: code_message     # { code, message }
trace: { header: x-request-id, prefix: "billing-" }
log_sources:
  - id: app
    path: ./logs/app.log
    format: json-lines
    marker_field: requestId
contract: { source: openapi, location: ./openapi.yaml, spec_version: "3.1" }
```

Este é o caso que prova a fronteira: **nada** aqui é Nokr, e nenhum campo exigiu mudança no
desenho. `scheme: raw` (valor direto, sem `Bearer`) e `format: json-lines` são os dois únicos
recursos que o exemplo Nokr não exercita.

### 5.3 O mínimo absoluto (rampa de entrada)

```yaml
version: 1
project: { id: toy-provider }
environments:
  local: { base_url: http://localhost:8000 }
```

É o descriptor do **provider de brinquedo** exigido por F8. Três blocos, zero auth, zero log.
Se o harness não roda com isto, a promessa de "qualquer API REST" é falsa — e é por isso que
ele é gate, não exemplo.

---

## 6. Validação do descriptor

O descriptor precisa de `validate`, pelo mesmo motivo que o round precisa: um descriptor
inválido que falha em silêncio é pior que um ausente.

Regras candidatas (a confirmar no protótipo):

| Regra | Erro |
|---|---|
| `project.id` obrigatório, `[a-z0-9-]+` | `DESCRIPTOR_ID_INVALID` |
| ao menos um ambiente | `DESCRIPTOR_NO_ENVIRONMENT` |
| toda `routes[].auth` existe em `auth` (ou é `none`) | `DESCRIPTOR_UNKNOWN_AUTH` |
| todo `auth.*.header` é identificador HTTP válido | `DESCRIPTOR_INVALID_HEADER` |
| `routes[].budget` existe em `budgets` | `DESCRIPTOR_UNKNOWN_BUDGET` |
| `trace.header` obrigatório se qualquer passo espera log | `DESCRIPTOR_TRACE_HEADER_MISSING` |
| segredo referenciado e ausente ⇒ falha de **instrumento**, não skip | `DESCRIPTOR_SECRET_MISSING` |
| `contract.location` alcançável (ou `--offline` declarado) | `DESCRIPTOR_CONTRACT_UNREACHABLE` |

A última linha é a mais importante: `DESCRIPTOR_SECRET_MISSING` como falha de instrumento
segue a regra que a emenda 11 já usa para o browser — *não poder medir nunca é `pass`*.

---

## 7. ADR-01: o descriptor é do alvo, com precedência sobre o harness

**Contexto.** O harness precisa saber URL, auth, roteamento, trace e fontes de log de um
projeto. Hoje isso mora num `config.yaml` único no repo do harness, achatado e misturando
núcleo com produto.

**Decisão.** O descriptor é um arquivo por projeto, versionado, com **precedência do repo do
alvo** sobre o do harness:

```
<alvo>/qa/project.yaml          -> vence
<harness>/providers/<id>/…      -> fallback
```

**Alternativas consideradas.**

| Alternativa | Por que não |
|---|---|
| Tudo no `config.yaml` central | é o estado atual: acopla o núcleo a todos os produtos, e não escala para o segundo |
| Tudo no repo do alvo, sem fallback | impede um round que cobre um alvo que ainda não tem descriptor (ex.: subir só para fumaça) |
| Descriptor por round | duplica auth e URLs em 71 rounds; auth não é propriedade do round |
| Convenção sobre configuração (inferir de URLs) | é literalmente V2/V3, o vazamento mais caro que F1 encontrou |

**Consequências.**

- Positivas: o revisor certo aparece no PR; um round cobre vários repos; o núcleo nunca
  importa identificador de produto; o provider Nokr vira um caso do mecanismo, não um caso
  especial.
- Negativas: dois lugares possíveis para o mesmo dado ⇒ precisa de uma regra de precedência
  clara e de um `doctor` que mostre qual arquivo venceu. Adicionar `descriptor origin` ao
  `summary.json` é o custo honesto desta decisão.
- Risco: se o time da API não mantiver o descriptor, ele apodrece no repo do alvo. Mitigação:
  `heimdall-qa doctor` falha quando o descriptor não bate com a API viva (ex.: rota declarada
  que sumiu), e o harness pode rodar em modo `--descriptor-only` para auditar isso sozinho.

**O que falsifica esta decisão.** Se a maioria dos projetos preferir declarar no harness
apesar de ter CODEOWNERS no alvo, a precedência está invertida — e a evidência é qual caminho
os descriptors reais tomam. Medir depois do segundo provider.

---

## 8. O que fica de fora do descriptor, deliberadamente

| Fora | Por quê |
|---|---|
| Valores de segredo | P3. Só referência |
| Casos e rounds | São conteúdo (provider), não setup |
| Os eixos de cobertura (`H`, `O`, `N`, …) | Política do núcleo; o projeto não escolhe como é cobrado |
| A definição de "pass" | Oráculo é do núcleo + hooks do provider, não config |
| Porta da UI de review | É do núcleo (infraestrutura do harness), não do produto |
| A lista de `MatrixSection` | Vira dado *da campanha*, não do descriptor — ver F8 |

---

## 9. O que esta frente prova

1. **A rampa de entrada é de nove linhas.** O nível 0 roda. O nível 1 resolve os quatro
   vazamentos que bloqueiam a fase 1 (V1, V2, V3, V6) num bloco de ~25 linhas que um time de
   API consegue escrever e revisar.
2. **Nenhum campo do desenho é específico de Nokr.** O exemplo Node (§5.2) não exigiu campo
   novo; exigiu usar dois recursos que o exemplo Nokr já não cobre (`scheme: raw`,
   `format: json-lines`).
3. **O descriptor é o instrumento do desacoplamento.** Sem ele, F1 não tem *para onde* mover
   os 11 vazamentos. Com ele, cada item de F1 §3 ganha um destino nomeado.
4. **A decisão de precedência é a única com risco real** (§7), e o que a falsifica é
   observável: qual caminho os descriptors reais escolhem.
