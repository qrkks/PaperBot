from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st

from pubmed_to_zotero import (
    build_collection_paths,
    chunked,
    ensure_collection_path,
    fetch_pubmed_xml,
    list_collections,
    normalize_collection_path,
    parse_pubmed_articles,
    search_pubmed_ids,
    validate_library_id,
    zotero_create_items,
)


SETTINGS_FILE = Path(__file__).with_name(".paperbot_streamlit_settings.json")
SETTINGS_KEYS = [
    "zotero_user_id",
    "zotero_api_key",
    "library_type",
    "library_id_input",
    "ncbi_email",
    "ncbi_api_key",
    "manual_collection_path",
    "auto_create_collection",
    "remember_settings",
]
DEFAULT_SETTINGS: dict[str, Any] = {
    "zotero_user_id": "",
    "zotero_api_key": "",
    "library_type": "users",
    "library_id_input": "",
    "ncbi_email": "",
    "ncbi_api_key": "",
    "manual_collection_path": "",
    "auto_create_collection": True,
    "remember_settings": True,
}


def load_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: raw.get(k, DEFAULT_SETTINGS[k]) for k in SETTINGS_KEYS}


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
    loaded = load_settings()
    for key in SETTINGS_KEYS:
        st.session_state[key] = loaded.get(key, DEFAULT_SETTINGS[key])
    st.session_state["settings_initialized"] = True


st.set_page_config(page_title="PubMed to Zotero", page_icon=":books:", layout="centered")
bootstrap_settings()

st.title("PubMed to Zotero")
st.caption("Search PubMed and import directly into Zotero.")

if "collection_paths" not in st.session_state:
    st.session_state["collection_paths"] = []

query = st.text_input("PubMed query", placeholder="e.g. glioblastoma AND immunotherapy")
max_results = st.number_input("Max results", min_value=1, max_value=500, value=20, step=1)

st.markdown("### Zotero")
zotero_user_id = st.text_input("Zotero user ID", key="zotero_user_id")
zotero_api_key = st.text_input("Zotero API key", type="password", key="zotero_api_key")
library_type = st.selectbox("Library type", options=["users", "groups"], key="library_type")
library_id_input = st.text_input("Library ID (optional for personal library)", key="library_id_input")
remember_settings = st.checkbox("Remember credentials locally", key="remember_settings")
st.caption(f"Saved file: `{SETTINGS_FILE}`")

settings_col1, settings_col2 = st.columns(2)
with settings_col1:
    if st.button("Save settings now"):
        try:
            save_settings(current_settings())
            st.success("Settings saved.")
        except Exception as exc:
            st.error(f"Failed to save settings: {exc}")
with settings_col2:
    if st.button("Clear saved settings"):
        try:
            clear_settings()
            for key in SETTINGS_KEYS:
                st.session_state[key] = DEFAULT_SETTINGS[key]
            st.success("Saved settings cleared.")
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to clear settings: {exc}")

resolved_library_id = (library_id_input or "").strip() or (zotero_user_id or "").strip()

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

load_collections = st.button("Load existing collections")
if load_collections:
    if not resolved_library_id:
        st.error("Please provide Zotero user ID or library ID before loading collections.")
    elif not zotero_api_key.strip():
        st.error("Please provide Zotero API key before loading collections.")
    else:
        try:
            validated_library_id = validate_library_id(library_type, resolved_library_id)
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
                st.session_state["collection_paths"] = sorted(mapping.keys())
                st.success(f"Loaded {len(st.session_state['collection_paths'])} collection paths.")
                if remember_settings:
                    save_settings(current_settings())
            except Exception as exc:
                st.error(f"Failed to load collections: {exc}")

selected_existing_path = st.selectbox(
    "Select existing collection path (optional)",
    options=[""] + st.session_state["collection_paths"],
    index=0,
)

st.markdown("### NCBI (optional)")
ncbi_email = st.text_input("NCBI email", key="ncbi_email")
ncbi_api_key = st.text_input("NCBI API key", type="password", key="ncbi_api_key")

dry_run = st.checkbox("Preview only (do not write to Zotero)", value=True)
run = st.button("Run import")

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
            validated_library_id = validate_library_id(library_type, resolved_library_id)
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

    st.success(f"Fetched {len(records)} records.")
    st.dataframe(
        [
            {
                "Title": record.get("title", ""),
                "Journal": record.get("publicationTitle", ""),
                "Date": record.get("date", ""),
                "DOI": record.get("DOI", ""),
                "PMID": record.get("PMID", ""),
                "URL": record.get("url", ""),
            }
            for record in records
        ],
        use_container_width=True,
    )

    if dry_run:
        if remember_settings:
            try:
                save_settings(current_settings())
            except Exception as exc:
                st.warning(f"Could not save settings: {exc}")
        if target_collection_path:
            st.info(f"Preview mode only. Target collection path: {target_collection_path}")
        else:
            st.info("Preview mode only. Target: library root.")
        st.stop()

    resolved_collection_key: str | None = None
    if target_collection_path:
        with st.spinner("Resolving/creating target collection..."):
            try:
                resolved_collection_key = ensure_collection_path(
                    library_type=library_type,
                    library_id=validated_library_id,
                    api_key=zotero_api_key.strip(),
                    collection_path=target_collection_path,
                    auto_create=auto_create_collection,
                )
            except Exception as exc:
                st.error(f"Collection resolve/create failed: {exc}")
                st.stop()

    with st.spinner("Importing into Zotero..."):
        try:
            total_success = 0
            total_failed = 0
            for batch in chunked(records, 50):
                result = zotero_create_items(
                    library_type=library_type,
                    library_id=validated_library_id,
                    api_key=zotero_api_key.strip(),
                    items=batch,
                    collection_key=resolved_collection_key,
                )
                total_success += len(result.get("successful", {}) or {})
                total_failed += len(result.get("failed", {}) or {})
        except Exception as exc:
            st.error(f"Import failed: {exc}")
            st.stop()

    if remember_settings:
        try:
            save_settings(current_settings())
        except Exception as exc:
            st.warning(f"Could not save settings: {exc}")

    if resolved_collection_key:
        st.success(
            f"Import finished. Success: {total_success}, Failed: {total_failed}. "
            f"Collection key: {resolved_collection_key}"
        )
    else:
        st.success(f"Import finished. Success: {total_success}, Failed: {total_failed}.")
