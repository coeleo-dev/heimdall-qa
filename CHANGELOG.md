# Changelog

Every change worth a reader's attention, newest first. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0, a minor bump is the breaking one: the YAML a project writes is the harness's
public interface, and a schema change that stops an existing round from validating is
the change a release note has to lead with.

## [Unreleased]

## [0.1.0] - 2026-09-24

The first public release: the harness, extracted from the project it was built
against, with nothing of that project left in it.

### Added

- **A review harness for a REST API**: YAML `contracts/`, `cases/`, `suites/` and
  `rounds/` under a `qa/` tree, replayed by `heimdall-qa run` into
  `runs/<stamp>-<id>/`, and read back on the review screen from `heimdall-qa serve`.
- **The project descriptor** (`qa/project.yaml`): origins, environments, auth routes,
  request budgets, log sources and fixtures — a target's whole identity in one file
  the harness reads rather than compiles in.
- **Discovery**: `heimdall-qa discover` reads a target's own contract and drafts the
  contracts, cases and a report naming every axis it could not derive. A contract
  source is a seam — `inline` and `openapi` in the core, and readers registered by
  distribution.
- **`heimdall-qa-spring`**, a separate distribution: a reader for Spring Boot source
  trees, so a Java target is discovered from the code itself. Installed only where it
  is used.
- **`heimdall-qa init`**: writes the starter tree a project begins from — the
  descriptor, one contract, one case, the suite and round that include it, a baseline,
  a secrets example and a README. Idempotent; `--force` overwrites only what it
  ships.
- **An MCP server** (`heimdall-qa-mcp`, the `mcp` extra) beside the CLI: the same
  operations as tools, a `heimdall://run/latest/...` resource, and `onboarding` and
  `review_run` prompts. stdio by default, loopback-only for HTTP.
- **Packs** that check what a response actually did — status, schema, leak markers,
  language, timing, idempotent replay — and report `skipped` when a check could not be
  measured rather than a result it did not earn.
- **Log correlation**: a step's `trace_id` is followed into the target's own log
  files, declared as log sources in the descriptor, so a failing response can be
  read beside the server-side line that explains it.
- **A provider seam**: what a product's answers *mean* — its price model, its error
  vocabulary, its fixtures — is a distribution found by entry point. The core asks;
  it never decides a product question, and a project with no provider gets the neutral
  oracle.
- **Two worked examples**: `examples/toy-provider/` (a whole provider small enough to
  read) and `examples/spring-fixture/` (a Spring Boot target for the Java reader).
  Each ships a `gate.sh` that proves its claim on a machine with nothing else
  installed.
- **`bin/audit-remote`**: the gate that keeps a review target out of this repository,
  asserting that the published set carries no product path and no product token, that
  the core names no product, and that the suite passes in the tree exactly as it would
  ship.
- **`bin/bootstrap`**: a fresh clone to a running harness and a green suite in one
  command.
- **Contributor documentation** under `contrib/`, with `contrib/architecture.md` as
  the structural reference, and the `heimdall-qa` skill under `.agents/skills/`.
