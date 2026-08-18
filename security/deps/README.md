# Dependency CVE remediation harness

A CVE against a third-party JAR is not fixed by editing one version string. It is
fixed when **no module can still reach the vulnerable version**, and when the
services that call that library **behave the same afterwards** — except for the
capability the advisory says must go away. This directory turns both of those
claims into commands that pass or fail.

```
security/deps/
├── advisory.yaml           the advisory under remediation: artifact, vulnerable range, candidate fixes
├── modules.yaml            every JVM module the gate measures, plus explicit exemptions
├── cases/<module>.json     behavior cases per module, each tagged policy=contract|attack
├── expected/<module>.json  recorded transcripts (the evidence the gate compares against)
├── harness/deps_check.py   inventory + advisory gate + suite runner + transcript grading
└── reports/                generated inventory/gate/tests/transcript JSON (gitignored)
```

## The four commands

| Command | Question it answers | Fails when |
|---|---|---|
| `make deps-inventory` | Which module pulls the artifact, at which version, directly or through which parent, and where is the version declared? | a module's tree cannot be resolved (exit 2) |
| `make deps-gate` | Can the vulnerable version still be reached from any module? | any tree contains a version in the advisory's range (exit 1), or a module is unmeasured (exit 2) |
| `make deps-tests` | Does every affected module still build and pass its own suite? | any module's suite fails (exit 1), or a module cannot be run here (exit 2) |
| `make deps-transcript` | Did the libraries' observable behavior change where it must not, and stop where it must? | a contract case changed, or an attack case still resolves (exit 1) |

Run `make deps-inventory` and `make deps-tests` **before** touching a version, so
the "after" numbers have something to be compared against.

A caller that branches on *which* failure happened must not read `make`'s own exit
status: `make` reports any recipe failure as exit 2, which erases the difference
between "vulnerable" (1) and "no verdict reached" (2). Get the harness invocation
with `make -s deps-command` and run the subcommand through it, which is what the
CI workflow does.

Exit 1 is reserved for something the harness *measured*: the vulnerable version is
reachable, a suite failed, or a graded case diverged. Anything else — no `mvn` or
`gradle` on `PATH`, a corrupt recording, any unanticipated crash — measured nothing
and exits 2 with a traceback, so a run that never inspected the estate is never read
as the documented before-state.

The tree readers treat an unreadable version as *no verdict*, never as an absence.
A Gradle coordinate is counted whether it carries a declared version, a version
Gradle overrode (`1.9 -> 1.10.0`) or none at all because a platform/BOM supplies it
(`group:name -> 1.10.0`); a `(c)` constraint entry is not on the classpath and is
not counted; and a `(n)` unresolved coordinate for the advisory's artifact reports
the module `unmeasured` (exit 2) rather than clean.

## Contract cases vs attack cases

Each case in `cases/<module>.json` carries a `policy`:

- **`contract`** — the value must be byte-identical to the recording. These are the
  business behaviors the remediation is not allowed to disturb: rendered report
  banners, notification bodies, portal branding, the strict rejection of an
  undefined variable.
- **`attack`** — the lookup the advisory is about. After remediation it must stop
  resolving, and the template text named by `attack_marker` must survive
  literally in the output. A remediation that leaves the exploit working fails
  here even if the version string changed.

`--stage baseline` grades every case, attack cases included, against the
recording: that is how the before-state proves it still reproduces.
`--stage remediated` applies the policies above. CI picks the stage from the
advisory gate's own verdict, so neither contract can be skipped.

Cases are self-contained: a case that needs a file on disk carries its
`fixture_content` and the harness materializes it, so a recording never depends
on setup the replay does not reproduce.

## Recording is audited

`expected/<module>.json` stores a SHA-256 of the case file it was recorded from.
Edit the cases and every gate reports `stale` and exits 2 — it will not silently
grade against a recording that no longer matches. Re-recording requires a reason
and an explicit override:

```bash
make deps-record REASON="baseline on commons-text 1.9" # first recording
make deps-record REASON="<why the old recording was wrong>" ALLOW_RERECORD=1
```

A red gate is either a real divergence or a defective fixture. Both are fixed at
the root; neither is fixed by re-recording the evidence.

## Which toolchain measures a module

Each module names `tool` candidates most-pinned first — its checked-in wrapper
(`./mvnw`, `./gradlew`), which is what pins the version the module is built with
elsewhere in CI, then the tool on `PATH`. The first candidate that can actually
start runs both the suite and the dependency tree, and every report names it, so
the evidence says which toolchain produced it. A module whose candidates all fail
is reported `unmeasured` (exit 2), never clean: `services/auth-service` ships a
wrapper with no `gradle-wrapper.jar`, and `./mvnw` cannot pin anything on a
machine that cannot reach its distribution, so the fallback keeps the estate
measured instead of silently skipping a module.

## Adding a module

Register it in `modules.yaml`. Discovery cross-checks the registry against the
`pom.xml` / `build.gradle{,.kts}` files on disk: a JVM build file that is neither
registered nor listed under `exempt` (with a reason) fails every command, so the
blast radius cannot quietly become partial. An exemption is itself reported — the
`reason` is required, each one is printed under the inventory table, carried in
`inventory.json`/`gate.json` and named on the `GATE PASSED` line — so moving a
module under `exempt:` cannot turn a vulnerable estate into a silent pass.

To give a module behavior cases, add `cases/<module>.json` and a
`DependencyTranscriptEmitterTest` in its own test sources — the emitter records
outcomes and the harness grades them, so one
comparator governs Java, Kotlin, Maven and Gradle alike.

## Reports

`reports/` is git-ignored: generated output churns the diff. Collect it as a CI
artifact (the workflow does) and paste the summary lines — the inventory table,
the suite counts, the gate verdict — into the PR body as the evidence.
