"""Pure aggregation logic for the analytics pipeline.

Streaming single-pass aggregation over event dicts (no pandas): memory stays
proportional to the number of distinct users/hours, not the number of events.
"""

from datetime import datetime

USER_ID_FIELDS = ("ownerId", "editedBy", "authorId", "deletedBy", "userId")


def resolve_event_type(event: dict) -> str:
    return event.get("eventType") or event.get("event_type") or "unknown"


def resolve_user_id(event: dict) -> str:
    for field in USER_ID_FIELDS:
        value = event.get(field)
        if value:
            return str(value)
    return "unknown"


def resolve_hour(event: dict) -> str:
    ts = event.get("timestamp")
    if isinstance(ts, str):
        try:
            return "%02d" % datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
        except ValueError:
            pass
    return "00"


def aggregate_events(events: list[dict], top_users: int = 100) -> dict:
    user_action_counts: dict[str, dict[str, int]] = {}
    hourly_breakdown: dict[str, dict[str, int]] = {}
    active_documents: set[str] = set()
    active_files: set[str] = set()
    documents_created = documents_edited = comments_added = 0
    files_uploaded = files_shared = files_deleted = 0
    bytes_uploaded = 0

    for event in events:
        etype = resolve_event_type(event)
        uid = resolve_user_id(event)
        hour = resolve_hour(event)

        user_action_counts.setdefault(uid, {})
        user_action_counts[uid][etype] = user_action_counts[uid].get(etype, 0) + 1

        hourly_breakdown.setdefault(hour, {})
        hourly_breakdown[hour][etype] = hourly_breakdown[hour].get(etype, 0) + 1

        doc_id = event.get("documentId")
        file_id = event.get("fileId")

        if etype == "document_created":
            documents_created += 1
            if doc_id:
                active_documents.add(str(doc_id))
        elif etype == "document_edited":
            documents_edited += 1
            if doc_id:
                active_documents.add(str(doc_id))
        elif etype == "comment_added":
            comments_added += 1
        elif etype == "file_uploaded":
            files_uploaded += 1
            bytes_uploaded += int(event.get("sizeBytes") or 0)
            if file_id:
                active_files.add(str(file_id))
        elif etype == "file_shared":
            files_shared += 1
            if file_id:
                active_files.add(str(file_id))
        elif etype == "file_deleted":
            files_deleted += 1
            if file_id:
                active_files.add(str(file_id))

    user_summaries = sorted(
        (
            {"user_id": uid, "actions": actions, "total": sum(actions.values())}
            for uid, actions in user_action_counts.items()
        ),
        key=lambda u: u["total"],
        reverse=True,
    )[:top_users]

    active_users = set(user_action_counts) - {"unknown"}

    summary = {
        "active_users": len(active_users),
        "active_documents": len(active_documents),
        "active_files": len(active_files),
        "total_events": len(events),
        "documents_created": documents_created,
        "documents_edited": documents_edited,
        "comments_added": comments_added,
        "files_uploaded": files_uploaded,
        "files_shared": files_shared,
        "files_deleted": files_deleted,
        "bytes_uploaded": bytes_uploaded,
    }

    return {
        "summary": summary,
        "hourly_breakdown": dict(sorted(hourly_breakdown.items())),
        "user_summaries": user_summaries,
    }


def peak_hour(hourly_breakdown: dict[str, dict[str, int]]) -> dict | None:
    if not hourly_breakdown:
        return None
    hour, counts = max(hourly_breakdown.items(), key=lambda item: sum(item[1].values()))
    return {"hour": hour, "event_count": sum(counts.values())}
