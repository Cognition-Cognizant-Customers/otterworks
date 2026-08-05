# /// script
# requires-python = ">=3.11"
# ///
"""Render a Markdown evidence report from results.json.

Usage: python3 security/runtime-validation/gen_report.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

HERE = Path(__file__).parent
results = json.loads((HERE / "results.json").read_text())

ICON = {
    "CONFIRMED": "CONFIRMED",
    "NOT_CONFIRMED": "not reproduced",
    "SKIPPED_SERVICE_DOWN": "skipped (service down)",
    "ERROR": "error",
}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "n/a": 4}

rows = [r for r in results if r["finding_id"] != "baseline"]
rows.sort(key=lambda r: (SEV_ORDER.get(r["severity"], 9), r["service"]))

confirmed = [r for r in rows if r["status"] == "CONFIRMED"]
skipped = [r for r in rows if r["status"] == "SKIPPED_SERVICE_DOWN"]
notrepro = [r for r in rows if r["status"] == "NOT_CONFIRMED"]

lines: list[str] = []
lines.append("# OtterWorks — Runtime Validation of Security-Scan Findings")
lines.append("")
lines.append(f"_Generated {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())} against the "
             "local `make up` stack._")
lines.append("")
lines.append("This report records the **live** result of exercising each reported "
             "vulnerability against the running services. A finding is `CONFIRMED` only "
             "when the attack actually succeeded at runtime; the raw HTTP evidence is "
             "included verbatim. Reproduce with:")
lines.append("")
lines.append("```bash")
lines.append("make up seed=1   # bring the stack up")
lines.append("uv run security/runtime-validation/validate_findings.py")
lines.append("python3 security/runtime-validation/gen_report.py")
lines.append("```")
lines.append("")
lines.append("## Summary")
lines.append("")
lines.append(f"- **Confirmed at runtime:** {len(confirmed)}")
lines.append(f"- **Not reproduced:** {len(notrepro)} "
             "(control works as designed / not runtime-provable here)")
lines.append(f"- **Skipped (service not running):** {len(skipped)}")
lines.append("")
lines.append("| Severity | Service | Finding | Result |")
lines.append("|---|---|---|---|")
for r in rows:
    lines.append(f"| {r['severity']} | {r['service']} | {r['title']} | {ICON[r['status']]} |")
lines.append("")

lines.append("## Confirmed findings — evidence")
lines.append("")
for r in confirmed:
    lines.append(f"### [{r['severity'].upper()}] {r['service']} — {r['title']}")
    lines.append(f"`{r['finding_id']}`")
    lines.append("")
    lines.append("```")
    for e in r["evidence"]:
        lines.append(e)
    lines.append("```")
    lines.append("")

if skipped:
    lines.append("## Skipped — service could not be built/run")
    lines.append("")
    lines.append("The JVM services (auth / report / notification / analytics) could not be "
                 "built in this environment due to a persistent upstream Maven/Gradle "
                 "`HTTP 429 Too Many Requests` rate limit. The findings below are "
                 "**code-confirmed** by the scan but were not exercised at runtime here; "
                 "re-run the harness once those images build.")
    lines.append("")
    for r in skipped:
        lines.append(f"- **[{r['severity'].upper()}] {r['service']}** — {r['title']} "
                     f"(`{r['finding_id']}`)")
    lines.append("")

if notrepro:
    lines.append("## Not reproduced at runtime")
    lines.append("")
    for r in notrepro:
        lines.append(f"### [{r['severity'].upper()}] {r['service']} — {r['title']}")
        lines.append(f"`{r['finding_id']}`")
        lines.append("")
        lines.append("```")
        for e in r["evidence"]:
            lines.append(e)
        lines.append("```")
        lines.append("")

(HERE / "REPORT.md").write_text("\n".join(lines))
print(f"Wrote {HERE / 'REPORT.md'} ({len(confirmed)} confirmed, "
      f"{len(skipped)} skipped, {len(notrepro)} not reproduced)")
