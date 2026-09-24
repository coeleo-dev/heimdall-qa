---
name: Feature request
about: Something the harness should do, or should stop doing
labels: [enhancement]
---

## What you are trying to find out

<!--
Start with the question, not the mechanism. "I cannot tell whether a retry actually
replayed the request" is a better opening than "add a retry pack" — the harness may
already answer it somewhere you have not looked, and if it does not, the question says
what the answer has to look like.
-->

## What the harness does today

<!-- And why that is not enough. -->

## Where this belongs

<!--
The harness is deliberately separable, and where a thing lands decides whether it can
be contributed at all:

- **the core** (`heimdall_qa`) — measures a property of HTTP. Correct anywhere, and its
  test has no provider installed;
- **a reader** — turns a stack's own source into contracts. `spring` is one; another is
  a distribution of its own;
- **a provider** — what *your* product's answers mean. Its price model, its error
  vocabulary, its fixtures. It lives beside the product, never here.

A feature that needs a product fact is a provider. Saying which one you mean saves a
round trip.
-->

## Anything you have already tried

<!-- Including "nothing", which is a fine answer. -->
