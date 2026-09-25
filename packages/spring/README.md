# heimdall-qa-spring

A **contract reader** for [Heimdall QA](../../README.md): it reads the Java source of a
Spring Boot API and answers the questions a contract needs — which routes exist, what
each one takes, what each one declares about its fields, which status it answers, and
which failures the advice maps to which axis.

It reads **text only**. There is no JVM, no compilation, no classpath and no running
application involved; the fixture's gate starts the built API separately, and only
because a round has to be replayed against *something*. Installing this distribution
does not install a JDK.

It is a distribution of its own on purpose. A Spring reader has nothing to do with any
product, and the alternative — living inside a product's provider — would mean a second
Java project had to install that product to read its own source. The core discovers it
by entry point and never imports it.

## Install

```bash
pip install -e packages/spring      # from a checkout
```

or, once published, `pip install heimdall-qa-spring`. Either way it depends on
`heimdall-qa` and brings the core with it.

## Point a project at it

`contract.source` in the project descriptor names the reader, and `contract.location`
is what it reads. For this reader that is a **directory** — the root of the Java source
tree, not a file:

```yaml
contract:
  source: [spring, openapi]           # try the code, fall back to the document
  location: ../api/src/main/java      # relative to this descriptor, or absolute
```

A list is a fallback policy: the first *installed* reader in the declared order wins, so
`[spring, openapi]` reads the code where the code is and the OpenAPI document where it
is not. Because the two readers take different kinds of `location`, a list mixes only
readers that look at the same kind of place.

Then, as with any reader:

```bash
heimdall-qa discover                                   # to the declaration
heimdall-qa discover --route "POST /api/ingest"        # narrow to one endpoint
heimdall-qa discover --dry-run                         # read and report, write nothing
```

`discover` writes one contract per endpoint under `contracts/`, one case file per area
under `cases/`, and a `DISCOVERY.md` naming every gap it could not close.

## What it reads

- `@RestController` / `@Controller` classes, with the path prefix from a class-level
  `@RequestMapping` when there is one — and the whole path on the method when there is
  not, which several real controllers do.
- `@GetMapping`, `@PostMapping`, `@PutMapping`, `@PatchMapping`, `@DeleteMapping`, and
  `@RequestMapping(method = …)`.
- `record` types for the request and response fields: names, types, and whether a field
  is required (including `@NotNull`, `@NotBlank`, `@NotEmpty`, `@AssertTrue` and the
  jakarta validation annotations).
- `@RestControllerAdvice` exception handlers and the statuses they name, as the error
  table for the project's failure axes.

## What it refuses to invent

A reader's value is what it declines to guess. Each of these becomes a `Gap` with the
axis it leaves uncovered, never a plausible-looking default:

- **A status the code chooses at runtime** (a `switch`, a conditional `return`) is
  `None`. Picking one would make every generated case for that route agree with our
  guess instead of with the API.
- **A path parameter with no knowable value** is a TODO the contract carries, and both
  `validate` and `run` refuse it.
- **A `@Profile`-gated controller** is read and reported with the profile it needs, and
  its cases are not generated — the route is real, but a case for a route disabled in
  the target deployment produces failures that say nothing about the product.
- **A business rule, or a constraint a custom annotation performs**, is a gap: the code
  cannot say which axis it costs.

`DISCOVERY.md` separates what is *declared and unread* from what is *read and
different*, so a fact already written in the descriptor is never shown as a gap.

## Tests

```bash
.venv/bin/python -m pytest -q packages/spring
```

The suite needs no JVM: its source-reading half is pure text. The `slow` half — the one
that builds and talks to the fixture — is skipped unless `HEIMDALL_QA_SLOW=1` and a JDK
is present. `examples/spring-fixture/gate.sh` is the end-to-end procedure CI runs, and
the fixture's own `README.md` has the two edits it needs.

The entry point is declared in `pyproject.toml` under
`[project.entry-points."heimdall_qa.contract_sources"]`, which is how the core finds it
without an import. [`contrib/architecture.md`](../../contrib/architecture.md) §1 is the
seam's contract: what a reader is handed, what it must answer, and how discovery
resolves.
