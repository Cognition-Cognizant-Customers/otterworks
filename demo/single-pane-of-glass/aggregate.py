"""Massage + collate the three source feeds into one unified model.

This is the "transformation" step: it rolls up per-department metrics,
reconciles the number the web UI shows a human against the structured
system of record, folds in external market context, and derives a few
plain-language insights for the single pane of glass.
"""
import re


def _human_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024


def _parse_int(text):
    if text is None:
        return None
    m = re.search(r"[\d,]+", str(text))
    return int(m.group(0).replace(",", "")) if m else None


def _status(ok):
    return "ok" if ok else "unavailable"


def aggregate(structured, portal, external):
    structured = structured or {}
    portal = portal or {}
    external = external or {}

    total_bytes = structured.get("total_storage_bytes", 0)
    departments = structured.get("departments", [])
    for d in departments:
        d["storage_human"] = _human_bytes(d["storage_bytes"])
        d["share"] = round(
            100 * d["storage_bytes"] / total_bytes, 1
        ) if total_bytes else 0.0

    # Reconcile the content a human sees in the web UI against the system of
    # record: every file surfaced on screen should map to a real DB record.
    ui_files = portal.get("recent_files") or []
    db_names = {n.strip().lower() for n in structured.get("file_names", []) if n}
    if ui_files and db_names:
        matched = sum(
            1 for f in ui_files if f.get("name", "").strip().lower() in db_names
        )
        reconciled = matched == len(ui_files)
        recon_detail = (
            f"{matched}/{len(ui_files)} files shown in the web portal reconciled "
            f"to the system of record ({len(db_names):,} records)."
        )
    else:
        reconciled = None
        recon_detail = "No UI content available for reconciliation."

    sources = [
        {
            "id": "structured",
            "name": structured.get("source", "OtterWorks Enterprise Drive"),
            "type": structured.get("type", "Structured system"),
            "status": _status(bool(structured)),
            "detail": (
                f"{structured.get('total_files', 0):,} files, "
                f"{structured.get('total_documents', 0)} docs across "
                f"{structured.get('department_count', 0)} departments"
                if structured else "not reached"
            ),
        },
        {
            "id": "portal",
            "name": portal.get("source", "OtterWorks Web Portal (UI only)"),
            "type": portal.get("type", "Browser / computer use"),
            "status": _status(bool(portal.get("recent_files"))),
            "detail": (
                f"Logged in via browser as {portal.get('logged_in_as', '')}; "
                f"read {len(portal.get('recent_files', []))} files + "
                f"{len(portal.get('recent_documents', []))} docs on screen"
                if portal.get("recent_files") else "not reached"
            ),
        },
        {
            "id": "external",
            "name": external.get("source", "World Bank Open Data"),
            "type": external.get("type", "External public web"),
            "status": _status(bool(external.get("indicators"))),
            "detail": (
                f"{len(external.get('indicators', []))} macro indicators for "
                f"{external.get('country', '')}"
                if external.get("indicators") else "not reached"
            ),
        },
    ]

    insights = []
    if departments:
        top = departments[0]
        insights.append(
            f"{top['name']} is the largest footprint at {top['storage_human']} "
            f"({top['share']}% of {_human_bytes(total_bytes)})."
        )
        no_docs = [d["name"] for d in departments if d["document_count"] == 0]
        if no_docs:
            insights.append(
                f"{len(no_docs)} department(s) have files but no rich-text "
                f"documentation: {', '.join(no_docs[:4])}"
                + ("\u2026" if len(no_docs) > 4 else "")
                + "."
            )
    if recon_detail:
        insights.append(recon_detail)
    if external.get("indicators"):
        parts = [
            f"{i['name']} {i['value']}{i['unit']} ({i['year']})"
            for i in external["indicators"]
        ]
        insights.append("External context \u2014 " + "; ".join(parts) + ".")

    return {
        "kpis": {
            "total_files": structured.get("total_files", 0),
            "total_documents": structured.get("total_documents", 0),
            "total_storage_bytes": total_bytes,
            "total_storage_human": _human_bytes(total_bytes),
            "department_count": structured.get("department_count", 0),
            "external_indicators": len(external.get("indicators", [])),
        },
        "sources": sources,
        "departments": departments,
        "file_types": structured.get("file_types", []),
        "activity": structured.get("activity", []),
        "portal": portal,
        "external": external,
        "reconciliation": {"ok": reconciled, "detail": recon_detail},
        "insights": insights,
    }
