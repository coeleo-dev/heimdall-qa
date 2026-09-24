# Architecture

Heimdall QA replays HTTP against an API it did not write, checks the answers, and
leaves evidence a human can judge. This document is the shape of that, and the
seams that keep a product out of the middle of it.

The one-line version: **the core asks questions, a project answers them, and every
question has a default that is honest about not knowing.**

---

## 1. The two distributions

| Distribution | Module | Contains |
| --- | --- | --- |
| `heimdall-qa` | `heimdall_qa` | Replaying, packs, run storage, the review UI, the descriptor schema. |
| `heimdall-qa-<id>` | `heimdall_qa_<id>` | A product's domain logic — its price model — plus its content. |

The core has exactly one import direction: **it never imports a provider**. A
provider is found by asking `importlib.metadata` for an entry point in the group
`heimdall_qa.providers`:

```toml
[project.entry-points."heimdall_qa.providers"]
acme = "heimdall_qa_acme"
```

`heimdall_qa.provider.load_provider` looks the id up there, falls back to the module
name convention `heimdall_qa_<id>`, and turns a miss into `PROVIDER_NOT_FOUND` rather
than an `ImportError` nobody can read. `oracle_factory` then takes the `oracle()`
callable from the loaded module and validates that it answers the `Oracle` protocol
at all (`PROVIDER_INVALID` if it does not).

A provider's *content* — cases, contracts, rounds, campaigns, baselines — is not in
`src/`. Content is data, and the descriptor points at where it lives.

### Why the split is physical, not conventional

`examples/toy-provider/gate.sh` builds a wheel, installs it into a venv with nothing
of the product on disk, and runs a real round. If any provider code had leaked into
`src/`, the wheel would fail on import; if any product data had leaked into the
harness's defaults, the example would need it. A boundary that is only a convention
cannot be tested this way — which is the same reason the phase that introduced the
split also introduced the gate. `examples/spring-fixture/gate.sh` is the same idea one
rung up: it discovers a declared Java target from source, generates its cases, and runs
them against the application it built (`§2`).

---

## 2. The project descriptor

`qa/project.yaml` in the target repository, `providers/<id>/project.yaml` as the
fallback the provider ships, and `--descriptor PATH` for one-off overrides. First
match wins, and the winner is written into the run's `summary.json`:

```json
"descriptor": {"origin": "target", "path": "/abs/path/qa/project.yaml"}
```

The loader resolves the origin, so a run can always say which project it measured.

### Shape

Every model in `heimdall_qa.schema.descriptor` is `extra="forbid"`. A descriptor
that silently ignores a key its author believed was doing something is worse than
one that refuses to load.

```yaml
version: 1
project:
  id: acme                 # [a-z0-9-]+; names the provider directory
  name: Acme
provider: acme             # absent means the neutral defaults
content_root: .            # where this project's cases/contracts/rounds live

contract:                  # where this API's own description is read from
  source: [spring, openapi]  # first *installed* reader wins; a list is a fallback
  location: ../openapi.json  # relative to this descriptor, or an http(s) URL
  spec_version: "3.1"

environments:
  sandbox:
    base_url: http://127.0.0.1:8080

auth:                      # how to build a credential header. Never the value.
  api_key:
    header: Authorization
    scheme: bearer
    prefixes:              # credential shape per environment, declared
      sandbox: test_key_
      production: live_key_
  jwt:
    header: Authorization
    scheme: bearer

environment_header:        # the header that selects the logical environment
  name: X-Acme-Environment
  values:
    sandbox: sandbox
    production: production
  isolation: production    # which environment E-isolate / E-conflict cross into

routes:                    # one entry per prefix: the decisions its requests need
  - prefix: /api
    auth: api_key
    budget: default
    async: true            # a worker settles it, so the log check waits
    require_environment_header: true
    target: admin          # serve this prefix from a declared `targets` entry
    catalog_unique: true   # a POST whose *name* the API refuses to duplicate
    pace_key: signup       # serialize requests here, sharing one pacer
  - prefix: /health        # no auth, no budget: the honest minimal entry

budgets:
  default:
    budget: 300
    fail: 1500

errors:
  statuses:                # per axis: 400 vs 422 varies by framework and by axis
    validation: 400
    idempotency_conflict: 409
  envelope: spring         # rfc7807 | spring | code_message | none
  product_packages:        # frames whose presence proves the trace is ours
    - com.acme
  redact:                  # patterns scrubbed from every artifact
    - 'test_key_\w+'

trace:
  header: X-Trace-Id
  prefix: acces-             # the harness stamps <prefix><run>-<step>

log_sources:               # where a correlation line can appear, per service
  - id: web
    path: ../AcmeAPI/logs/acme-web.log
    format: logback
    marker: 'trace_id: \[{trace_id}\]'
    role: sync                 # logs the request itself
  - id: worker
    path: ../AcmeAPI/logs/acme-worker.log
    format: logback
    marker: 'trace_id: \[{trace_id}\]'
    role: async                # logs what a consumer did with it afterwards
    propagate: true            # false = the service was never asked to log this

fixtures:
  locale: en_US
  email_domain: qa.acme.dev
  generators:              # kind -> implementation named by the core's registry
    cpf: {kind: validate_docbr_cpf}
  field_kinds:             # field name -> fixture kind, for auto-fill
    phone: person_name

request_source:            # where request shapes are read from
  kind: bru
  collection: ../bruno/Acme

secrets:
  api_key: {from_file: secrets.local.yaml, key: api_key}
  jwt: {from_env: ACME_JWT}
```

### What the descriptor deletes

The point of the file is the string matching it removes. Each optional block exists
because a hardcoded answer used to live in the runner:

| Declared | Replaces |
| --- | --- |
| `environments[].base_url` | a product's URL in `config.py` |
| `auth` + `prefixes` | the credential-prefix literals a runner matched on |
| `environment_header` | one environment header, spelled out in the core |
| `routes[].auth` | path-prefix matching to guess the credential |
| `routes[].async` | a path substring test |
| `routes[].catalog_unique` | a hardcoded tuple of unique-name endpoints |
| `routes[].pace_key` | a hardcoded onboarding path |
| `routes[].target` | a hardcoded `/admin` prefix |
| `budgets` | per-path timeout literals |
| `errors.product_packages` | the product's Java package prefix |
| `trace.header` / `prefix` | the trace header and prefix the runner assumed |
| `log_sources` | log paths in `config.yaml` |
| `contract.source` | "the contract YAML is the only truth, always" |
| `errors.statuses` | `422` for every failure axis |
| `fixtures.email_domain` / `locale` | a fixed email domain and locale |
| `request_source.collection` | a hardcoded Bruno collection path |
| `secrets` | `secrets.local.yaml` keys read by position |

### Rules the schema enforces

A descriptor is rejected, with a named code, when it:

- has no `project.id`, or an id that is not `[a-z0-9-]+` (`DESCRIPTOR_ID_INVALID`);
- has no `environments` (`DESCRIPTOR_NO_ENVIRONMENT`), or an environment with no
  `base_url` (`DESCRIPTOR_BASE_URL_MISSING`);
- names an `auth` on a route that is not declared (`DESCRIPTOR_UNKNOWN_AUTH`);
- names a `budget` or a `target` that is not declared (`DESCRIPTOR_UNKNOWN_BUDGET`,
  `DESCRIPTOR_UNKNOWN_TARGET`);
- uses a header name that is not an RFC 7230 token (`DESCRIPTOR_INVALID_HEADER`);
- declares the same prefix twice with different auth (`DESCRIPTOR_DUPLICATE_ROUTE`) —
  the longest prefix wins at match time, so a duplicate resolves by document order
  and the reader cannot tell which is in force;
- declares `log_sources` without `trace.header`
  (`DESCRIPTOR_TRACE_HEADER_MISSING`) — otherwise every log pack would report
  "not measured" and the run would never fail;
- gives a log source two ways to carry the id, or one its format cannot use
  (`DESCRIPTOR_LOG_SOURCE_AMBIGUOUS`) — `json-lines` correlates by `marker_field`,
  text by `marker`, and a source that declares both is a contradiction rather than a
  preference;
- gives `marker_field` something that is not a dotted path
  (`DESCRIPTOR_INVALID_FIELD_PATH`) — judged as a *field path*, not as an HTTP header
  name, which is what used to refuse `data.trace.id`;
- names a `provider` that is not a slug (`DESCRIPTOR_PROVIDER_INVALID`), because a
  lookup by name should fail at load time and not as a missing distribution later;
- declares no usable `contract.source` (`DESCRIPTOR_CONTRACT_SOURCE_MISSING`), or
  names an axis in `errors.statuses` the harness does not know
  (`DESCRIPTOR_UNKNOWN_ERROR_AXIS`).

### The contract source is a seam, not a format

`contract.source` names a **reader**, and that is the whole of the descriptor's
opinion about where a contract comes from. Two readers ship with the core, because
neither needs anything the core does not already have:

| `source` | What it reads |
| --- | --- |
| `inline` | nothing — the contract file a human wrote *is* the source |
| `openapi` | an OpenAPI 3.1/3.0 document, YAML or JSON, on disk or at a URL |

Anything else registers under the `heimdall_qa.contract_sources` entry point group
and arrives with its own distribution, so a project that reads its API out of Java
source code installs that reader on purpose and every other project pays nothing
for it. `source` accepts a **list**, and the order is the fallback policy: the
first *installed* reader wins.

```yaml
contract:
  source: [spring, openapi]      # the code first, the document as a fallback
```

A declaration no installed reader can serve fails as `CONTRACT_SOURCE_UNAVAILABLE`
and never as "the contract is empty". That distinction is the reason the seam
exists at all: "I could not read it" and "there is nothing to read" produce the
same generated files and must not produce the same silence.

Every reader returns the same neutral shape — an endpoint has a method, a path, a
body or no body, fields, and the statuses and rules the source could name — so the
generator never learns which format spoke. What a source cannot answer is a `Gap`,
not a guess: an invented denylist or an invented `maxLength` produces a green case
that verifies nothing, which is worse than a named hole.

### `heimdall-qa-spring`, the reader for a Spring Boot API

It is a distribution of its own (`packages/spring/`) and not a corner of the core,
because a Java project and a product's provider both want it and neither should have to
install the other. It reads **text only** — no JVM, no Maven, no `pom.xml` — because
the point is a `pip install` on a machine that has the checkout and nothing else:

```bash
pip install -e packages/spring
heimdall-qa discover --source spring --location ../api/src/main/java --dry-run
```

What it reads, and where each fact comes from:

| Fact | Source in the code |
| --- | --- |
| the route | `@RestController` + class `@RequestMapping` + `@PostMapping`/`@GetMapping`/… |
| the body | `@Valid @RequestBody` and the `record` it names, by simple name |
| `required` / `json` | `@NotBlank`/`@NotNull`/`@NotEmpty`/`@AssertTrue`, `@JsonProperty`, `@JsonAlias` |
| `max_length` | `@Size(max = …)` on a string, or a `^[a-z]{3,128}$`-shaped pattern that bounds itself |
| `max_keys` | `@Size(max = …)` on a `Map` or a collection, where the count is of keys |
| `pattern` | `@Pattern(regexp = …)` |
| `denylist` | the one declared constant whose name says so, marked for confirmation |
| the happy status | the `return`, or the `switch` if the code chooses one — then it is a gap |
| the status of a failure | `@RestControllerAdvice` and the controllers' own `@ExceptionHandler` |
| the error envelope | the record the handlers *build*, not the wrapper they return |

Five decisions are worth keeping, because each one is a case that would otherwise be
generated wrong:

- **A `@Valid` component is read one dotted level down.** Bean Validation cascades, so
  a missing `kyc_profile.name` really is refused; the field is named by its Java path
  and carried on the wire by its JSON path (`json: kyc_profile.country`).
- **`@Size` means a count on a collection and a length on a string.** Reading it as
  `max_length` always generates a `B-max` case that sends one long string where the
  API counts keys.
- **A length is read out of a pattern only when the pattern bounds itself** — anchored,
  one repeated atom, one `{n,m}`. `^\d{3}-\d{4}$` bounds nothing a reader may state.
- **`@Profile` makes a route conditional.** The route is real, so `read` reports it and
  `discover` keeps it off the surface with a section naming the condition: a contract
  for a route the deployment does not serve is a case that fails for a reason the
  product never stated. `--route` is the explicit override for a run where it is on.
- **A status the code chooses is never a status.** `ResponseEntity.status(ex.getStatusCode())`
  and a `switch` over four outcomes leave the axis unset and say so in a note.

Every one of those is a `Gap` when it cannot be answered, and the `discover` report has
a section for the gaps that belong to no route (the `auth` scheme, a denylist to
confirm) and one for the routes it deliberately did not generate.

The provider is what the descriptor points at:

```yaml
# qa/project.yaml, in the project's own repository
contract:
  source: spring
  location: ../src/main/java    # relative to the descriptor
```

A relative `location` is read against the descriptor's own directory, which is where
the person who wrote it was standing. One location is shared by every name in a
`source` list, so an ordered list is only meaningful when the readers accept the same
kind of location — a Java tree and a URL cannot be the two ends of one staircase.

**It was measured against the corpus it has to serve.** 48 authored contracts against
the 93 routes the reader finds in a real Spring tree: 117 fields, 268 mechanical
attribute statements (`required` 117, `json` 117, `max_length` 19, `pattern` 15), zero
disagreements, and five routes it declares it cannot answer for rather than guessing —
the two bodies the code types as `Map<String, Object>` and the three webhook routes
with no `@Valid`. `tests/test_spring_fixture.py` is that measurement as a gate, on the
tree `examples/spring-fixture/` ships: it re-reads the source and fails on the first
hand-fix, which is what makes regenerating a corpus safe. What stays a human's is named
rather than implied — `example` (a value, not a declaration, so `discover` reports a
`baseline` hole), `auth`'s scheme, `rules`, `live_only_rules`, `p_gaps`, `dedup`,
`captures`, `window`, `flat` and `unique_json`.

```
heimdall-qa discover         # contracts/ + cases/ + baselines/ + DISCOVERY.md
```

`discover` writes one contract per endpoint, generates the cases `coverage.expand`
derives from each, and writes down everything it could not derive. Nothing is
invented: an operation with no declared 2xx becomes `status: TODO`, a path parameter
with no known value becomes `path_values: TODO`, and a body with a required field the
source gave no example for is reported as a `baseline` hole — sometimes the only case
that has to send a value *correctly* is the happy one, and an empty baseline cannot.
All three are refused by `validate` and by `run`. Everything else the source cannot
answer — a PII denylist, a timestamp window, a second accepted spelling, a product
rule's per-rule status, or a body the document declares without describing a single
property — becomes a line in the report naming the axis it leaves uncovered and what
closing it would take.

The report separates three things a reader would otherwise conflate:

- **gaps** — coverage nobody wrote, per axis, with what closing it takes;
- **placeholders** — the values nobody supplied, which block the cases that exist;
- **the middle rung** — the axes the source could not answer and the *descriptor*
  does, printed as `the descriptor answers what the source could not`. Showing one of
  those as unanswered would send a reader to fix a fact already written down, and
  showing a declaration next to a contradicting reading is its own section
  (`declared` wins, and the report says the two disagree).

### The Java parity target

`examples/spring-fixture/` is the Java counterpart of the toy provider. The toy
proves the harness runs a target with *nothing* declared; the fixture proves a
declared Java target is measured to the same level the reference product is. Three
routes, one record carrying the four annotations the reader has to understand, one
advice carrying the failure table, one filter refusing the credential where Spring
Security refuses it, one log file with the trace in it — and no domain at all, on
purpose: it is the smallest application that answers every axis the harness asks about.

| Tier | What it does | Needs |
| --- | --- | --- |
| 1 (`tests/test_spring_fixture.py`, always in CI) | reads the committed Java and asserts the IR, then asserts the *committed* contracts and cases are what a fresh `discover` writes plus the two edits a human made | nothing but the reader |
| 2 (`-m slow`) | builds the jar, starts it on a free port, runs the generated round against it | a JDK |

Tier 1 is the half that makes the "zero human edit" claim repeatable: a generator is
not tested by the files it already wrote, so the tree is compared against a fresh
discovery of the same source and every difference must be a named one. Tier 2 is the
half that checks the reader against reality rather than against our reading of it —
the statuses the advice declares are the statuses the application answers.

`bash examples/spring-fixture/gate.sh` makes both claims in one run, and is what a
person debugging a reader uses; the pytest file is what CI runs.

Value verification stays the project's: the fixture declares no `provider`, so its
book is priced by `NeutralOracle`, and the run records `"oracle": "neutral"`. A second
API reaches the reference product's level of value checking by writing its own oracle,
not by inheriting one.

### Content: one case file per area

A corpus is `cases/<area>.yaml` — a map of case id to case — not one file per case.
A round names the file to run the area whole, or the file and a fragment to run one
case in it:

```yaml
include:
  - cases/ingest.yaml                          # every case in the file
  - cases/ingest.yaml#ingest-I-replay           # one of them
```

`#` is the only selector shape; `heimdall_qa.schema.load.iter_cases` resolves both and
is the single place a reference becomes cases, because the loader has no include
logic of its own and every consumer would otherwise reimplement it. Its order is the
file's order — `H01` creating what the `I-*` cases replay is not an accident a sort
may reorder — which is why the loader refuses a duplicated key instead of keeping the
last one, and why a case id is a key rather than a field.

`bin/consolidate-cases --merge` / `--split` moves a corpus between the
two shapes. `--merge` writes the file in the order the rounds already run the cases
in, and rewrites a round's references to a single line only when that round runs the
whole area in that order; a round that runs a subset, or the same cases in another
order, keeps one selector per case. Consolidation shortens the writing and never
reroutes a round — `--merge` followed by `--split` gives back the corpus it started
from, byte for byte in the parsed sense.

---

## 3. `ProjectView`

`ProjectView` is the only thing the HTTP path knows about a project. It wraps the
descriptor plus the paths of the run, and every accessor answers a question the
runner would otherwise have to guess:

```
base_url(env)  url(path, env)  origin(path, env)  route(path)  budget(path)
auth_for(path) auth_named(name) other_auth_names(scheme)
environment_header_name()  environment_value(env)  isolation_environment()
requires_environment_header(path)  waits_for_async_worker(path)
catalog_unique(path)  pace_key(path)
product_packages()  redact_patterns()  trace_header()  trace_prefix()
email_domain()  locale()  field_kinds()  generator_kinds()
log_path(id)  required_log_path(id)  request_collection()
content_root(root)  oracle()  provider_id()  require()
contract_source()  contract_location()
error_status(axis)  validation_status()  error_envelope()
```

Two of them carry a policy worth naming:

- **`required_log_path` raises, `log_path` returns `None`.** A question that must be
  answered raises; a question that may legitimately have no answer does not.
- **`oracle()`** returns the project's oracle, or `NeutralOracle` when no provider is
  declared. This is the only place a provider is resolved.

### The honest-default rule

An unspecified descriptor field means *the harness has no opinion*, and the packs
say so rather than inventing one:

- no `log_sources` → no correlation is attempted, `http.success` and `observability`
  report **skipped**, and `summary.logs_incomplete` stays `0`. Unmeasured is not
  incomplete.
- no `routes` entry for a path → `auth: none`, no budget, no environment header.
- no `provider` → the neutral oracle, which counts what it can price and calls
  nothing a `402`.

Skipped-with-a-reason is the harness's most important output. A check that could not
run is not a check that passed, and a project must never be told it failed something
it never declared.

---

## 4. The pack seam

A pack is a check applied to one HTTP exchange. `run_all(PackContext)` runs the
always-on set and then the status-dependent ones:

| Pack | Fails when |
| --- | --- |
| `http.baseline` | Status differs from `expect.status`, or no `trace_id` came back |
| `security.leak` | A product package path, an unredacted secret, or a Java stack frame is in the response |
| `performance` | `elapsed_ms` exceeds the route's `fail` (or, over `budget`, fails when `performance` is a declared dimension and warns otherwise) |
| `auth.surface` | A credential was accepted where the route declares none, or the wrong one was accepted |
| `mutation` | A `POST`/`PUT`/`PATCH`/`DELETE` on a route declaring `header_uuid_v4` arrived without a UUID-v4 `X-Idempotency-Key` |
| `observability` | A source the project declared carries this trace and has no line for it (fail), or a log message is Portuguese |
| `business.rule` | A `rules[]` outcome from the contract was not met |
| `http.status_origin` | A 5xx arrived (added only when the status is 5xx) |
| `http.success` | A 2xx body is an `{error, traceId}` envelope, a declared source has no line for the trace, or one of them holds an `ERROR` (added on expected 2xx) |
| `http.error` | A 4xx body is not an object, its error code does not match the contract's, it leaks a Java FQCN, it is Portuguese, or it has no matching `traceId` (added on expected 4xx) |

Each pack reads its rules from `ctx.project`, never from a literal. `auth.surface`
asks `ProjectView` which credential the route wants and which other credentials
exist; `security.leak` asks for `product_packages` and `redact_patterns`. That is the
whole of what decoupling the HTTP path meant in practice.

**Waivers** are declared per case, never applied to a check that could not run. A case
may waive a pack with a reason; `NON_WAIVABLE_PACKS` is empty today and exists so that
a future check can refuse to be waived.

---

## 5. The step-kind registry

A suite step declares exactly one kind, and the kinds the core ships are declared in
one place — `heimdall_qa.step_kinds`:

```python
PROBE_BEGIN = "probe_begin"
LOOP = "loop"
PROBE = "probe"

SUITE_STEP_KINDS = frozenset({PROBE_BEGIN, LOOP, PROBE})
```

`SuiteStep` has `extra="allow"` plus a validator that rejects any key nobody
registered, with a message naming it and listing the ones that exist:

```
step kind not registered: ui (registered kinds: loop, probe, probe_begin)
```

This replaced a chain of `if step.ui is not None` branches, and it is why removing the
browser (the `ui` kind) in Etapa 1 was a deletion rather than a rewrite: with the kind
unregistered, a `ui:` step fails at **load time** — before any request is sent —
instead of running as something else or being dropped in silence by an
`extra="ignore"` that used to be there.

The set is frozen because a set is the whole mechanism a step kind needs: the kinds are
core code, and the failure mode this closes is a typo, not an extension point. It is
also the seam a provider extends later — the core stops owning the *set*, keeping only
the ones it ships — and that extension is not built yet, because no second kind exists
to justify the shape of it.

---

## 6. The oracle seam

A run settles what it saw twice: once *while* it runs, when every metering or ingest
response becomes a charged or excluded line in the book, and once *after*, when a
probe compares a surface against that total. Both answers are arithmetic over a
product's price model, which is not a fact about REST in general.

So the core keeps the questions and nothing else:

```python
class Oracle(Protocol):
    def require_pricing_model(self) -> None: ...
    def add_metering(self, *, status_code, body, amount, idempotency_key) -> None: ...
    def add_ingest(self, *, status_code, body, quantity, unit_amount, idempotency_key) -> None: ...
    def included_total(self) -> Decimal: ...
    def expected_after(self, surface, before, total) -> Decimal: ...
    def matches(self, surface, before, after, total) -> bool: ...
    def as_dict(self) -> dict: ...
```

- **`NeutralOracle`** is the default. A 2xx mutation is counted, nothing ever
  decreases on its own, replays are not double-counted, and an amount nobody
  declared is excluded by name (`no_amount`) rather than invented as a zero.
- **A provider's oracle** overrides it. The reference implementation prices `FLAT`
  ingests as `quantity × unit_amount`, rounds the way its product rounds, and knows
  that HTTP `402` and `429` are exclusions and that a `FROZEN` rating never settles.

`heimdall_qa.money` holds the only arithmetic the core is allowed to do on an amount:
build a `Decimal` from what the wire said (`to_decimal`), and compare two amounts as
numbers rather than as strings (`money_equal`). **Rounding is absent on purpose** —
how a product rounds its money belongs to that product's oracle, and a core that
guessed would be wrong somewhere expensive.

### The abort rule, and where it was answered

Etapa 1 carried an explicit instruction: if this seam grew beyond a few methods, stop
and rescope, because a growing interface means the price model wants to be declared
data rather than moved code. It did not grow. What did surface is the shape of the
loop: a suite's `loop` step books every iteration it can price, and "can price" means
the request body carries an `amount`. That is a product-shaped habit still in the HTTP
path, and it belongs to the declarative price model of Etapa 2 rather than to this
seam. It is recorded in `tests/test_toy_example.py` rather than hidden.

---

## 7. Context propagation

Per-step context travels as **explicit keyword arguments** and nowhere else.

`execute_step` takes the case, the contract, the baseline, the config, the client, the
run directory, the step index, the run id, the secrets, the dimensions, the environment
in force, the captures map and the pacer — thirteen named things, all of them required
to be passed by the caller. There is no module global, no thread-local, no ambient
"current run", and therefore no way for a step to be silently executed in the context
of a different step. The reference language for this harness is one that replaced
thread-locals with scoped values; Python's answer here is not to have the state at all.

Every request is stamped with `trace_header` set to `<trace_prefix><run-id>-<step>`,
and the collector reads the log tails written since the request began, looking for
that exact marker. **Every declared source is read, and each one is waited for**: a
source that answers does not end the wait, only the whole set answering does, because
the consumer that settles an asynchronous request always writes after the web process
has answered — and returning on the first hit is what made 208 steps report "worker
logs incomplete" as `skipped` about the very endpoints where correlation is the point.

The wait is a deadline, never a sleep: the collector polls at 20 ms and gives up when
the deadline passes. What happens then is decided by the **declaration**, never by how
long the harness waited (F5 §5):

| The source said | No line arrived | Verdict |
| --- | --- | --- |
| `propagate: true`, `role: sync` | the service was supposed to log it | `fail` |
| `propagate: true`, `role: async`, and the step **accepted** the work (2xx) | the consumer never settled it | `fail` |
| `propagate: true`, `role: async`, and the step was refused (4xx) | nothing was queued | not owed |
| `propagate: false` | nobody asked it to log this | `skipped`, with the reason |
| any, cut by `max_tail_bytes` | the harness cannot say | `warn` |

The status is what makes the async rule honest in both directions: an endpoint that is
asynchronous when it accepts is not asynchronous when it refuses, and demanding a
consumer's line for a 401 would be a failure the product never earned.

Nothing selects a line by time any more. `timestamp` is declared, and it is used to
**order** the entries of a step across services — never to choose them — which is why
there is no window left in the collector and no fallback that invents lines from
proximity (ADR-03 in F5: a ±1 s window around the harness's own clock is a known mode
of failure, not a mitigation).

A project that declares no `log_source` never reaches the collector at all: the run
proceeds with `LogCollection.not_declared()`, which is `measured=False`, and the two
packs that read a trace line report **skipped**. That distinction — unmeasured versus
incomplete — is the reason the collector has two constructors instead of one.

---

## 8. Run storage

```
runs/<stamp>-<round-id>/
  round.yaml      the round as it was, for the record
  summary.json    counts, packs, coverage, latency, descriptor origin, oracle
  evidence.md     the same story as prose
  book.json       what the oracle priced, and every exclusion by name
  steps/NNN-<id>/ request, response, packs, logs, verdict
  probes/<id>/    before, after, delta, oracle
runs/latest -> <the last run>
```

`summary.json` names the oracle as `"neutral"` or as the provider's id, because a run
whose value checks were answered neutrally is a shape and not a price model, and a
reader who took it for one would be reading a claim nobody made.

A step directory is the unit of review, and a verdict is written into it, not into a
database. That is what makes `runs/latest` a usable API for an agent: a run is a
directory of files and an agent reads files.

`run.json` — one versioned document the review UI reads instead of walking the tree —
is Etapa 2. Today `summary.json` plus `steps/*/` is the contract.

---

## 9. The review UI

`heimdall-qa serve` binds `127.0.0.1:7878` and refuses anything else. It is a screen
for a human to judge a run, not a test of a frontend: it is not optional, it is the
reason the harness exists in this shape at all.

- One process reviews one HTTP round at a time (`ROUND_BUSY` otherwise).
- An unparseable round does not start (`ROUND_INVALID`); a parseable but incomplete
  one appears as not ready.
- The server replays nothing that was already replayed: a completed step is reopened
  from disk.
- **Agents never call port 7878.** They read the run directory.

Its copy is in Portuguese; its code, CLI output and documentation are in English.

---

## 10. What is deliberately absent

- **No browser.** The step kind was removed in Etapa 1 and preserved on the tag
  `e3-freeze`. When it returns it returns as a provider, never as core code.
- **No test runner integration.** This is not pytest's job and it does not report to
  it. A round is a review with a human at the end.
- **No schema of a product's business rules.** Rules are `rules[]` in a contract, and
  their expected outcome is declared, not inferred.
- **No guessing.** When the descriptor does not say, the harness skips with a reason.
  The alternative — a default that looks like a fact — is how a sandbox case ends up
  running against production.
