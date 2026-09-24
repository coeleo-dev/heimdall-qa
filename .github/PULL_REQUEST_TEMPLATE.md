<!--
Two things to know before you fill this in.

Nothing belonging to the project you review with this harness may reach this
repository. Not a case file, not a descriptor, not a study, not a worked example — its
provider is where it lives. `bin/audit-remote` refuses a tree that carries one, and CI
runs it, so a leak fails here rather than after publication.

Then: a change is done when the gates are green, not when the suite is. Say which ones
you ran. If you ran none, say that instead of nothing — a review can start from there.
-->

## What this changes

<!-- The change, in a sentence or two. What was wrong, or what is now possible. -->

## Why it is the right shape

<!--
If you added a file: why it earns its place. If you moved a rule: where it is stated
now — a rule stated in two places will diverge, and the second copy is the one that
goes stale.
-->

## The gates

- [ ] `.venv/bin/python -m pytest -q` — the core's suite
- [ ] `.venv/bin/python -m pytest -q packages/spring` — the reader's suite
- [ ] `bin/audit-remote` — nothing of a review target is publishable
- [ ] `bash examples/toy-provider/gate.sh` — a clean wheel still measures an API
- [ ] `bash examples/spring-fixture/gate.sh` — and still reads Java source

<!-- Cross the ones you ran. Leave the rest unticked and say why they do not apply. -->

## If this changes the YAML a project writes

<!--
The YAML is the harness's public interface. A change that stops an existing round from
validating is the change a reader has to hear about, so say it plainly here and add it
to the top of CHANGELOG.md under Unreleased.
-->

- [ ] `CHANGELOG.md` updated
- [ ] no existing round stops validating — or the break is described above
