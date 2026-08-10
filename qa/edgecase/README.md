# Edge-Case Mutant Harness

Prove — programmatically — that a service's test suite covers **positive,
negative, and boundary edge cases**, not just the happy path.

Line coverage says a test *executed* a line; it says nothing about whether the
test would notice the line being wrong. This harness plants each bug from a
curated catalog (`mutants.yaml`) into a service module, runs that service's
tests, and restores the module:

- **KILLED** — at least one test failed while the bug was planted. The suite
  has real coverage for that class of input.
- **SURVIVED** — the suite stayed green with a live bug in the code. That is a
  proven coverage gap, mapped to a concrete edge case.

## Quick Start

```bash
# List the mutant catalog
make edgecase-list

# Plant every mutant and report KILLED/SURVIVED
make edgecase-verify

# Verify a single mutant, or a single service
make edgecase-verify MUTANT=EDGE-SEARCH-ESCAPE-DROPPED
make edgecase-verify SERVICE=document-service
```

`edgecase-verify` exits non-zero while any mutant survives, so it can gate CI.
The runner refuses to mutate a suite that is already red — fix the baseline
first.

## Catalog Categories

| Category | What a survivor means |
|---|---|
| `positive` | happy-path behavior on valid input is untested |
| `boundary` | off-by-one / empty / zero / whitespace edges are untested |
| `negative-input` | hostile or malformed input (wildcards, markup, missing ids) is untested |
| `contract` | a service contract guarantee (idempotence, versioning) is untested |

## Adding a Mutant

Add an entry to `mutants.yaml` with a unique `id`, the `service`, a `category`,
the `file`, and an `original`/`mutated` snippet pair. The `original` snippet
must appear exactly once in the file. Keep mutants realistic — each one should
be a bug a reviewer could plausibly miss.

## Files

```
qa/edgecase/
├── README.md            # this file
├── mutants.yaml         # the mutant catalog (per-service test commands + bugs)
└── edgecase_mutants.py  # the runner (plant → test → restore → report)
```
