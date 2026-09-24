# Reviewing this API with Heimdall QA

`heimdall-qa init` wrote this tree. It is a working round against a `GET /health`
that probably does not exist yet, and it is meant to be edited rather than admired:
the fastest way to understand the harness is to make this one case pass against your
API, then grow it.

```bash
heimdall-qa validate rounds/smoke.yaml     # answers offline, no API needed
heimdall-qa run rounds/smoke.yaml --mode headless
heimdall-qa last-run                       # prints the run directory
heimdall-qa serve                          # opens the review screen for the operator
```

## What each file is for

| Path | Answers |
| --- | --- |
| `qa/project.yaml` | where the API is, and what it needs to be reached |
| `contracts/<area>.yaml` | one endpoint: method + path, auth, fields, rules, baseline |
| `cases/<area>.yaml` | case id → what to expect, what to diff, what to generate |
| `baselines/<area>.json` | the canonical body the cases compare against |
| `rounds/<id>.yaml` | the cases to run, in order, for one environment |
| `suites/<id>.yaml` | a round that repeats a case or compares a surface |

## The order to work in

1. **Point the descriptor at your API.** `project.id` has to be a slug; one
   `environments` entry with a `base_url` is enough to run.
2. **Declare the routes you will exercise.** `routes:` with the auth each prefix
   needs — `/platform/**` a JWT, `/api/**` an API key, `/admin/**` the admin secret.
   A route that is not declared is a case that cannot be written.
3. **Read the API's own contract rather than typing it:**
   `heimdall-qa discover --source openapi --location http://localhost:8080/v3/api-docs`.
   It writes contracts, cases and a `DISCOVERY.md` whose **gaps** are the decisions a
   document cannot state — a rule enforced in code has no field to describe.
4. **Validate until it is quiet.** Every refusal carries a `code`, the `where`, the
   `fix` and the `why`. `heimdall-qa validate --explain` lists every code; that index
   is the reference.
5. **Run it, then read the run.** `logs_incomplete` in `summary.json` means the run
   has not finished answering — a source that owed the trace a line never wrote it.

## Three rules

- **Never invent a value a fixture can generate.** `heimdall-qa fixture KIND`, or
  `generate:` in the case. A memorised document number is a test that passes for a
  reason nobody chose.
- **Never make a failing round pass by editing its expectation.** A waiver needs a
  registered gap id, and a gap is a statement about coverage, not a shrug.
- **Never commit `secrets.local.yaml`.** It is the only place a credential lives.

Delete this file once the tree is yours; it describes the starter, not your API.
