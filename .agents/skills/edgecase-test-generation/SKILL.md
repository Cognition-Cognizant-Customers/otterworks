---
name: edgecase-test-generation
description: >
  Repo-specific mechanics for closing positive/negative/boundary edge-case
  test coverage gaps in OtterWorks using the edge-case mutant harness. Covers
  the catalog, the Makefile targets that drive the verification loop, where
  the target tests live, and how to run them fast.
---

# Edge-Case Test Generation — OtterWorks

Repo-specific mechanics behind the `!edgecase-tests` Playbook. Auto-loaded
when Devin works in this repository.

## What the harness is

`qa/edgecase/` plants realistic bugs (mutants) from `qa/edgecase/mutants.yaml`
into a service module, runs that service's tests, and restores the module.
SURVIVED = the suite stayed green with a live bug = a proven edge-case
coverage gap. The catalog maps each mutant to a category: `positive`,
`boundary`, `negative-input`, `contract`.

## Commands

```bash
make edgecase-list                                # the mutant catalog
make edgecase-verify                              # full run: every mutant, KILLED/SURVIVED table
make edgecase-verify MUTANT=<id>                  # one mutant — the fast inner loop
make edgecase-verify SERVICE=document-service     # one service
```

Exit codes: `0` all mutants killed, `1` survivors remain, `2` harness error
(red baseline, snippet drift). The runner refuses to mutate a suite that is
already red on unmutated code.

## Where things live

| Thing | Path |
|---|---|
| Mutant catalog + per-service test commands | `qa/edgecase/mutants.yaml` |
| Runner | `qa/edgecase/edgecase_mutants.py` |
| document-service target module | `services/document-service/app/services/document_service.py` |
| document-service tests the harness runs | `services/document-service/tests/test_document_service.py` |

The harness runs each service's `test_command` from the catalog — for
document-service that is `poetry run pytest -q tests/test_document_service.py`
inside `services/document-service/`. New edge-case tests belong in that file,
using the existing `db_session` / `owner_id` fixtures from `tests/conftest.py`
(async pytest, in-memory SQLite).

## Fast loop

```bash
cd services/document-service && poetry install --no-root   # once
poetry run pytest -q tests/test_document_service.py        # suite green on real code
cd ../.. && make edgecase-verify MUTANT=<survivor-id>      # prove the kill
make edgecase-verify                                       # final full run for the PR
```

Lint before the PR: `cd services/document-service && poetry run ruff check .`

## Gotchas

- The harness matches the `original` snippet **exactly once** — do not
  reformat `document_service.py`; only test files should change.
- `word_count` uses `str.split()` (any whitespace). A killing test for
  `EDGE-WORDCOUNT-WHITESPACE` needs content where `split()` and `split(" ")`
  disagree, e.g. `"a  b"` (double space) or text with newlines *and* runs of
  spaces.
- Pagination mutants survive when tests assert only `total`; assert the
  returned items for a specific `page`/`size`.
- The API-level tests (`tests/test_documents_api.py`) are not part of the
  harness's test command — do not rely on them for kills.
