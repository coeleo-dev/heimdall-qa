---
name: Bug report
about: The harness did something it should not have, or failed to do something it should
labels: [bug]
---

<!--
Please do not paste a credential or a customer's payload. A run directory that shows
the problem is the best evidence there is — but if the API under review is real,
describe the shape and let the maintainers ask for a reproducer.
-->

## What happened

<!-- And what you expected instead. -->

## How to reproduce it

<!--
The smallest thing that still shows it. Five lines beat a paragraph:

    heimdall-qa validate rounds/smoke.yaml
-->

## The version

<!-- `heimdall-qa --version`, plus the commit if you are on a branch. -->

## What the harness printed

<!--
The `error[CODE]:` line and its `hint:` are usually enough. A traceback is worth
including when a failure reads like a bug instead of like an instruction — that is
what `--verbose` prints.
-->

## The environment

- Python version:
- OS:
- Provider installed (or none — "none" is a supported answer):
