---
name: tp-pre-pr-self-check
description: Checklist for tech-partnerships migration units before opening a PR.
---

# Tech-partnerships pre-PR self-check

Run every item below and attach the evidence to the unit recon report before
opening a PR. A skipped or unverified item must be listed explicitly rather
than described as green.

- [ ] NULL and missing attribution cannot fail open; they are rejected or
      explicitly attributed according to the unit contract.
- [ ] Every catalog, schema, collection, and table reference is scoped to the
      unit namespace and uses the `ow_tp` / `ow-tp-` prefix.
- [ ] No DDL drops, replaces, or alters a shared table.
- [ ] Retention and cleanup logic is safe on a rerun and does not remove a
      newer run's data.
- [ ] Cleanup paths retain run evidence and recon artifacts.
- [ ] No secrets, tokens, or real distribution-list/email addresses occur in
      source, evidence, or commit history.
- [ ] The parity-versus-tolerance decision matches the contract; it was not
      invented during implementation.
- [ ] Idempotency was proven by an actual rerun, not inferred from code.
- [ ] Recon values were recomputed from the target platform, not copied from
      migration memory or a previous report.
- [ ] Every unverified or untested path is listed in the recon report.
- [ ] The recon report declares `"kind": "recon-report"` and is stored as a
      `*.recon.json` artifact when using the machine-readable report schema.
- [ ] Capability preflight passed for every required path before live work.
- [ ] `make tp-smoke` is green.

If any box is not true, stop before opening the PR and fix it or record the
contractual coverage gap with a concrete reason.
