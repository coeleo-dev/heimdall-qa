# Security policy

## What this project is

Heimdall QA replays HTTP against an API the operator already has access to, and
writes down what came back. It holds no service of its own beyond the review screen
it serves on loopback, it stores no credential outside `secrets.local.yaml`, and it
sends nothing anywhere except the origins a project's descriptor declares.

That shape decides what a vulnerability here *is*, so it is worth stating before the
reporting process:

- **A secret that reaches a run directory.** A run's evidence is meant to be readable
  by a person and shareable with a colleague, so a credential that lands in a
  captured request, a header dump or a log excerpt is a leak even though nothing left
  the machine.
- **A secret that reaches a commit.** `secrets.local.yaml` is ignored, `*.local.yaml`
  is ignored, and `bin/audit-remote` refuses to publish a tree that names a product.
  A path around either one is a finding.
- **A binding that is not loopback.** The review screen and the MCP server are for
  the operator's own machine. Either one listening on a public interface exposes the
  evidence of every run, which includes whatever the API returned.
- **A failure that reads as a pass.** A pack that reports `pass` — or `skipped` —
  when it could not measure would make the harness lie, and that is the failure this
  project exists to prevent. It is a security-relevant bug, not a cosmetic one.

Not in scope: a target API's own vulnerabilities. This harness measures them; it does
not fix them, and a report about the API under review belongs to whoever owns that
API.

## Reporting

Report privately, through GitHub's **Security → Report a vulnerability** flow on the
repository. Do not open a public issue, and do not include a live credential or a
customer's payload in the report — describe the shape and let the maintainers ask for
a reproducer.

A useful report says: what you ran, what you expected, what happened, and the version
(`heimdall-qa --version`) or commit. A run directory that shows the problem is the
best evidence there is, as long as the credential that leaked is the fixture's own
and not a real one.

## What to expect

This is a small project maintained in the open. The commitment is honest rather than
corporate:

- an acknowledgement within about a week;
- an assessment, including "this is a documented design decision", within two;
- a fix or a public advisory as soon as a fix exists, credited to the reporter unless
  they ask otherwise.

If a report is a design decision you disagree with rather than a defect, say so and
it will be argued on its merits in the open. The design decisions that are *not* open
for argument are the four above: a secret in a run, a secret in a commit, a public
bind, and a check that reports a result it did not measure.

## Supported versions

The harness is pre-1.0, so the supported version is the tip of the default branch.
Fixes land there and are released as they are made; there is no backport branch.
