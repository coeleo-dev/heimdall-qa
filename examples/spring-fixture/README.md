# The Java parity target

A Spring Boot application whose only job is to be read by `heimdall-qa-spring` and
then talked to, so the reader's numbers are checked against a running API instead of
against our reading of one.

It is the Java counterpart of [`../toy-provider/`](../toy-provider/): that one proves
the harness runs with *nothing* declared, this one proves a declared Java target is
measured. Neither carries a price model, a tenant or a domain — a fixture that had
those would be measuring the product, not the seam.

## What it answers

| route | what it is for |
| --- | --- |
| `GET /fixture/health` | a route with no body |
| `POST /fixture/items` | a validated record, an idempotency key, and four `@ExceptionHandler`s |
| `GET /fixture/items/{id}` | a path parameter and the not-found branch |

`ItemRequest` carries the four annotations the reader has to understand — `@NotBlank`
(`required`), `@Size(max = …)` (`max_length`), `@Pattern` (the pattern an `N-pattern`
case breaks) and `@JsonProperty` (the wire name) — on purpose, in one record. A fixture
that covered three of the four would leave the fourth measured by nothing.

`GlobalExceptionHandler` is the failure table the reader reads, and it is written in
the two spellings a real advice uses (`ResponseEntity.badRequest()`,
`ResponseEntity.status(CONFLICT)`) because a table written only one way would not
prove the reader handles the other. `TraceTokenFilter` refuses the credential at the
servlet layer, which is where Spring Security refuses it too — and which is why `auth`
is the one axis no advice can declare.

## Running it

```bash
./mvnw -DskipTests package
java -jar target/spring-fixture-0.0.1-SNAPSHOT.jar
```

It listens on `8120` and writes `logs/fixture.log`, both declared in `qa/project.yaml`
(the descriptor declares the log as `../logs/fixture.log`, because a descriptor's paths
are anchored to its own directory — the same anchoring `contract.location` uses).

## The demo

```bash
./demo.sh            # the fixture up, the round served in the review UI
./demo.sh --headless # the fixture up, the round run and summarised, then exit
```

Both start the application on the declared port and point its log at the path the
descriptor declares — asked of `ProjectView.log_sources()`, not written down in the
script — then leave the round on screen: 21 cases over three routes, every axis the
fixture answers. The script stops the application when it exits.

## The gate

```bash
bash gate.sh
```

Five claims, in order: the reader is installed; `discover` reads the live source and
writes a tree that reviews clean; the two human edits (a happy baseline and four path
values) plus the one hand-written case are the whole of the work and the round passes
21/21; nothing measurable comes back unmeasured and the log correlation reads the real
file; and the tree committed here is the tree `discover` writes.

[`tests/test_spring_fixture.py`](../../tests/test_spring_fixture.py) makes the same
claims in pytest, which is what CI runs. Its tier 1 is hermetic — it reads the
committed Java and needs no JDK; tier 2 (`-m slow`) builds, starts and talks to the
application.

## Value verification is the project's

The fixture declares no `provider`, so the run prices its book with `NeutralOracle`:
every 2xx mutation is included and no surface ever decreases on its own. That is a
*shape*, not a price model — which is why the run writes `"oracle": "neutral"`
into `summary.json` instead of leaving a reader to assume the numbers were someone's
arithmetic. A second API reaches that level of value checking by writing its own
oracle, not by inheriting one.

## What the fixture found

It is a parity target, so it is worth recording what it caught that no unit test did:

- **`mechanical_headers` had unreachable arms.** `I-format` generated `expect: 400`
  and sent a perfectly good idempotency key, because the environment arm returned
  before the idempotency arm could run. The reference corpus carries those headers
  because a human wrote them, which is exactly why nothing failed.
- **A discovered POST has no happy body.** `discover` points every contract at
  `baselines/empty.json`; for a route with required fields that body cannot produce a
  `201`, and the report said nothing. It now names `baseline` among the placeholders
  it could not fill.
- **A ghost path value only reaches a `String` id.** The harness replaces a declared
  path value with `ghost_<hex>` and expects the not-found status; a route typed `long`
  answers a type mismatch (400) instead. The fixture types its id as a `String` and
  says why — the harness has no way to be told what a ghost must look like.
- **The validation messages were Portuguese.** Hibernate Validator resolves messages
  from `Locale.getDefault()`, and the `http.error` pack refuses a message that reads
  Portuguese. The pack was right and the fixture was wrong.
