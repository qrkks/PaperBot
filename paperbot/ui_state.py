from __future__ import annotations

from typing import Any, Mapping, Sequence


def first_present_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        return value
    return None


def coerce_selectbox_value(
    selected_value: str | None,
    options: Sequence[str],
    *,
    default: str = "",
) -> str:
    if selected_value in options:
        return str(selected_value)
    if default in options:
        return default
    return options[0] if options else ""


def format_history_entry_label(entry: Mapping[str, Any]) -> str:
    created_at = str(entry.get("created_at", "") or "")
    event_type = str(entry.get("event_type", "") or "")
    query = str(entry.get("query", "") or "")
    return f"{created_at} | {event_type} | {query}"


def coerce_history_entry_id(
    selected_id: str | None,
    history_entries: Sequence[Mapping[str, Any]],
) -> str:
    options = [""] + [str(entry.get("id", "") or "") for entry in history_entries]
    return coerce_selectbox_value(selected_id, options, default="")
