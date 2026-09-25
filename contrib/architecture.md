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
  summary.json    counts, packs, coverage, latency, descriptor origin, oracle, selection
  evidence.md     the same story as prose
  book.json       what the oracle priced, and every exclusion by name
  steps/NNN-<id>/ request, response, packs, logs, verdict
  probes/<id>/    before, after, delta, oracle
runs/latest -> <the last run>
```

A partial run appends `~<selection>` to the directory name (`~case-note-H01`,
`~from-note-H01`) and writes the same string into `summary.json` as `selection`, empty
for a whole round. The `~` is the separator the dedup suffix already used, so a partial
run is a run of that round and not *the* run of it: `find_latest_run(round_id)`
compares the whole tail, and the round's canonical status stays the whole round's. A
reviewer can therefore run a single case without the tree losing track of what the
round itself last did.

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

`heimdall-qa` with no subcommand opens the client in a native window, in the same
process as an API on an ephemeral loopback port; `heimdall-qa serve` is the same API
and the same bundle on `127.0.0.1:7878`, for a remote box or for devtools. Both refuse
a bind that is not localhost. It is a screen
for a human to judge a run, not a test of a frontend: it is not optional, it is the
reason the harness exists in this shape at all.

- **One process runs one plan at a time** (`ROUND_BUSY` otherwise). A plan is an
  ordered list of units — the rounds a campaign, a flow or a single case adds up to —
  and the invariant that used to be "one round at a time" is the same one, stated at
  the size the screen actually works in.
- **A run is not held by the request that starts it.** A request starts a plan and
  answers; a worker thread walks it; the client is told as it goes over
  Server-Sent Events. Closing the tab does not stop a campaign, and a 30-second probe
  is a spinner rather than a browser waiting on a socket.
- **A plan waiting for a human outranks the selection.** While the engine is parked on
  a step, the detail pane shows that step whatever the tree has selected. A campaign's
  roll-up offers a Cancel and no verdict form, and a walk over 41 rounds has to be
  answerable from where the reviewer already is. The strip keeps the "where am I" —
  unit i/n, step j/m, the case, the clock — and the roll-up is back the moment the
  plan is not waiting.
- **The tree is re-indexed when the plan has something new to say**, on a unit
  boundary and at the end, not on every poll: a round that just finished is only on
  disk by then, and a campaign's roll-up would otherwise read "not reviewed" for every
  round it had already walked. Globbing `runs/` twice a second would be a cost the
  review screen does not need to pay.
- An unparseable round does not start (`ROUND_INVALID`); a parseable but incomplete
  one appears as not ready, and a plan that contains one skips it with the reason the
  collection already worked out instead of silently leaving a hole.
- The server replays nothing that was already replayed: a completed step is reopened
  from disk.
- **Agents never call port 7878.** They read the run directory.

Its copy is in Portuguese; its code, CLI output and documentation are in English.

### 9.1 Granularity

The screen offers six scopes, and which ones exist for a node is one table
(`plan.scopes_for`), shared by the buttons that are drawn and the plan that has to
honour them. It answers from the node and not from its kind, because one kind needs
the distinction:

| Selected | Scopes | What runs |
| --- | --- | --- |
| project | — | nothing: "every project I registered" is bigger than a button should answer |
| folder (real, under `campaigns/`) | `directory` | every campaign under that subtree |
| campaign | `campaign` | every round the manifest lists, in the order it lists them |
| flow (a matrix) | `folder` | the rounds of that matrix |
| round | `round` | that round |
| case | `case`, `case_forward` | that case; or that case and everything after it |
| step of a suite round | `round` | that round — a suite step has no case scope (below) |

**A step of a suite round is not a case.** The tree shows a suite round's visible
steps — `loop chain ×2`, `probe two-loops` — and the run's queue is keyed by the same
labels, so they read like cases. They are not: a loop is a count and a probe is a
comparison against a baseline the loops left behind, and both only mean something
inside the sequence that holds them. `scopes_for` therefore offers the round and
nothing narrower, the card says why in words, and `plan_for` resolves that button back
to the round so it cannot run something other than what it names. A round written with
`include:` has real cases and keeps both case scopes.

Where a case's own buttons live follows from the same fact. A case that has already
been reviewed is never drawn as a unit card again — selecting it reopens its step,
which is the point — so `case` and `case_forward` are also drawn on the step's own
read-only bar. Without that, a red case inside a green round could not be run alone.

`case_forward` is not a convenience. `capture` is how a later case receives a value an
earlier one minted, so a case that spends a capture cannot run alone — the smallest
scope that still works is the case that mints it, and forward. Re-deriving the
credential behind the author's back would be the runner inventing a setup the corpus
never declared.

Two facts follow from running a slice instead of a round, and both are deliberate:

- **The order is read from the campaign manifest**, not inferred from the tree. The
  tree groups rounds into one folder per matrix; a campaign that interleaves matrices
  would be reordered by a tree traversal, and the capture chains of §7 would break
  with a wall of 401s that look like a product defect.
- **A partial run never passes for the round.** Its directory is named
  `<stamp>-<round id>~case-<id>` (or `~from-<id>`), which `find_latest_run` does not
  accept as the round's, and its `summary.json` carries a `selection` naming the
  slice. The round's status stays the whole round's, which is the only thing it can
  honestly be. The cost is that a case run on its own is not marked in the tree; the
  unit card lists it as a partial run instead.
- **Coverage is checked against the whole round**, before any selection narrows it. A
  subset may legitimately skip kinds it does not include; it may never relax the
  round's own coverage check, or selecting a subset would be a way to start a round
  the harness has already refused.

### 9.2 One renderer, now a desktop client — ADR-05, superseding ADR-04

ADR-04 fixed the review UI as server-rendered Jinja with zero external dependencies
and rejected the SPA with one specific argument, not an aesthetic one:

> SPA (React/Vue) — reescreve 739 linhas por interatividade que não existe; traz
> toolchain para dentro do harness

**That premise is what changed.** The review screen is now the project's primary
interface, and it has to carry an editor: a command palette, tree and panel chrome
that behaves like an application rather than a document, and Monaco over the YAML a
reviewer writes. "Interactivity that does not exist" is no longer the case, and the
toolchain came in the moment an editor did. What ADR-04 protected is kept; what it
rejected on evidence is revised on evidence.

The rule that survives is the one that mattered: **one renderer.** ADR-04's target
was never "Jinja", it was "not two renderers". So:

- **The renderer is now the desktop client** — a React + Tailwind + shadcn/ui SPA,
  with Monaco for code. Jinja is retired, not kept alive beside it: a server template
  and a client component describing the same panel is exactly the drift ADR-04
  refused, and running both "for a while" is how that drift becomes permanent. The
  transition is bounded by the phase plan in §9.5 and ends by deleting `templates/`,
  `static/style.css` and `static/app.js`.
- **The data boundary becomes a declared contract.** It used to be
  `panel.fragment_context()`, a loose `dict[str, Any]` shaped like HTML (`body_open`,
  `scope_labels`, `case_prefix`). The client cannot read that; it needs models. Those
  live in `serve/models.py` as Pydantic types, `serve/contract.py` translates the
  panel payloads into them, and `serve/panel.py` keeps being the one place that reads
  a step directory off disk. The reading is not duplicated — that would be the second
  source of truth this section exists to prevent — and the translation is a separate
  module precisely so that "what the screen shows" and "what the client is promised"
  are two things that can be read side by side.
- **Feedback stops being a poll.** `GET /panel` and its 500 ms interval are replaced
  by `GET /api/events`, a Server-Sent Events stream carrying the engine's phase,
  unit index, feed events and `awaiting_verdict`. One stream, pushed, instead of a
  request every half second that mostly finds nothing changed.
- **The shell is native.** `heimdall-qa desktop` opens a pywebview window onto a
  loopback uvicorn, and this is what makes the app feel like an app rather than a tab.
  pywebview is a core dependency; it is not browser *automation*, and the difference
  is load-bearing (see §10).

**The costs this accepts, stated rather than discovered later:**

- Node enters the maintainer's toolchain and CI. The built bundle is committed, so
  `pip install` never runs npm — the same contract a lockfile has, with a CI job that
  rebuilds and fails on drift.
- pywebview is not pure `pip` on Linux: it needs a system webview (GTK + WebKit2GTK,
  or Qt WebEngine). That is a real onboarding cost and it is why a missing backend
  has to fail with the `apt install …` line and not an `ImportError`.
- That system binding belongs to the *system* Python: `python3-gi` ships
  `_gi.cpython-<the system's version>.so`, so a venv on a different Python — 3.14 from
  a version manager, say — cannot import it whatever `pip` installs, and needs a Python
  the distro's packages match, or `--system-site-packages`. The refusal message names
  `sys.executable` for this reason: which interpreter is short is the one fact neither
  the reader's shell nor the traceback supplies.
- The distributed wheel grows by the client bundle and Monaco.
- `README.md`'s promise of "no browser dependency" is amended, not deleted: what it
  guaranteed was that reviewing an API never required driving one. That still holds.

Two invariants are untouched by all of this, and a change that breaks either is wrong
regardless of how good the screen looks: **agents read the run directory, never the
port** (`AGENTS.md`), and **`serve/bind.py` still refuses any host but loopback**.

One thing the shell must not do is outlive its plan badly. Closing the window does
not silently kill a campaign in flight — the engine owns the plan, not the window,
which is the same rule that made "closing the tab does not stop a campaign" true.

### 9.3 The client talks HTTP to its own process

The desktop shell **does not import FastAPI**; it starts the server and points a
window at it. `tests/test_cli_help.py::test_fastapi_is_confined_to_serve` stays as it
is and keeps holding, which is the cheapest proof that the shell and the server are
genuinely two things.

A JSON API that mutates on loopback is reachable by any page the host's browser
loads, so every `/api/*` route requires a per-run token and checks `Origin`. The HTML
forms escaped this by accident; a JSON surface has to do it on purpose.

### 9.4 The review UI's shape after the change

The screen keeps the structure the corpus already knows, because it was arrived at by
reviewing hundreds of steps and not by taste:

- the collection as a tree, with the status filters and the search box;
- the five scopes of `plan.scopes_for`, and the rule that a suite step is not a case;
- the verdict bar with `A` / `R` / `Shift+R` / `/` / `J` / `K`;
- the unit card, the campaign roll-up, and the end-of-run KPIs.

What changes is how it is drawn and how fast it answers.

### 9.5 The transition is bounded

| Phase | What landed | What still existed then |
| --- | --- | --- |
| 0 | ADR-05, packaging fix, gate hygiene | Jinja |
| 1 | `/api` + models + SSE, tested | Jinja (still the UI) |
| 2–3 | the SPA reaching parity, read-first | Jinja (still served) |
| 4 | `heimdall-qa desktop` | Jinja (still served) |
| 5 | Monaco editing, save/validate | Jinja (still served) |
| 6 | **Jinja, `style.css` and `app.js` deleted** | one renderer |

The debt is paid: at the end of phase 6 the only renderer is the client, `serve/app.py`
mounts nothing else, and the retired page routes are a 404 asserted by
`tests/test_desktop_shell.py`. Two facts from the phases are worth keeping, because
each one is a claim that was written down before it was true and then measured:

- **The client is two languages wide, not a hundred.** The plan accepted Monaco as a
  cost; the `monaco-editor` barrel import would also have registered all ~120 basic
  languages and shipped a hundred chunks nobody can reach. Only the JSON service and
  the YAML tokenizer are wired, which is 2.5 MB instead of 4.0 MB of assets.
- **The core suite never needs the bundle.** Every API test builds its app against
  `stub_client`, so `pytest` on a clone with no Node is green — the same claim the
  `clone` job makes about a product-less install.

### 9.6 Execution lives in the core

`plan.py` and `engine.py` sit in the core, not in `serve/`, though only the UI uses
them today. They know nothing about HTTP or HTML: a plan is a list of units, and the
engine owns one worker thread, one writer to `runs/`, and the verdict handoff. The CLI
and the MCP surface of §9.7 call the same functions, and the rule that the core never
imports `serve` stays intact.

### 9.7 Many projects, real folders, and an MCP server in the window

Three features arrived together because each one is a question about *which root the
client is looking at*, and answering them separately would have produced three answers.

**The tree is multi-root.** A reviewer working across an API and the library that
consumes it has two collections, and re-launching the app to switch between them is
the friction that ends with nobody using the tree. So the roots are remembered in
`~/.config/heimdall-qa/projects.yaml` (`$HEIMDALL_QA_REGISTRY` overrides, which is what
keeps the suite hermetic), and the tree draws all of them:

```
project → directory → campaign → flow → round → case
```

The registry is the reviewer's organisation and not the project's, which is why it
lives in the home directory and never in a commit. Three decisions carry it:

- **The id is the folder name plus a short hash of the resolved path.** A bare name
  collides the moment somebody has two checkouts of one repository; a bare path is
  unreadable in a log line or a tree key. The pair is stable, legible and unique.
- **Registering demands that the directory look like a project** — `qa/project.yaml`,
  or `campaigns/` and `rounds/`. This is the one place the harness grows what it reads,
  and accepting `/` or `$HOME` by accident would turn a slip in a file dialog into a
  very slow index over a home directory.
- **A key is qualified by project**: `campaign:<pid>:<cid>`, `round:<pid>:<rel>`,
  `directory:<pid>:<rel>` and so on, built only by `keys.py`. Before multi-root the
  literals were scattered through `collection.py`, `plan.py` and `workspace.py`; they
  are now constructed in one module, because a format with seven call sites is a format
  that drifts.

**Folders are real directories under `campaigns/`.** `Nova pasta` creates one;
`Mover para…` moves a campaign into one. The new kind is `directory` — *not* `folder`,
which already means the grouping by matrix inside a campaign and keeps meaning it. Two
consequences are load-bearing: the walk under `campaigns/` is `rglob`, so a campaign in
a subdirectory is indexed like any other; and empty directories are kept, so a folder
that was just created is visible immediately and has somewhere to receive a campaign.
Only campaigns move, because the first path component is what decides a document's kind
— a round moved out of `rounds/` would silently turn the editor against another loader.
A campaign's key comes from its own `id:` field, so moving the file changes neither the
key nor the selection, and the entries in its manifest are relative to the content root,
so it does not break its own references either. Git sees one rename.

**The MCP server can run inside the client.** Driving the harness from a model used to
mean a second process with its own working directory and its own idea of which project
it was looking at; with the window open, the two would each index the collection and
neither would see the other's runs. `mcp/runtime.py` runs `streamable_http_app()` under
a `uvicorn.Server` the class owns — `MCPServer.run` builds its own and keeps no
reference, so a switch built on it would turn on and never off — on loopback, asserted
by the same `assert_local_bind` that `serve` and `desktop` pass through. A missing `mcp`
extra is a *state*, not a crash: `MCP_EXTRA_MISSING` with the `pip install` line reaches
the dialog instead of taking the window down.

The three share one invariant: **the engine is still a single worker**, so `ROUND_BUSY`
is global rather than per project. There is no second worker to run your other project
while this one walks, and pretending otherwise would be a promise the run directory
cannot keep — one `runs/latest` per project is already the reason a run is not shared.

### 9.8 A run waiting for a verdict is not a modal

The first version of the parked-run rule read well and behaved badly: while
`engine.awaiting` was set, `_pane` returned `review` for *every* selection, so the
window was pinned to the step under review and the tree stopped answering. The rule that
replaced it is narrower — the parked step holds the pane only while the selection is the
plan that owns it (the round being walked, its cases, or the node the plan started from);
anything else opens what was selected, and `WorkspaceView.pending_key` names the row to
jump back to, which the strip offers as **Ir ao passo em espera** whenever it is not on
screen. The run is still the authority on what is *pending*; it is no longer the
authority on what the reviewer is allowed to look at.

Two client-side rules keep that from being undone by the event stream:

- **A click outranks an older read.** The bootstrap arrives over SSE and a selection is a
  request/response; when a frame written before a click lands after it, the pane snaps
  back to the previous node. `useHarness` stamps each mutation with an epoch and drops
  any read issued before the last applied one, so the stream catches up to the reviewer
  rather than the reverse.
- **One tooltip layer, not nine hundred.** The tree marks rows with `data-tip` and a
  single `TipLayer` at the root answers `pointerover`/`focusin`; a Radix tooltip per row
  is a per-row subscription on a list whose whole point is to be long.
- **A finished plan is not a running one.** `plan_label` outlives the plan — the strip
  names the last one — so a roll-up that asked "is a plan running in this item?" by
  label kept answering yes to a campaign that had just ended: the live card stayed, the
  Start buttons stayed behind "a plan is already running here", and the elapsed clock
  kept climbing, until the process restarted. The predicate now requires
  `!engine.finished`, and `RunEngine._end` is the one place a terminal phase is set, so
  the clock stops with the phase it belongs to.

### 9.9 Indexing reads each file once per change

`WorkspaceSession.refresh()` parses every contract, case, round, suite and campaign to
build the tree, and a campaign is free to include the same round twice. Measured on a
179-file workspace where 70 rounds referenced a shared set, one refresh was 1,059 YAML
parses and 13,775 `model_validate` calls — 7.1 seconds, once per case that finished,
which is the pause that reads as "the window froze". `schema/load.py` now keys the raw
mapping and the validated model on `(path, mtime_ns, size)`, so the second reader of a
file gets a deep copy (mappings) or the model itself (validated), and the same refresh
costs ~120 ms. The content is still trusted to change: the stamp is the filesystem's, so
an edit invalidates both entries without anyone remembering to. Handing out one validated
model to many callers is safe because nothing in this codebase assigns to a field of a
loaded model — the one place that needs a variation calls `model_copy`; the mutable list
`load_cases` returns is wrapped fresh for that reason. Both caches drop wholesale past a
thousand entries, which costs one re-parse of whatever is in use and cannot leave a stale
entry behind.

---

## 10. What is deliberately absent

- **No browser automation.** The step kind was removed in Etapa 1 and preserved on the
  tag `e3-freeze`. When it returns it returns as a provider, never as core code. This
  is a different thing from the desktop shell of §9.2, and the difference is the one
  that matters: Playwright and axe *drive a browser to test a frontend*, and no part
  of reviewing an API should ever need one. pywebview *displays this harness's own
  screen* and automates nothing. The gate that checks this checks for `playwright`
  and `axe`, not for a webview.
- **No test runner integration.** This is not pytest's job and it does not report to
  it. A round is a review with a human at the end.
- **No schema of a product's business rules.** Rules are `rules[]` in a contract, and
  their expected outcome is declared, not inferred.
- **No guessing.** When the descriptor does not say, the harness skips with a reason.
  The alternative — a default that looks like a fact — is how a sandbox case ends up
  running against production.
- **No remote anything.** The registry of §9.7 is one machine's organisation, not a
  team's: it lives in the user's home, the MCP server binds loopback and is switched on
  by hand, and there is no account, no sync and no service to run. The deployment story
  is a `pip install` and a window.
