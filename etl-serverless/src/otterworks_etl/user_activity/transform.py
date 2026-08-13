"""Pure aggregation logic for the user activity report."""


def merge_user_day(user_totals: dict[str, dict], user_data: dict) -> None:
    uid = user_data.get("user_id", "unknown")
    if uid not in user_totals:
        user_totals[uid] = {
            "user_id": uid,
            "total_actions": 0,
            "active_days": 0,
            "actions_by_type": {},
        }
    entry = user_totals[uid]
    entry["total_actions"] += user_data.get("total", 0)
    entry["active_days"] += 1
    for action_type, count in user_data.get("actions", {}).items():
        entry["actions_by_type"][action_type] = (
            entry["actions_by_type"].get(action_type, 0) + count
        )


def build_trends(daily_summaries: list[dict]) -> dict:
    total_events = sum(d.get("total_events", 0) for d in daily_summaries)
    return {
        "total_events": total_events,
        "peak_active_users": max(
            (d.get("active_users", 0) for d in daily_summaries), default=0
        ),
        "avg_daily_events": round(
            total_events / len(daily_summaries) if daily_summaries else 0, 2
        ),
        "reporting_days": len(daily_summaries),
    }
