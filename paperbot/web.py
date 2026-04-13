from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
from typing import Any

import pandas as pd
import streamlit as st

from paperbot.core import (
    PUBMED_SORT_VALUES,
    SECONDARY_SORT_VALUES,
    apply_secondary_metrics_to_records,
    build_collection_paths,
    chunked,
    ensure_collection_path,
    fetch_pubmed_xml,
    fetch_openalex_metrics_by_pmids,
    find_ambiguous_collection_paths,
    list_collections,
    list_existing_items_info,
    normalize_collection_path,
    parse_pubmed_articles,
    plan_record_import_actions,
    secondary_sort_records,
    search_pubmed_ids,
    validate_library_id,
    zotero_create_items,
    zotero_link_existing_items_to_collection,
)


APP_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = APP_ROOT / ".paperbot_streamlit_settings.json"
HISTORY_FILE = APP_ROOT / ".paperbot_history.json"
ENV_FILE = APP_ROOT / ".env"
SETTINGS_KEYS = [
    "zotero_user_id",
    "zotero_api_key",
    "library_type",
    "library_id_input",
    "pubmed_sort",
    "secondary_sort",
    "attach_metrics_to_extra",
    "skip_duplicates",
    "duplicate_scope",
    "openalex_email",
    "openalex_api_key",
    "ncbi_email",
    "ncbi_api_key",
    "manual_collection_path",
    "selected_collection_path",
    "auto_create_collection",
    "remember_settings",
]
DEFAULT_SETTINGS: dict[str, Any] = {
    "zotero_user_id": "",
    "zotero_api_key": "",
    "library_type": "users",
    "library_id_input": "",
    "pubmed_sort": "relevance",
    "secondary_sort": "none",
    "attach_metrics_to_extra": True,
    "skip_duplicates": True,
    "duplicate_scope": "library",
    "openalex_email": "",
    "openalex_api_key": "",
    "ncbi_email": "",
    "ncbi_api_key": "",
    "manual_collection_path": "",
    "selected_collection_path": "",
    "auto_create_collection": True,
    "remember_settings": True,
}
MAX_HISTORY_ENTRIES = 50



def load_local_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value
    except OSError:
        return


load_local_dotenv(ENV_FILE)


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    try:
        raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def save_history(entries: list[dict[str, Any]]) -> None:
    trimmed = entries[:MAX_HISTORY_ENTRIES]
    HISTORY_FILE.write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_history_entry(entry: dict[str, Any]) -> None:
    history = load_history()
    history.insert(0, entry)
    save_history(history)


def save_to_history_and_update_session(
    *,
    history_entry: dict[str, Any],
) -> None:
    """Save history entry to file and update session state."""
    append_history_entry(history_entry)
    st.session_state["history_entries"] = load_history()


def build_history_entry(
    *,
    event_type: str,
    query: str,
    pubmed_sort: str,
    secondary_sort: str,
    target_collection_path: str,
    duplicate_scope: str,
    skip_duplicates: bool,
    library_type: str,
    library_id: str,
    display_records: list[dict[str, Any]],
    import_records: list[dict[str, Any]],
    skipped_existing: int,
    skipped_incoming: int,
    total_success: int | None = None,
    total_failed: int | None = None,
) -> dict[str, Any]:
    preview_rows: list[dict[str, Any]] = []
    for record in display_records:
        raw_item = {k: v for k, v in record.items() if not str(k).startswith("_")}
        preview_rows.append(
            {
                "title": record.get("title", ""),
                "journal": record.get("publicationTitle", ""),
                "pmid": record.get("PMID", ""),
                "doi": record.get("DOI", ""),
                "status": record.get("_dedup_status", "new"),
                "will_import": bool(record.get("_will_import", False)),
                "cited_by": record.get("_metric_citation_count"),
                "journal_metric": record.get("_metric_journal_2yr_mean_citedness"),
                "raw_item": raw_item,
                "planned_action": record.get("_planned_action", "create"),
                "existing_item_key": record.get("_existing_item_key", ""),
                "existing_item_version": record.get("_existing_item_version"),
                "existing_item_collections": list(
                    record.get("_existing_item_collections", []) or []
                ),
            }
        )

    return {
        "id": dt.datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event_type": event_type,
        "query": query,
        "pubmed_sort": pubmed_sort,
        "secondary_sort": secondary_sort,
        "target_collection_path": target_collection_path,
        "duplicate_scope": duplicate_scope,
        "skip_duplicates": skip_duplicates,
        "library_type": library_type,
        "library_id": library_id,
        "result_count": len(display_records),
        "importable_count": len(import_records),
        "skipped_existing": skipped_existing,
        "skipped_incoming": skipped_incoming,
        "total_success": total_success,
        "total_failed": total_failed,
        "records": preview_rows,
    }


def refresh_payload_records(
    *,
    payload_state_key: str,
    library_type: str,
    library_id: str,
    zotero_api_key: str,
    target_collection_path: str,
    duplicate_scope: str,
    skip_duplicates: bool,
) -> tuple[int, int, int, bool, str, int]:
    """Refresh record status (for history and preview cache).

    Returns:
        tuple containing: skipped_existing, skipped_incoming, link_existing,
                         target_collection_missing, effective_duplicate_scope,
                         changed_rows
    """
    if not zotero_api_key.strip():
        st.error("Refreshing statuses requires Zotero API key.")
        st.stop()
    if not library_id:
        st.error("Current form has no valid library ID.")
        st.stop()
    try:
        validated_library_id = validate_library_id(library_type, library_id)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

    with st.spinner("Refreshing statuses using current form..."):
        try:
            return reevaluate_payload_records(
                payload_state_key=payload_state_key,
                library_type=library_type,
                library_id=validated_library_id,
                zotero_api_key=zotero_api_key.strip(),
                target_collection_path=normalize_collection_path(target_collection_path),
                duplicate_scope=duplicate_scope,
                skip_duplicates=skip_duplicates,
            )
        except Exception as exc:
            st.error(f"Failed to refresh statuses: {exc}")
            st.stop()


def validate_and_refresh_payload_status(
    *,
    payload_state_key: str,
    current_evaluation_signature: str,
    library_type: str,
    library_id: str,
    zotero_api_key: str,
    target_collection_path: str,
    duplicate_scope: str,
    skip_duplicates: bool,
) -> None:
    """Validate and refresh payload status, auto-refresh if signatures don't match.

    Args:
        payload_state_key: The session state key for the payload
        current_evaluation_signature: The current execution signature
        library_type: Zotero library type
        library_id: Zotero library ID
        zotero_api_key: Zotero API key
        target_collection_path: Target collection path
        duplicate_scope: Duplicate checking scope
        skip_duplicates: Whether to skip duplicates
    """
    payload = dict(st.session_state.get(payload_state_key, {}))
    if str(payload.get("evaluation_signature", "")) != current_evaluation_signature:
        st.warning(
            "Status markers were refreshed to match the current form. "
            "Review the list and click import again."
        )
        with st.spinner("Refreshing statuses using current form..."):
            try:
                reevaluate_payload_records(
                    payload_state_key=payload_state_key,
                    library_type=library_type,
                    library_id=validate_library_id(library_type, library_id),
                    zotero_api_key=zotero_api_key.strip(),
                    target_collection_path=normalize_collection_path(target_collection_path),
                    duplicate_scope=duplicate_scope,
                    skip_duplicates=skip_duplicates,
                )
            except Exception as exc:
                st.error(f"Failed to refresh statuses: {exc}")
                st.stop()
        st.rerun()


def load_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in SETTINGS_KEYS if k in raw}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def load_env_settings() -> dict[str, Any]:
    library_type = os.getenv("ZOTERO_LIBRARY_TYPE", DEFAULT_SETTINGS["library_type"])
    if library_type not in {"users", "groups"}:
        library_type = DEFAULT_SETTINGS["library_type"]

    pubmed_sort = os.getenv("PUBMED_SORT", DEFAULT_SETTINGS["pubmed_sort"])
    if pubmed_sort not in PUBMED_SORT_VALUES:
        pubmed_sort = DEFAULT_SETTINGS["pubmed_sort"]

    secondary_sort = os.getenv("SECONDARY_SORT", DEFAULT_SETTINGS["secondary_sort"])
    if secondary_sort not in SECONDARY_SORT_VALUES:
        secondary_sort = DEFAULT_SETTINGS["secondary_sort"]

    duplicate_scope = os.getenv("DUPLICATE_SCOPE", DEFAULT_SETTINGS["duplicate_scope"])
    if duplicate_scope not in {"library", "collection"}:
        duplicate_scope = DEFAULT_SETTINGS["duplicate_scope"]

    manual_collection_path = normalize_collection_path(
        os.getenv("ZOTERO_COLLECTION_PATH", DEFAULT_SETTINGS["manual_collection_path"])
    )

    return {
        "zotero_user_id": os.getenv("ZOTERO_USER_ID", DEFAULT_SETTINGS["zotero_user_id"]),
        "zotero_api_key": os.getenv("ZOTERO_API_KEY", DEFAULT_SETTINGS["zotero_api_key"]),
        "library_type": library_type,
        "library_id_input": os.getenv("ZOTERO_LIBRARY_ID", DEFAULT_SETTINGS["library_id_input"]),
        "pubmed_sort": pubmed_sort,
        "secondary_sort": secondary_sort,
        "attach_metrics_to_extra": _env_flag(
            "ATTACH_METRICS_TO_EXTRA",
            DEFAULT_SETTINGS["attach_metrics_to_extra"],
        ),
        "skip_duplicates": _env_flag("SKIP_DUPLICATES", DEFAULT_SETTINGS["skip_duplicates"]),
        "duplicate_scope": duplicate_scope,
        "openalex_email": os.getenv("OPENALEX_EMAIL", DEFAULT_SETTINGS["openalex_email"]),
        "openalex_api_key": os.getenv("OPENALEX_API_KEY", DEFAULT_SETTINGS["openalex_api_key"]),
        "ncbi_email": os.getenv("NCBI_EMAIL", DEFAULT_SETTINGS["ncbi_email"]),
        "ncbi_api_key": os.getenv("NCBI_API_KEY", DEFAULT_SETTINGS["ncbi_api_key"]),
        "manual_collection_path": manual_collection_path,
        "selected_collection_path": manual_collection_path,
        "auto_create_collection": _env_flag(
            "ZOTERO_AUTO_CREATE_COLLECTION",
            DEFAULT_SETTINGS["auto_create_collection"],
        ),
        "remember_settings": _env_flag(
            "PAPERBOT_REMEMBER_SETTINGS",
            DEFAULT_SETTINGS["remember_settings"],
        ),
    }


def save_settings(values: dict[str, Any]) -> None:
    payload = {k: values.get(k, DEFAULT_SETTINGS[k]) for k in SETTINGS_KEYS}
    SETTINGS_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clear_settings() -> None:
    if SETTINGS_FILE.exists():
        SETTINGS_FILE.unlink()


def current_settings() -> dict[str, Any]:
    return {k: st.session_state.get(k, DEFAULT_SETTINGS[k]) for k in SETTINGS_KEYS}


def bootstrap_settings() -> None:
    if st.session_state.get("settings_initialized"):
        return
    env_loaded = load_env_settings()
    file_loaded = load_settings()
    for key in SETTINGS_KEYS:
        st.session_state[key] = file_loaded.get(
            key,
            env_loaded.get(key, DEFAULT_SETTINGS[key]),
        )
    st.session_state["settings_initialized"] = True


def import_records_to_zotero(
    records: list[dict[str, Any]],
    library_type: str,
    library_id: str,
    zotero_api_key: str,
    target_collection_path: str,
    auto_create_collection: bool,
) -> tuple[int, int, str | None]:
    resolved_collection_key: str | None = None
    if target_collection_path:
        resolved_collection_key = ensure_collection_path(
            library_type=library_type,
            library_id=library_id,
            api_key=zotero_api_key,
            collection_path=target_collection_path,
            auto_create=auto_create_collection,
        )

    total_success = 0
    total_failed = 0
    create_records = [
        record for record in records if record.get("_planned_action", "create") != "link"
    ]
    link_records = [record for record in records if record.get("_planned_action") == "link"]

    for batch in chunked(create_records, 50):
        if not batch:
            continue
        result = zotero_create_items(
            library_type=library_type,
            library_id=library_id,
            api_key=zotero_api_key,
            items=batch,
            collection_key=resolved_collection_key,
        )
        total_success += len(result.get("successful", {}) or {})
        total_failed += len(result.get("failed", {}) or {})

    for batch in chunked(link_records, 50):
        if not batch or not resolved_collection_key:
            continue
        result = zotero_link_existing_items_to_collection(
            library_type=library_type,
            library_id=library_id,
            api_key=zotero_api_key,
            collection_key=resolved_collection_key,
            items=batch,
        )
        total_success += len(result.get("successful", {}) or {})
        total_failed += len(result.get("failed", {}) or {})
    return total_success, total_failed, resolved_collection_key


def build_execution_signature(
    *,
    library_type: str,
    library_id: str,
    target_collection_path: str,
    skip_duplicates: bool,
    duplicate_scope: str,
) -> str:
    payload = {
        "library_type": library_type,
        "library_id": library_id,
        "target_collection_path": normalize_collection_path(target_collection_path),
        "skip_duplicates": bool(skip_duplicates),
        "duplicate_scope": duplicate_scope if duplicate_scope in {"library", "collection"} else "library",
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def format_execution_context(
    *,
    library_type: str,
    library_id: str,
    target_collection_path: str,
    duplicate_scope: str,
) -> str:
    scope_label = (
        "Target collection only"
        if duplicate_scope == "collection" and normalize_collection_path(target_collection_path)
        else "Whole library"
    )
    target_label = normalize_collection_path(target_collection_path) or "(library root)"
    return (
        f"{library_type}/{library_id or '?'} | "
        f"target: {target_label} | "
        f"scope: {scope_label}"
    )


def record_identity(record: dict[str, Any]) -> str:
    doi = _normalize_doi_local(record.get("DOI") or record.get("doi"))
    pmid = _normalize_pmid_local(record.get("PMID") or record.get("pmid"))
    title = str(record.get("title", "")).strip().lower()
    if doi:
        return f"doi:{doi}"
    if pmid:
        return f"pmid:{pmid}"
    return f"title:{title}"


def build_candidate_records_from_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        raw_item = row.get("raw_item", {})
        if isinstance(raw_item, dict) and raw_item:
            item_payload = dict(raw_item)
        elif isinstance(row, dict) and any(
            key in row for key in ["itemType", "title", "publicationTitle", "DOI", "PMID", "extra", "url"]
        ):
            item_payload = {
                k: v
                for k, v in row.items()
                if not str(k).startswith("_")
                and k
                not in {
                    "status",
                    "will_import",
                    "planned_action",
                    "existing_item_key",
                    "existing_item_version",
                    "existing_item_collections",
                    "journal",
                    "pmid",
                    "doi",
                    "cited_by",
                }
            }
        else:
            item_payload = {
                "itemType": "journalArticle",
                "title": row.get("title", ""),
                "publicationTitle": row.get("journal", ""),
                "PMID": row.get("pmid", ""),
                "DOI": row.get("doi", ""),
                "extra": f"PMID: {row.get('pmid', '')}" if row.get("pmid") else "",
            }
        candidates.append(item_payload)
    return candidates


def apply_duplicate_policy(
    *,
    candidate_records: list[dict[str, Any]],
    library_type: str,
    library_id: str,
    zotero_api_key: str,
    target_collection_path: str,
    duplicate_scope: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, int, bool, str]:
    dedupe_library_id = validate_library_id(library_type, library_id)
    dedupe_collection_key: str | None = None
    target_collection_requested = bool(target_collection_path)
    target_collection_missing = False
    effective_duplicate_scope = duplicate_scope if duplicate_scope in {"library", "collection"} else "library"

    if effective_duplicate_scope == "collection" and not target_collection_requested:
        effective_duplicate_scope = "library"

    if target_collection_path:
        try:
            dedupe_collection_key = ensure_collection_path(
                library_type=library_type,
                library_id=dedupe_library_id,
                api_key=zotero_api_key,
                collection_path=target_collection_path,
                auto_create=False,
            )
        except RuntimeError as exc:
            msg = str(exc)
            if (
                "not found" in msg.lower()
                or "failed to resolve collection path" in msg.lower()
            ):
                target_collection_missing = True
                dedupe_collection_key = None
            else:
                raise

    existing_items = list_existing_items_info(
        library_type=library_type,
        library_id=dedupe_library_id,
        api_key=zotero_api_key,
    )
    (
        import_records,
        display_records,
        skipped_existing,
        skipped_incoming,
        link_existing,
    ) = plan_record_import_actions(
        candidate_records,
        existing_items=existing_items,
        target_collection_key=dedupe_collection_key,
        target_collection_requested=target_collection_requested,
        duplicate_scope=effective_duplicate_scope,
    )
    return (
        import_records,
        display_records,
        skipped_existing,
        skipped_incoming,
        link_existing,
        target_collection_missing,
        effective_duplicate_scope,
    )


def reevaluate_payload_records(
    *,
    payload_state_key: str,
    library_type: str,
    library_id: str,
    zotero_api_key: str,
    target_collection_path: str,
    duplicate_scope: str,
    skip_duplicates: bool,
) -> tuple[dict[str, Any], int, int, int, bool, str, int]:
    payload = dict(st.session_state.get(payload_state_key, {}))
    existing_display_records = list(payload.get("display_records", []))
    previous_status_map = {
        record_identity(row): (
            str(row.get("_dedup_status") or row.get("status", "new")),
            str(row.get("_planned_action") or row.get("planned_action", "create")),
        )
        for row in existing_display_records
    }
    selected_identities = {
        record_identity(row)
        for row in existing_display_records
        if row.get("_selected", False)
    }
    candidate_records = build_candidate_records_from_rows(existing_display_records)
    import_records = [
        dict(
            record,
            _planned_action="create",
            _dedup_status="new",
            _will_import=True,
        )
        for record in candidate_records
    ]
    display_records = list(import_records)
    skipped_existing = 0
    skipped_incoming = 0
    link_existing = 0
    target_collection_missing = False
    effective_duplicate_scope = duplicate_scope

    if skip_duplicates:
        (
            import_records,
            display_records,
            skipped_existing,
            skipped_incoming,
            link_existing,
            target_collection_missing,
            effective_duplicate_scope,
        ) = apply_duplicate_policy(
            candidate_records=candidate_records,
            library_type=library_type,
            library_id=library_id,
            zotero_api_key=zotero_api_key,
            target_collection_path=target_collection_path,
            duplicate_scope=duplicate_scope,
        )

    for row in display_records:
        identity = record_identity(row)
        row["_selected"] = identity in selected_identities if selected_identities else bool(
            row.get("_will_import", False)
        )

    selected_actionable = [
        row
        for row in display_records
        if row.get("_selected", False)
        and row.get("_planned_action", "create") in {"create", "link"}
    ]
    payload["display_records"] = display_records
    payload["import_records"] = selected_actionable
    payload["skipped_existing"] = skipped_existing
    payload["skipped_incoming"] = skipped_incoming
    payload["evaluation_signature"] = build_execution_signature(
        library_type=library_type,
        library_id=library_id,
        target_collection_path=target_collection_path,
        skip_duplicates=skip_duplicates,
        duplicate_scope=effective_duplicate_scope,
    )
    changed_rows = 0
    for row in display_records:
        identity = record_identity(row)
        previous = previous_status_map.get(identity)
        current = (
            str(row.get("_dedup_status") or row.get("status", "new")),
            str(row.get("_planned_action") or row.get("planned_action", "create")),
        )
        if previous != current:
            changed_rows += 1
    st.session_state[payload_state_key] = payload
    return (
        payload,
        skipped_existing,
        skipped_incoming,
        link_existing,
        target_collection_missing,
        effective_duplicate_scope,
        changed_rows,
    )


def _normalize_doi_local(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.replace("https://doi.org/", "").replace("http://doi.org/", "")
    raw = raw.replace("doi:", "").strip()
    return raw


def _normalize_pmid_local(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"(\d+)", raw)
    return match.group(1) if match else ""


def _extract_pmid_from_extra_local(extra_value: Any) -> str:
    extra = str(extra_value or "")
    for line in extra.splitlines():
        text = line.strip()
        if text.lower().startswith("pmid:"):
            return _normalize_pmid_local(text.split(":", 1)[1])
    return ""


def split_records_by_duplicate_status(
    records: list[dict[str, Any]],
    existing_dois: set[str],
    existing_pmids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    import_records: list[dict[str, Any]] = []
    display_records: list[dict[str, Any]] = []
    incoming_dois: set[str] = set()
    incoming_pmids: set[str] = set()
    skipped_existing = 0
    skipped_incoming = 0

    for record in records:
        item = dict(record)
        doi = _normalize_doi_local(item.get("DOI"))
        pmid = _normalize_pmid_local(
            item.get("PMID")
        ) or _extract_pmid_from_extra_local(item.get("extra"))

        if (doi and doi in existing_dois) or (pmid and pmid in existing_pmids):
            item["_dedup_status"] = "duplicate_existing"
            item["_will_import"] = False
            skipped_existing += 1
            display_records.append(item)
            continue

        if (doi and doi in incoming_dois) or (pmid and pmid in incoming_pmids):
            item["_dedup_status"] = "duplicate_incoming"
            item["_will_import"] = False
            skipped_incoming += 1
            display_records.append(item)
            continue

        item["_dedup_status"] = "new"
        item["_will_import"] = True
        import_records.append(item)
        display_records.append(item)
        if doi:
            incoming_dois.add(doi)
        if pmid:
            incoming_pmids.add(pmid)

    return import_records, display_records, skipped_existing, skipped_incoming


def dedup_status_icon(status: str) -> str:
    mapping = {
        "new": "🟢",
        "duplicate_existing": "🔴",
        "duplicate_incoming": "🟠",
    }
    return mapping.get(status, "⚪")


def status_label(status: str) -> str:
    mapping = {
        "new": "new",
        "duplicate_existing": "duplicate_existing",
        "duplicate_incoming": "duplicate_incoming",
        "existing_add_to_collection": "existing_add_to_collection",
    }
    return mapping.get(status, "unknown")


def action_label(action: str) -> str:
    mapping = {
        "create": "create new item",
        "link": "add existing to target collection",
        "skip": "no change",
    }
    return mapping.get(action, "unknown")


def status_style(value: Any) -> str:
    text = str(value)
    if text == "new":
        return "background-color: #e8f7ea; color: #17653a; font-weight: 600;"
    if text == "duplicate_existing":
        return "background-color: #fdeceb; color: #a12622; font-weight: 600;"
    if text == "duplicate_incoming":
        return "background-color: #fff4e5; color: #9a5b00; font-weight: 600;"
    if text == "existing_add_to_collection":
        return "background-color: #e8f1ff; color: #1d4ed8; font-weight: 600;"
    return "background-color: #f3f4f6; color: #374151;"


def render_selectable_records_editor(
    *,
    payload: dict[str, Any],
    payload_state_key: str,
    key_prefix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    actionable_statuses = {"new", "existing_add_to_collection"}
    display_records = list(payload.get("display_records", []))
    for record in display_records:
        if "_selected" not in record:
            record["_selected"] = bool(
                record.get("_will_import", record.get("will_import", True))
            )

    token = str(payload.get("id") or payload.get("created_at", "default"))
    editor_version_state_key = f"{key_prefix}_editor_version::{token}"
    if editor_version_state_key not in st.session_state:
        st.session_state[editor_version_state_key] = 0

    select_col1, select_col2, select_col3 = st.columns(3)
    with select_col1:
        choose_non_duplicates = st.button(
            "Select non-duplicates", key=f"{key_prefix}_sel_non_dup_{token}"
        )
    with select_col2:
        choose_all = st.button("Select all", key=f"{key_prefix}_sel_all_{token}")
    with select_col3:
        choose_none = st.button("Select none", key=f"{key_prefix}_sel_none_{token}")

    bulk_select_changed = False
    if choose_non_duplicates:
        for record in display_records:
            status = record.get("_dedup_status") or record.get("status")
            record["_selected"] = status in actionable_statuses
        bulk_select_changed = True
    if choose_all:
        for record in display_records:
            record["_selected"] = True
        bulk_select_changed = True
    if choose_none:
        for record in display_records:
            record["_selected"] = False
        bulk_select_changed = True

    if bulk_select_changed:
        selected_records = [r for r in display_records if r.get("_selected", False)]
        selected_import_records = [
            r
            for r in selected_records
            if (r.get("_dedup_status") or r.get("status")) in actionable_statuses
        ]
        selected_duplicate_records = len(selected_records) - len(selected_import_records)
        payload["display_records"] = display_records
        payload["import_records"] = selected_import_records
        payload["selected_duplicates"] = selected_duplicate_records
        st.session_state[payload_state_key] = payload
        st.session_state[editor_version_state_key] = (
            int(st.session_state[editor_version_state_key]) + 1
        )
        st.rerun()

    editor_df = pd.DataFrame(
        [
            {
                "Select": bool(record.get("_selected", False)),
                "Status": status_label(
                    str(record.get("_dedup_status") or record.get("status", "new"))
                ),
                "Action": action_label(
                    str(record.get("_planned_action") or record.get("planned_action", "create"))
                ),
                "Title": record.get("title", ""),
                "Journal": record.get("publicationTitle") or record.get("journal", ""),
                "PMID": record.get("PMID") or record.get("pmid", ""),
                "DOI": record.get("DOI") or record.get("doi", ""),
                "Cited By": record.get("_metric_citation_count") or record.get("cited_by"),
            }
            for record in display_records
        ]
    )
    styled_editor_df = editor_df.style.map(status_style, subset=["Status"])
    edited_df = st.data_editor(
        styled_editor_df,
        hide_index=True,
        width="stretch",
        key=(
            f"{key_prefix}_editor_{token}_"
            f"{st.session_state.get(editor_version_state_key, 0)}"
        ),
        column_config={
            "Select": st.column_config.CheckboxColumn("Select"),
        },
        disabled=["Status", "Action", "Title", "Journal", "PMID", "DOI", "Cited By"],
    )
    for idx, row in edited_df.iterrows():
        display_records[idx]["_selected"] = bool(row.get("Select", False))

    selected_records = [r for r in display_records if r.get("_selected", False)]
    selected_import_records = [
        r
        for r in selected_records
        if (r.get("_dedup_status") or r.get("status")) in actionable_statuses
    ]
    selected_duplicate_records = len(selected_records) - len(selected_import_records)

    payload["display_records"] = display_records
    payload["import_records"] = selected_import_records
    payload["selected_duplicates"] = selected_duplicate_records
    st.session_state[payload_state_key] = payload

    st.caption(
        "Legend: green = create new item, blue = add existing item to target collection, "
        "red = already in target scope, amber = duplicate inside this batch"
    )
    st.caption(
        f"Current selection: {len(selected_records)} | "
        f"will import: {len(selected_import_records)} | "
        f"selected no-op rows: {selected_duplicate_records}"
    )
    return display_records, selected_import_records, selected_duplicate_records


st.set_page_config(
    page_title="PubMed to Zotero", page_icon=":books:", layout="centered"
)
bootstrap_settings()

if "history_entries" not in st.session_state:
    st.session_state["history_entries"] = load_history()

st.title("PubMed to Zotero")
st.caption("Search PubMed and import directly into Zotero.")

if "collection_paths" not in st.session_state:
    st.session_state["collection_paths"] = []
if "collection_paths_library_signature" not in st.session_state:
    st.session_state["collection_paths_library_signature"] = ""
if "ambiguous_collection_paths" not in st.session_state:
    st.session_state["ambiguous_collection_paths"] = {}

query = st.text_input("PubMed query", placeholder="e.g. glioblastoma AND immunotherapy")
max_results = st.number_input(
    "Max results", min_value=1, max_value=500, value=20, step=1
)
pubmed_sort = st.selectbox(
    "PubMed API sort", options=PUBMED_SORT_VALUES, key="pubmed_sort"
)
secondary_sort = st.selectbox(
    "Secondary sort (local rerank)",
    options=SECONDARY_SORT_VALUES,
    key="secondary_sort",
)
attach_metrics_to_extra = st.checkbox(
    "Attach citation/journal metrics to Zotero extra",
    key="attach_metrics_to_extra",
)
skip_duplicates = st.checkbox(
    "Skip duplicates already in Zotero (DOI/PMID)",
    key="skip_duplicates",
)
duplicate_scope = st.selectbox(
    "Duplicate check scope",
    options=["library", "collection"],
    key="duplicate_scope",
    format_func=lambda x: "Whole library"
    if x == "library"
    else "Target collection only",
)

st.markdown("### Zotero")
zotero_user_id = st.text_input("Zotero user ID", key="zotero_user_id")
zotero_api_key = st.text_input("Zotero API key", type="password", key="zotero_api_key")
library_type = st.selectbox(
    "Library type", options=["users", "groups"], key="library_type"
)
library_id_input = st.text_input(
    "Library ID (optional for personal library)", key="library_id_input"
)
remember_settings = st.checkbox("Remember credentials locally", key="remember_settings")
st.caption(f"Saved file: `{SETTINGS_FILE}`")

settings_col1, settings_col2 = st.columns(2)
with settings_col1:
    if st.button("Save settings"):
        try:
            save_settings(current_settings())
            st.success("Settings saved.")
        except Exception as exc:
            st.error(f"Failed to save settings: {exc}")
with settings_col2:
    if st.button("Clear settings"):
        try:
            clear_settings()
            for key in SETTINGS_KEYS:
                st.session_state[key] = DEFAULT_SETTINGS[key]
            st.success("Saved settings cleared.")
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to clear settings: {exc}")

resolved_library_id = (library_id_input or "").strip() or (zotero_user_id or "").strip()
current_collection_library_signature = json.dumps(
    {
        "library_type": library_type,
        "library_id": resolved_library_id,
    },
    ensure_ascii=False,
    sort_keys=True,
)
if (
    st.session_state.get("collection_paths_library_signature", "")
    != current_collection_library_signature
):
    st.session_state["collection_paths"] = []
    st.session_state["selected_collection_path"] = ""
    st.session_state["collection_paths_library_signature"] = current_collection_library_signature
    st.session_state["ambiguous_collection_paths"] = {}

st.markdown("### Collection")
manual_collection_path = st.text_input(
    "Target collection path (optional)",
    placeholder="e.g. ProjectA/Review/2026Q2",
    key="manual_collection_path",
)
auto_create_collection = st.checkbox(
    "Auto-create collection path if missing",
    key="auto_create_collection",
)

load_collections = st.button("Load collections")
if load_collections:
    if not resolved_library_id:
        st.error(
            "Please provide Zotero user ID or library ID before loading collections."
        )
    elif not zotero_api_key.strip():
        st.error("Please provide Zotero API key before loading collections.")
    else:
        try:
            validated_library_id = validate_library_id(
                library_type, resolved_library_id
            )
        except ValueError as exc:
            st.error(str(exc))
            st.stop()

        with st.spinner("Loading collections from Zotero..."):
            try:
                collections = list_collections(
                    library_type=library_type,
                    library_id=validated_library_id,
                    api_key=zotero_api_key.strip(),
                )
                mapping = build_collection_paths(collections)
                ambiguous_paths = find_ambiguous_collection_paths(collections)
                st.session_state["collection_paths"] = sorted(mapping.keys())
                st.session_state["collection_paths_library_signature"] = (
                    current_collection_library_signature
                )
                st.session_state["ambiguous_collection_paths"] = ambiguous_paths
                normalized_manual_path = normalize_collection_path(
                    st.session_state.get("manual_collection_path", "")
                )
                if normalized_manual_path in st.session_state["collection_paths"]:
                    st.session_state["selected_collection_path"] = normalized_manual_path
                elif (
                    st.session_state.get("selected_collection_path", "")
                    not in st.session_state["collection_paths"]
                ):
                    st.session_state["selected_collection_path"] = ""
                st.success(
                    f"Loaded {len(st.session_state['collection_paths'])} collection paths."
                )
                if ambiguous_paths:
                    ambiguous_list = "; ".join(
                        f"{path} ({len(keys)} matches)"
                        for path, keys in sorted(ambiguous_paths.items())
                    )
                    st.error(
                        "Ambiguous Zotero collection paths detected. "
                        "Imports to these paths are blocked until duplicate same-name collections are cleaned up: "
                        f"{ambiguous_list}"
                    )
                if remember_settings:
                    save_settings(current_settings())
            except Exception as exc:
                st.error(f"Failed to load collections: {exc}")

selected_existing_path = st.selectbox(
    "Select existing collection path (optional)",
    options=[""] + st.session_state["collection_paths"],
    key="selected_collection_path",
)
if st.session_state.get("ambiguous_collection_paths"):
    ambiguous_list = "; ".join(
        f"{path} ({len(keys)} matches)"
        for path, keys in sorted(st.session_state["ambiguous_collection_paths"].items())
    )
    st.warning(
        "Ambiguous collection paths exist in this library. "
        "Those paths are excluded from the dropdown and blocked for import until you clean them up in Zotero: "
        f"{ambiguous_list}"
    )

st.markdown("### NCBI (optional)")
ncbi_email = st.text_input("NCBI email", key="ncbi_email")
ncbi_api_key = st.text_input("NCBI API key", type="password", key="ncbi_api_key")
openalex_email = st.text_input("OpenAlex email", key="openalex_email")
openalex_api_key = st.text_input(
    "OpenAlex API key", type="password", key="openalex_api_key"
)

dry_run = st.checkbox("Preview only (do not write to Zotero)", value=True)
run_button_label = "Run preview" if dry_run else "Run and import"
run = st.button(run_button_label)

st.markdown("### History")
st.caption(f"Saved file: `{HISTORY_FILE}`")
history_entries = list(st.session_state.get("history_entries", []))
history_header_col1, history_header_col2 = st.columns(2)
with history_header_col1:
    if st.button("Reload history"):
        st.session_state["history_entries"] = load_history()
        st.rerun()
with history_header_col2:
    if st.button("Clear all history"):
        save_history([])
        st.session_state["history_entries"] = []
        st.success("History cleared.")
        st.rerun()

if history_entries:
    history_summary = [
        {
            "Time": item.get("created_at", ""),
            "Type": item.get("event_type", ""),
            "Query": item.get("query", ""),
            "Results": item.get("result_count", 0),
            "Importable": item.get("importable_count", 0),
            "Skipped Existing": item.get("skipped_existing", 0),
            "Target": item.get("target_collection_path", "") or "(library root)",
        }
        for item in history_entries
    ]
    st.dataframe(history_summary, width="stretch", hide_index=True)

    history_labels = [
        f"{item.get('created_at', '')} | {item.get('event_type', '')} | {item.get('query', '')}"
        for item in history_entries
    ]
    # Remember user's selected history index
    if "selected_history_index" not in st.session_state:
        st.session_state["selected_history_index"] = 0
    else:
        # Check if the stored index is still valid
        current_index = st.session_state["selected_history_index"]
        if current_index >= len(history_entries):
            st.session_state["selected_history_index"] = 0

    selected_history_label = st.selectbox(
        "Inspect history entry",
        options=[""] + history_labels,
        index=st.session_state["selected_history_index"],
        on_change=lambda: None,  # Simple callback to maintain index state
        key="history_selector",
        label_visibility="collapsed",
    )
    if selected_history_label:
        history_index = history_labels.index(selected_history_label)
        st.session_state["selected_history_index"] = history_index
        selected_history = history_entries[history_index]
        st.caption(
            f"PubMed sort: {selected_history.get('pubmed_sort', '')} | "
            f"Secondary sort: {selected_history.get('secondary_sort', '')} | "
            f"Duplicate scope: {selected_history.get('duplicate_scope', '')}"
        )
        history_payload_key = f"history_payload::{selected_history.get('id', history_index)}"
        if history_payload_key not in st.session_state:
            history_display_records = []
            for row in selected_history.get("records", []):
                history_display_records.append(
                    {
                        "title": row.get("title", ""),
                        "journal": row.get("journal", ""),
                        "pmid": row.get("pmid", ""),
                        "doi": row.get("doi", ""),
                        "status": row.get("status", "new"),
                        "will_import": bool(row.get("will_import", False)),
                        "_selected": bool(row.get("will_import", False)),
                        "raw_item": row.get("raw_item", {}),
                        "planned_action": row.get("planned_action", "create"),
                        "existing_item_key": row.get("existing_item_key", ""),
                        "existing_item_version": row.get("existing_item_version"),
                        "existing_item_collections": list(
                            row.get("existing_item_collections", []) or []
                        ),
                    }
                )
            st.session_state[history_payload_key] = {
                "id": selected_history.get("id", ""),
                "created_at": selected_history.get("created_at", ""),
                "display_records": history_display_records,
                "import_records": [
                    row
                    for row in history_display_records
                    if row.get("status") in {"new", "existing_add_to_collection"}
                    and row.get("_selected", False)
                ],
                "evaluation_signature": "",
            }

        history_payload = dict(st.session_state[history_payload_key])
        history_display_records, _, _ = render_selectable_records_editor(
            payload=history_payload,
            payload_state_key=history_payload_key,
            key_prefix=f"history_{selected_history.get('id', history_index)}",
        )
        selected_history_rows = [
            row for row in history_display_records if row.get("_selected", False)
        ]

        st.caption(
            "History keeps the saved article list and snapshot statuses. "
            "Import uses the current form's Zotero library, collection path, and duplicate settings. "
            "Use 'Load this history config' first if you want to restore the old target before importing."
        )
        current_history_signature = build_execution_signature(
            library_type=library_type,
            library_id=resolved_library_id,
            target_collection_path=selected_existing_path or manual_collection_path,
            skip_duplicates=skip_duplicates,
            duplicate_scope=duplicate_scope,
        )
        history_status_current = (
            str(history_payload.get("evaluation_signature", "")) == current_history_signature
        )
        if history_status_current:
            st.caption("Current duplicate/status markers are already aligned with the current form.")
        else:
            st.warning(
                "These status markers are not aligned with the current form yet. "
                "Refresh them before importing if you changed library, collection, or duplicate settings."
            )
        st.caption(
            "Evaluate against: "
            + format_execution_context(
                library_type=library_type,
                library_id=resolved_library_id,
                target_collection_path=selected_existing_path or manual_collection_path,
                duplicate_scope=duplicate_scope,
            )
        )

        history_action_col1, history_action_col2, history_action_col3 = st.columns(3)
        with history_action_col1:
            import_from_history = st.button(
                "Import selected",
                key=f"import_history_{selected_history.get('id', history_index)}",
            )
        with history_action_col2:
            refresh_history_status = st.button(
                "Re-check status",
                key=f"refresh_history_{selected_history.get('id', history_index)}",
            )
        with history_action_col3:
            load_history_config = st.button(
                "Load config",
                key=f"load_history_{selected_history.get('id', '')}",
            )

        if import_from_history:
            target_library_id = resolved_library_id
            target_library_type = library_type
            target_collection_path = normalize_collection_path(
                selected_existing_path or manual_collection_path
            )
            if not zotero_api_key.strip():
                st.error("Importing from history requires Zotero API key.")
                st.stop()
            if not target_library_id:
                st.error("Current form has no valid library ID.")
                st.stop()
            try:
                target_library_id = validate_library_id(target_library_type, target_library_id)
            except ValueError as exc:
                st.error(str(exc))
                st.stop()

            # Validate and refresh status if needed
            current_history_signature = build_execution_signature(
                library_type=target_library_type,
                library_id=target_library_id,
                target_collection_path=target_collection_path,
                skip_duplicates=skip_duplicates,
                duplicate_scope=duplicate_scope,
            )
            validate_and_refresh_payload_status(
                payload_state_key=history_payload_key,
                current_evaluation_signature=current_history_signature,
                library_type=target_library_type,
                library_id=target_library_id,
                zotero_api_key=zotero_api_key,
                target_collection_path=target_collection_path,
                duplicate_scope=duplicate_scope,
                skip_duplicates=skip_duplicates,
            )

            current_history_payload = dict(st.session_state.get(history_payload_key, {}))
            history_import_display_records = list(current_history_payload.get("display_records", []))
            import_history_records = [
                row
                for row in history_import_display_records
                if row.get("_selected", False)
                and row.get("_planned_action", "create") in {"create", "link"}
            ]
            skipped_existing = int(current_history_payload.get("skipped_existing", 0))
            skipped_incoming = int(current_history_payload.get("skipped_incoming", 0))

            if not import_history_records:
                st.warning("No actionable history records selected for import.")
                st.stop()

            with st.spinner("Importing selected history records into Zotero..."):
                try:
                    total_success, total_failed, resolved_collection_key = import_records_to_zotero(
                        records=import_history_records,
                        library_type=target_library_type,
                        library_id=target_library_id,
                        zotero_api_key=zotero_api_key.strip(),
                        target_collection_path=target_collection_path,
                        auto_create_collection=auto_create_collection,
                    )
                except Exception as exc:
                    st.error(f"Import from history failed: {exc}")
                    st.stop()

            history_entry = build_history_entry(
                event_type="import-from-history",
                query=str(selected_history.get("query", "")),
                pubmed_sort=str(selected_history.get("pubmed_sort", "relevance")),
                secondary_sort=str(selected_history.get("secondary_sort", "none")),
                target_collection_path=target_collection_path,
                duplicate_scope=duplicate_scope,
                skip_duplicates=skip_duplicates,
                library_type=target_library_type,
                library_id=target_library_id,
                display_records=history_import_display_records,
                import_records=import_history_records,
                skipped_existing=skipped_existing,
                skipped_incoming=skipped_incoming,
                total_success=total_success,
                total_failed=total_failed,
            )
            save_to_history_and_update_session(history_entry)
            if resolved_collection_key:
                st.success(
                    f"History import finished. Success: {total_success}, Failed: {total_failed}. "
                    f"Collection key: {resolved_collection_key}"
                )
            else:
                st.success(
                    f"History import finished. Success: {total_success}, Failed: {total_failed}."
                )

        if refresh_history_status:
            (
                skipped_existing,
                skipped_incoming,
                link_existing,
                target_collection_missing,
                effective_duplicate_scope,
                changed_rows,
            ) = refresh_payload_records(
                payload_state_key=history_payload_key,
                library_type=library_type,
                library_id=resolved_library_id,
                zotero_api_key=zotero_api_key,
                target_collection_path=selected_existing_path or manual_collection_path,
                duplicate_scope=duplicate_scope,
                skip_duplicates=skip_duplicates,
            )

            if target_collection_missing and (selected_existing_path or manual_collection_path):
                st.info(
                    "Current target collection does not exist yet. Existing library items can still be linked into it during import."
                )
            if effective_duplicate_scope != duplicate_scope:
                st.info(
                    "Duplicate scope fell back to Whole library because no target collection path is currently set."
                )
            st.success(
                f"History re-check complete. changed rows={changed_rows}, "
                f"existing in scope={skipped_existing}, incoming duplicate={skipped_incoming}, "
                f"link candidates={link_existing}."
            )
            st.rerun()

        if import_from_history:
            target_library_id = resolved_library_id
            target_library_type = library_type
            target_collection_path = normalize_collection_path(
                selected_existing_path or manual_collection_path
            )
            if not zotero_api_key.strip():
                st.error("Importing from history requires Zotero API key.")
                st.stop()
            if not target_library_id:
                st.error("Current form has no valid library ID.")
                st.stop()
            try:
                target_library_id = validate_library_id(target_library_type, target_library_id)
            except ValueError as exc:
                st.error(str(exc))
                st.stop()
            if str(history_payload.get("evaluation_signature", "")) != current_history_signature:
                st.warning(
                    "Status markers were refreshed to match the current form. Review the list and click import again."
                )
                with st.spinner("Refreshing history statuses using current form..."):
                    try:
                        reevaluate_payload_records(
                            payload_state_key=history_payload_key,
                            library_type=target_library_type,
                            library_id=target_library_id,
                            zotero_api_key=zotero_api_key.strip(),
                            target_collection_path=target_collection_path,
                            duplicate_scope=duplicate_scope,
                            skip_duplicates=skip_duplicates,
                        )
                    except Exception as exc:
                        st.error(f"Failed to refresh history statuses: {exc}")
                        st.stop()
                st.rerun()

            current_history_payload = dict(st.session_state.get(history_payload_key, {}))
            history_import_display_records = list(current_history_payload.get("display_records", []))
            import_history_records = [
                row
                for row in history_import_display_records
                if row.get("_selected", False)
                and row.get("_planned_action", "create") in {"create", "link"}
            ]
            skipped_existing = int(current_history_payload.get("skipped_existing", 0))
            skipped_incoming = int(current_history_payload.get("skipped_incoming", 0))

            if not import_history_records:
                st.warning("No actionable history records selected for import.")
                st.stop()

            with st.spinner("Importing selected history records into Zotero..."):
                try:
                    total_success, total_failed, resolved_collection_key = import_records_to_zotero(
                        records=import_history_records,
                        library_type=target_library_type,
                        library_id=target_library_id,
                        zotero_api_key=zotero_api_key.strip(),
                        target_collection_path=target_collection_path,
                        auto_create_collection=auto_create_collection,
                    )
                except Exception as exc:
                    st.error(f"Import from history failed: {exc}")
                    st.stop()

            history_entry = build_history_entry(
                event_type="import-from-history",
                query=str(selected_history.get("query", "")),
                pubmed_sort=str(selected_history.get("pubmed_sort", "relevance")),
                secondary_sort=str(selected_history.get("secondary_sort", "none")),
                target_collection_path=target_collection_path,
                duplicate_scope=duplicate_scope,
                skip_duplicates=skip_duplicates,
                library_type=target_library_type,
                library_id=target_library_id,
                display_records=history_import_display_records,
                import_records=import_history_records,
                skipped_existing=skipped_existing,
                skipped_incoming=skipped_incoming,
                total_success=total_success,
                total_failed=total_failed,
            )
            save_to_history_and_update_session(history_entry)
            st.session_state.pop("preview_payload", None)
            if resolved_collection_key:
                st.success(
                    f"History import finished. Success: {total_success}, Failed: {total_failed}. "
                    f"Collection key: {resolved_collection_key}"
                )
            else:
                st.success(
                    f"History import finished. Success: {total_success}, Failed: {total_failed}."
                )

        if load_history_config:
            st.session_state["pubmed_sort"] = selected_history.get(
                "pubmed_sort", "relevance"
            )
            st.session_state["secondary_sort"] = selected_history.get(
                "secondary_sort", "none"
            )
            st.session_state["skip_duplicates"] = bool(
                selected_history.get("skip_duplicates", True)
            )
            st.session_state["duplicate_scope"] = selected_history.get(
                "duplicate_scope", "library"
            )
            loaded_target_collection_path = normalize_collection_path(
                selected_history.get("target_collection_path", "")
            )
            st.session_state["manual_collection_path"] = loaded_target_collection_path
            if loaded_target_collection_path in st.session_state.get("collection_paths", []):
                st.session_state["selected_collection_path"] = loaded_target_collection_path
            else:
                st.session_state["selected_collection_path"] = ""
            st.session_state["library_type"] = selected_history.get(
                "library_type", "users"
            )
            st.session_state["library_id_input"] = selected_history.get(
                "library_id", ""
            )
            st.success("History config loaded into the current form.")
            st.rerun()
else:
    st.caption("No history yet. Preview or import once and it will appear here.")

preview_payload = st.session_state.get("preview_payload")
if preview_payload:
    st.markdown("### Preview Cache")
    st.caption(
        "You can import the last previewed result directly without re-querying PubMed/OpenAlex."
    )
    cached_display_records = list(preview_payload.get("display_records", []))
    cached_import_records = list(preview_payload.get("import_records", []))
    cached_duplicate_existing = int(preview_payload.get("skipped_existing", 0))
    cached_duplicate_incoming = int(preview_payload.get("skipped_incoming", 0))
    st.caption(
        f"Created: {preview_payload.get('created_at', '-')}, "
        f"all records: {len(cached_display_records)}, "
        f"importable: {len(cached_import_records)}, "
        f"query: {preview_payload.get('query', '-')}"
    )
    current_preview_signature = build_execution_signature(
        library_type=library_type,
        library_id=resolved_library_id,
        target_collection_path=selected_existing_path or manual_collection_path,
        skip_duplicates=skip_duplicates,
        duplicate_scope=duplicate_scope,
    )
    preview_status_current = (
        str(preview_payload.get("evaluation_signature", "")) == current_preview_signature
    )
    if preview_status_current:
        st.caption("Preview duplicate/status markers are aligned with the current form.")
    else:
        st.warning(
            "Preview status markers are not aligned with the current form yet. "
            "Refresh them before importing if you changed library, collection, or duplicate settings."
        )
    st.caption(
        "Evaluate against: "
        + format_execution_context(
            library_type=library_type,
            library_id=resolved_library_id,
            target_collection_path=selected_existing_path or manual_collection_path,
            duplicate_scope=duplicate_scope,
        )
    )

    render_selectable_records_editor(
        payload=preview_payload,
        payload_state_key="preview_payload",
        key_prefix="preview",
    )
    st.caption(
        f"Dedup summary: duplicate_existing={cached_duplicate_existing}, "
        f"duplicate_incoming={cached_duplicate_incoming}"
    )

    cache_col1, cache_col2, cache_col3 = st.columns(3)
    with cache_col1:
        import_preview_now = st.button("Import selected")
    with cache_col2:
        refresh_preview_status = st.button("Re-check status")
    with cache_col3:
        clear_preview_cache = st.button("Clear preview")

    if clear_preview_cache:
        st.session_state.pop("preview_payload", None)
        st.success("Preview cache cleared.")
        st.rerun()

    if refresh_preview_status:
        (
            skipped_existing,
            skipped_incoming,
            link_existing,
            target_collection_missing,
            effective_duplicate_scope,
            changed_rows,
        ) = refresh_payload_records(
            payload_state_key="preview_payload",
            library_type=library_type,
            library_id=resolved_library_id,
            zotero_api_key=zotero_api_key,
            target_collection_path=selected_existing_path or manual_collection_path,
            duplicate_scope=duplicate_scope,
            skip_duplicates=skip_duplicates,
        )

        if target_collection_missing and (selected_existing_path or manual_collection_path):
            st.info(
                "Current target collection does not exist yet. Existing library items can still be linked into it during import."
            )
        if effective_duplicate_scope != duplicate_scope:
            st.info(
                "Duplicate scope fell back to Whole library because no target collection path is currently set."
            )
        st.success(
            f"Preview re-check complete. changed rows={changed_rows}, "
            f"existing in scope={skipped_existing}, incoming duplicate={skipped_incoming}, "
            f"link candidates={link_existing}."
        )
        st.rerun()

    if import_preview_now:
        current_library_type = library_type
        current_library_id = resolved_library_id
        current_target_collection_path = normalize_collection_path(
            selected_existing_path or manual_collection_path
        )

        if not zotero_api_key.strip():
            st.error("Importing preview cache requires Zotero API key.")
            st.stop()
        if not current_library_id:
            st.error("Current form has no valid library ID.")
            st.stop()

        try:
            current_library_id = validate_library_id(
                current_library_type, current_library_id
            )
        except ValueError as exc:
            st.error(f"Current form library ID invalid: {exc}")
            st.stop()

        # Validate and refresh status if needed
        validate_and_refresh_payload_status(
            payload_state_key="preview_payload",
            current_evaluation_signature=current_preview_signature,
            library_type=current_library_type,
            library_id=current_library_id,
            zotero_api_key=zotero_api_key,
            target_collection_path=current_target_collection_path,
            duplicate_scope=duplicate_scope,
            skip_duplicates=skip_duplicates,
        )

        current_preview_payload = dict(st.session_state.get("preview_payload", {}))
        preview_import_display_records = list(current_preview_payload.get("display_records", []))
        preview_import_records = [
            row
            for row in preview_import_display_records
            if row.get("_selected", False)
            and row.get("_planned_action", "create") in {"create", "link"}
        ]
        skipped_existing = int(current_preview_payload.get("skipped_existing", 0))
        skipped_incoming = int(current_preview_payload.get("skipped_incoming", 0))

        if not preview_import_records:
            st.warning("No actionable preview records selected for import.")
            st.stop()

        with st.spinner("Importing previewed records into Zotero..."):
            try:
                total_success, total_failed, resolved_collection_key = (
                    import_records_to_zotero(
                        records=preview_import_records,
                        library_type=current_library_type,
                        library_id=current_library_id,
                        zotero_api_key=zotero_api_key.strip(),
                        target_collection_path=current_target_collection_path,
                        auto_create_collection=auto_create_collection,
                    )
                )
            except Exception as exc:
                st.error(f"Import failed: {exc}")
                st.stop()

        if remember_settings:
            try:
                save_settings(current_settings())
            except Exception as exc:
                st.warning(f"Could not save settings: {exc}")

        history_entry = build_history_entry(
            event_type="import-from-preview",
            query=str(preview_payload.get("query", "")),
            pubmed_sort=str(preview_payload.get("pubmed_sort", "relevance")),
            secondary_sort=str(preview_payload.get("secondary_sort", "none")),
            target_collection_path=current_target_collection_path,
            duplicate_scope=duplicate_scope,
            skip_duplicates=skip_duplicates,
            library_type=current_library_type,
            library_id=current_library_id,
            display_records=preview_import_display_records,
            import_records=preview_import_records,
            skipped_existing=skipped_existing,
            skipped_incoming=skipped_incoming,
            total_success=total_success,
            total_failed=total_failed,
        )
        append_history_entry(history_entry)
        st.session_state["history_entries"] = load_history()

        st.session_state.pop("preview_payload", None)
        if resolved_collection_key:
            st.success(
                f"Import finished. Success: {total_success}, Failed: {total_failed}. "
                f"Collection key: {resolved_collection_key}"
            )
        else:
            st.success(
                f"Import finished. Success: {total_success}, Failed: {total_failed}."
            )

if run:

    if not query.strip():
        st.error("Please input a PubMed query.")
        st.stop()

    if not dry_run:
        if not zotero_user_id.strip():
            st.error("Import mode requires Zotero user ID.")
            st.stop()
        if not zotero_api_key.strip():
            st.error("Import mode requires Zotero API key.")
            st.stop()
        if not resolved_library_id:
            st.error("Import mode requires a resolved library ID.")
            st.stop()
        try:
            validated_library_id = validate_library_id(
                library_type, resolved_library_id
            )
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
    else:
        validated_library_id = resolved_library_id

    target_collection_path = normalize_collection_path(
        selected_existing_path or manual_collection_path
    )

    with st.spinner("Searching PubMed..."):
        try:
            pmids = search_pubmed_ids(
                query=query.strip(),
                retmax=int(max_results),
                email=ncbi_email.strip() or None,
                api_key=ncbi_api_key.strip() or None,
                sort=pubmed_sort,
            )
        except Exception as exc:
            st.error(f"PubMed search failed: {exc}")
            st.stop()

    if not pmids:
        st.warning("No PubMed records found.")
        st.stop()

    with st.spinner("Fetching PubMed records..."):
        try:
            root = fetch_pubmed_xml(
                pmids=pmids,
                email=ncbi_email.strip() or None,
                api_key=ncbi_api_key.strip() or None,
            )
            records = parse_pubmed_articles(root)
        except Exception as exc:
            st.error(f"Failed to fetch PubMed records: {exc}")
            st.stop()

    if not records:
        st.warning("Search returned records but no parseable entries were found.")
        st.stop()

    should_fetch_secondary_metrics = secondary_sort != "none" or attach_metrics_to_extra
    if should_fetch_secondary_metrics:
        with st.spinner("Fetching citation/journal metrics..."):
            try:
                metrics_by_pmid = fetch_openalex_metrics_by_pmids(
                    pmids=[str(record.get("PMID", "")).strip() for record in records],
                    email=openalex_email.strip() or None,
                    api_key=openalex_api_key.strip() or None,
                )
                apply_secondary_metrics_to_records(
                    records=records,
                    metrics_by_pmid=metrics_by_pmid,
                    secondary_sort=secondary_sort,
                    attach_to_extra=attach_metrics_to_extra,
                )
            except Exception as exc:
                if secondary_sort != "none":
                    st.error(
                        f"Secondary sort requires metrics, but metric fetch failed: {exc}"
                    )
                    st.stop()
                st.warning(f"Metrics fetch failed, continuing without metrics: {exc}")

    if secondary_sort != "none":
        records = secondary_sort_records(records, secondary_sort)

    import_records = [
        dict(record, _dedup_status="new", _planned_action="create", _will_import=True)
        for record in records
    ]
    display_records = list(import_records)
    skipped_existing = 0
    skipped_incoming = 0
    link_existing = 0
    if skip_duplicates:
        if not zotero_api_key.strip():
            if dry_run:
                st.warning(
                    "Duplicate check skipped in preview mode because Zotero API key is empty."
                )
            else:
                st.error("Duplicate check requires Zotero API key.")
                st.stop()
        elif not resolved_library_id:
            st.warning("Duplicate check skipped because library ID is empty.")
        else:
            try:
                with st.spinner("Checking duplicates in Zotero..."):
                    (
                        import_records,
                        display_records,
                        skipped_existing,
                        skipped_incoming,
                        link_existing,
                        target_collection_missing,
                        effective_duplicate_scope,
                    ) = apply_duplicate_policy(
                        candidate_records=records,
                        library_type=library_type,
                        library_id=resolved_library_id,
                        zotero_api_key=zotero_api_key.strip(),
                        target_collection_path=target_collection_path,
                        duplicate_scope=duplicate_scope,
                    )
                if target_collection_missing and target_collection_path:
                    st.info(
                        "Target collection does not exist yet. "
                        "Existing library items can still be linked into it during import."
                    )
                if effective_duplicate_scope != duplicate_scope:
                    st.info(
                        "Duplicate scope fell back to Whole library because no target collection path is set."
                    )
            except Exception as exc:
                if dry_run:
                    st.warning(f"Duplicate check failed in preview mode: {exc}")
                else:
                    st.error(f"Duplicate check failed: {exc}")
                    st.stop()

    st.success(
        f"Fetched {len(display_records)} records. "
        f"Importable after dedup: {len(import_records)}."
    )
    st.dataframe(
        [
            {
                "Title": record.get("title", ""),
                "Journal": record.get("publicationTitle", ""),
                "Date": record.get("date", ""),
                "DOI": record.get("DOI", ""),
                "PMID": record.get("PMID", ""),
                "Dedup Status": record.get("_dedup_status", "new"),
                "Planned Action": record.get("_planned_action", "create"),
                "Will Import": bool(record.get("_will_import", True)),
                "Cited By": record.get("_metric_citation_count"),
                "Journal Metric": record.get("_metric_journal_2yr_mean_citedness"),
                "Hybrid Score": record.get("_metric_hybrid_score"),
                "URL": record.get("url", ""),
            }
            for record in display_records
        ],
        width="stretch",
    )

    st.info(
        f"PubMed sort: {pubmed_sort} | Secondary sort: {secondary_sort} | "
        f"Attach metrics to extra: {attach_metrics_to_extra} | "
        f"Skip duplicates: {skip_duplicates} ({duplicate_scope})"
    )
    if skipped_existing or skipped_incoming:
        st.caption(
            f"Dedup summary: skipped existing={skipped_existing}, "
            f"skipped incoming duplicate={skipped_incoming}, "
            f"link to collection={link_existing}"
        )

    if dry_run:
        history_entry = build_history_entry(
            event_type="preview",
            query=query.strip(),
            pubmed_sort=pubmed_sort,
            secondary_sort=secondary_sort,
            target_collection_path=target_collection_path,
            duplicate_scope=duplicate_scope,
            skip_duplicates=skip_duplicates,
            library_type=library_type,
            library_id=resolved_library_id,
            display_records=display_records,
            import_records=import_records,
            skipped_existing=skipped_existing,
            skipped_incoming=skipped_incoming,
        )
        append_history_entry(history_entry)
        st.session_state["history_entries"] = load_history()
        st.session_state["preview_payload"] = {
            "created_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "query": query.strip(),
            "display_records": display_records,
            "import_records": import_records,
            "skipped_existing": skipped_existing,
            "skipped_incoming": skipped_incoming,
            "library_type": library_type,
            "library_id": resolved_library_id,
            "target_collection_path": target_collection_path,
            "auto_create_collection": auto_create_collection,
            "pubmed_sort": pubmed_sort,
            "secondary_sort": secondary_sort,
            "skip_duplicates": skip_duplicates,
            "duplicate_scope": duplicate_scope,
            "evaluation_signature": build_execution_signature(
                library_type=library_type,
                library_id=resolved_library_id,
                target_collection_path=target_collection_path,
                skip_duplicates=skip_duplicates,
                duplicate_scope=duplicate_scope,
            ),
        }
        if remember_settings:
            try:
                save_settings(current_settings())
            except Exception as exc:
                st.warning(f"Could not save settings: {exc}")
        st.success(
            "Preview cache updated. Use 'Import previewed records now' if you want to import without re-querying."
        )
        if target_collection_path:
            st.info(
                f"Preview mode only. Target collection path: {target_collection_path}"
            )
        else:
            st.info("Preview mode only. Target: library root.")
        st.rerun()

    if not import_records:
        st.info("No new records to import after duplicate filtering.")
        st.stop()

    with st.spinner("Importing into Zotero..."):
        try:
            total_success, total_failed, resolved_collection_key = (
                import_records_to_zotero(
                    records=import_records,
                    library_type=library_type,
                    library_id=validated_library_id,
                    zotero_api_key=zotero_api_key.strip(),
                    target_collection_path=target_collection_path,
                    auto_create_collection=auto_create_collection,
                )
            )
        except Exception as exc:
            st.error(f"Import failed: {exc}")
            st.stop()

    if remember_settings:
        try:
            save_settings(current_settings())
        except Exception as exc:
            st.warning(f"Could not save settings: {exc}")
    history_entry = build_history_entry(
        event_type="import",
        query=query.strip(),
        pubmed_sort=pubmed_sort,
        secondary_sort=secondary_sort,
        target_collection_path=target_collection_path,
        duplicate_scope=duplicate_scope,
        skip_duplicates=skip_duplicates,
        library_type=library_type,
        library_id=validated_library_id,
        display_records=display_records,
        import_records=import_records,
        skipped_existing=skipped_existing,
        skipped_incoming=skipped_incoming,
        total_success=total_success,
        total_failed=total_failed,
    )
    append_history_entry(history_entry)
    st.session_state["history_entries"] = load_history()
    st.session_state.pop("preview_payload", None)

    if resolved_collection_key:
        st.success(
            f"Import finished. Success: {total_success}, Failed: {total_failed}. "
            f"Collection key: {resolved_collection_key}"
        )
    else:
        st.success(
            f"Import finished. Success: {total_success}, Failed: {total_failed}."
        )
