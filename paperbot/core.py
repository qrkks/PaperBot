#!/usr/bin/env python3
"""
Search PubMed and import results into Zotero.

Usage example:
python pubmed_to_zotero.py --query "glioblastoma immunotherapy 2025" --max-results 20
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import re
import time
from typing import Any
from xml.etree import ElementTree

import requests


EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ZOTERO_BASE = "https://api.zotero.org"
ZOTERO_PAGE_SIZE = 100
OPENALEX_BASE = "https://api.openalex.org"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_local_dotenv(env_path: str | None = None) -> None:
    env_path = env_path or os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
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


load_local_dotenv()

PUBMED_SORT_VALUES = ["relevance", "pub_date", "Author", "JournalName"]
SECONDARY_SORT_VALUES = [
    "none",
    "citation_count_desc",
    "journal_metric_desc",
    "hybrid_score_desc",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search PubMed and import records into Zotero."
    )
    parser.add_argument("--query", required=True, help="PubMed query string.")
    parser.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Maximum PubMed results to import (default: 20).",
    )
    parser.add_argument(
        "--pubmed-sort",
        choices=PUBMED_SORT_VALUES,
        default=os.getenv("PUBMED_SORT", "relevance"),
        help="PubMed API sort method.",
    )
    parser.add_argument(
        "--secondary-sort",
        choices=SECONDARY_SORT_VALUES,
        default=os.getenv("SECONDARY_SORT", "none"),
        help="Local secondary ranking after PubMed retrieval.",
    )
    parser.add_argument(
        "--zotero-user-id",
        default=os.getenv("ZOTERO_USER_ID"),
        help="Zotero user id (or set ZOTERO_USER_ID).",
    )
    parser.add_argument(
        "--zotero-api-key",
        default=os.getenv("ZOTERO_API_KEY"),
        help="Zotero API key (or set ZOTERO_API_KEY).",
    )
    parser.add_argument(
        "--library-type",
        choices=["users", "groups"],
        default=os.getenv("ZOTERO_LIBRARY_TYPE", "users"),
        help="Zotero library type: users or groups.",
    )
    parser.add_argument(
        "--library-id",
        default=os.getenv("ZOTERO_LIBRARY_ID"),
        help="Zotero library id for group library. Defaults to Zotero user id for users library.",
    )
    parser.add_argument(
        "--collection-path",
        default=os.getenv("ZOTERO_COLLECTION_PATH", ""),
        help="Target collection full path, e.g. ProjectA/Review.",
    )
    parser.add_argument(
        "--collection-key",
        default=os.getenv("ZOTERO_COLLECTION_KEY", ""),
        help="Target collection key. Highest priority when provided.",
    )
    parser.add_argument(
        "--no-create-collection-if-missing",
        action="store_true",
        help="Do not auto-create missing collection path.",
    )
    parser.add_argument(
        "--no-skip-duplicates",
        action="store_true",
        help="Do not skip items that already exist in Zotero (DOI/PMID check).",
    )
    parser.add_argument(
        "--openalex-email",
        default=os.getenv("OPENALEX_EMAIL"),
        help="Optional contact email for OpenAlex API polite usage.",
    )
    parser.add_argument(
        "--openalex-api-key",
        default=os.getenv("OPENALEX_API_KEY"),
        help="Optional OpenAlex API key.",
    )
    parser.add_argument(
        "--no-attach-metrics-to-extra",
        action="store_true",
        help="Do not write secondary metrics into Zotero extra field.",
    )
    parser.add_argument(
        "--ncbi-email",
        default=os.getenv("NCBI_EMAIL"),
        help="Optional email for NCBI E-utilities polite usage.",
    )
    parser.add_argument(
        "--ncbi-api-key",
        default=os.getenv("NCBI_API_KEY"),
        help="Optional NCBI API key.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write to Zotero, only print parsed records.",
    )
    return parser.parse_args()


def _request(url: str, params: dict[str, Any]) -> requests.Response:
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response


def _zotero_headers(api_key: str) -> dict[str, str]:
    return {
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
        "Content-Type": "application/json",
    }


def validate_library_id(library_type: str, library_id: str) -> str:
    normalized = (library_id or "").strip()
    if not normalized:
        raise ValueError("Library ID is required.")

    if library_type in {"users", "groups"} and not normalized.isdigit():
        if "@" in normalized:
            raise ValueError(
                "Invalid library ID: this looks like an email address. "
                "Use numeric Zotero user/group ID (e.g. 1234567), not email."
            )
        raise ValueError(
            "Invalid library ID: users/groups library ID must be numeric "
            "(e.g. 1234567)."
        )

    return normalized


def normalize_collection_path(path: str) -> str:
    parts = [segment.strip() for segment in path.split("/") if segment.strip()]
    return "/".join(parts)


def _normalize_parent_collection(value: Any) -> str | None:
    if value in (None, "", False):
        return None
    text = str(value).strip()
    if not text or text.lower() == "false":
        return None
    return text


def list_collections(
    library_type: str,
    library_id: str,
    api_key: str,
    page_size: int = ZOTERO_PAGE_SIZE,
) -> list[dict[str, Any]]:
    collections: list[dict[str, Any]] = []
    start = 0
    normalized_library_id = validate_library_id(library_type, library_id)
    url = f"{ZOTERO_BASE}/{library_type}/{normalized_library_id}/collections"

    while True:
        response = requests.get(
            url,
            headers=_zotero_headers(api_key),
            params={"limit": page_size, "start": start},
            timeout=30,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            if response.status_code == 400:
                raise RuntimeError(
                    "Zotero returned 400 for collections API. "
                    "Please verify library type/id and ensure ID is numeric."
                ) from exc
            raise
        batch = response.json()
        if not isinstance(batch, list):
            raise RuntimeError("Unexpected Zotero collections response format.")
        collections.extend(batch)
        if len(batch) < page_size:
            break
        start += len(batch)

    return collections


def build_collection_paths(collections: list[dict[str, Any]]) -> dict[str, str]:
    path_to_keys = build_collection_path_keys(collections)
    return {
        path: keys[0]
        for path, keys in path_to_keys.items()
        if path and len(keys) == 1
    }


def build_collection_path_keys(collections: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_key: dict[str, dict[str, Any]] = {}
    for collection in collections:
        key = str(collection.get("key", "")).strip()
        if key:
            by_key[key] = collection

    memo: dict[str, str] = {}
    visiting: set[str] = set()

    def _resolve_path(collection_key: str) -> str:
        if collection_key in memo:
            return memo[collection_key]
        if collection_key in visiting:
            return ""
        visiting.add(collection_key)

        collection = by_key.get(collection_key, {})
        data = collection.get("data", {}) or {}
        name = str(data.get("name", "")).strip()
        parent = _normalize_parent_collection(data.get("parentCollection"))

        if parent and parent in by_key:
            parent_path = _resolve_path(parent)
            path = f"{parent_path}/{name}" if parent_path else name
        else:
            path = name

        memo[collection_key] = path
        visiting.remove(collection_key)
        return path

    path_to_keys: dict[str, list[str]] = {}
    for key in by_key:
        path = _resolve_path(key)
        if path:
            path_to_keys.setdefault(path, []).append(key)
    return path_to_keys


def find_ambiguous_collection_paths(collections: list[dict[str, Any]]) -> dict[str, list[str]]:
    path_to_keys = build_collection_path_keys(collections)
    return {
        path: keys
        for path, keys in path_to_keys.items()
        if path and len(keys) > 1
    }


def create_collection(
    library_type: str,
    library_id: str,
    api_key: str,
    name: str,
    parent_collection: str | None = None,
) -> str:
    if not name.strip():
        raise ValueError("Collection name cannot be empty.")

    payload: dict[str, Any] = {"name": name.strip()}
    if parent_collection:
        payload["parentCollection"] = parent_collection

    normalized_library_id = validate_library_id(library_type, library_id)
    url = f"{ZOTERO_BASE}/{library_type}/{normalized_library_id}/collections"
    response = requests.post(
        url,
        headers=_zotero_headers(api_key),
        json=[payload],
        timeout=30,
    )
    response.raise_for_status()

    result = response.json() if response.text else {}
    successful = result.get("successful", {}) if isinstance(result, dict) else {}
    if successful:
        first_value = next(iter(successful.values()))
        if isinstance(first_value, dict):
            key = str(first_value.get("key", "")).strip()
            if key:
                return key
        key = str(first_value).strip()
        if key:
            return key

    raise RuntimeError("Collection created but key was not returned by Zotero API.")


def _build_parent_name_index(
    collections: list[dict[str, Any]],
) -> dict[tuple[str | None, str], str]:
    index: dict[tuple[str | None, str], str] = {}
    for collection in collections:
        key = str(collection.get("key", "")).strip()
        data = collection.get("data", {}) or {}
        name = str(data.get("name", "")).strip()
        parent = _normalize_parent_collection(data.get("parentCollection"))
        if key and name:
            index[(parent, name)] = key
    return index


def ensure_collection_path(
    library_type: str,
    library_id: str,
    api_key: str,
    collection_path: str,
    auto_create: bool = True,
) -> str:
    normalized = normalize_collection_path(collection_path)
    if not normalized:
        raise ValueError("Collection path is empty.")

    collections = list_collections(
        library_type=library_type,
        library_id=library_id,
        api_key=api_key,
    )
    ambiguous_paths = find_ambiguous_collection_paths(collections)
    ambiguous_keys = ambiguous_paths.get(normalized, [])
    if ambiguous_keys:
        keys_text = ", ".join(ambiguous_keys)
        raise RuntimeError(
            f"Collection path '{normalized}' is ambiguous in Zotero "
            f"(multiple matching collections: {keys_text}). "
            "Please clean up duplicate collection names under the same parent before importing."
        )
    index = _build_parent_name_index(collections)

    parent_key: str | None = None
    segments = normalized.split("/")
    for segment in segments:
        existing = index.get((parent_key, segment))
        if existing:
            parent_key = existing
            continue

        if not auto_create:
            raise RuntimeError(
                f"Collection path segment not found: '{segment}' in '{normalized}'."
            )

        new_key = create_collection(
            library_type=library_type,
            library_id=library_id,
            api_key=api_key,
            name=segment,
            parent_collection=parent_key,
        )
        index[(parent_key, segment)] = new_key
        parent_key = new_key

    if not parent_key:
        raise RuntimeError(f"Failed to resolve collection path: {normalized}")
    return parent_key


def search_pubmed_ids(
    query: str,
    retmax: int,
    email: str | None,
    api_key: str | None,
    sort: str = "relevance",
) -> list[str]:
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": retmax,
        "retmode": "json",
        "sort": sort,
    }
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key

    response = _request(f"{EUTILS_BASE}/esearch.fcgi", params)
    payload = response.json()
    id_list = payload.get("esearchresult", {}).get("idlist", [])
    return [str(pmid) for pmid in id_list]


def fetch_pubmed_xml(
    pmids: list[str], email: str | None, api_key: str | None
) -> ElementTree.Element:
    params: dict[str, Any] = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key

    response = _request(f"{EUTILS_BASE}/efetch.fcgi", params)
    return ElementTree.fromstring(response.text)


def _text(node: ElementTree.Element | None, path: str, default: str = "") -> str:
    if node is None:
        return default
    child = node.find(path)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _parse_authors(article: ElementTree.Element) -> list[dict[str, str]]:
    creators: list[dict[str, str]] = []
    for author in article.findall(".//AuthorList/Author"):
        collective = _text(author, "CollectiveName")
        if collective:
            creators.append({"creatorType": "author", "name": collective})
            continue

        last = _text(author, "LastName")
        first = _text(author, "ForeName") or _text(author, "Initials")
        if last or first:
            creators.append(
                {
                    "creatorType": "author",
                    "lastName": last,
                    "firstName": first,
                }
            )
    return creators


def parse_pubmed_articles(root: ElementTree.Element) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for pubmed_article in root.findall(".//PubmedArticle"):
        medline = pubmed_article.find("MedlineCitation")
        article = medline.find("Article") if medline is not None else None
        if medline is None or article is None:
            continue

        pmid = _text(medline, "PMID")
        title = _text(article, "ArticleTitle")
        abstract_parts = []
        for node in article.findall("Abstract/AbstractText"):
            text = (node.text or "").strip()
            if text:
                label = node.attrib.get("Label", "").strip()
                abstract_parts.append(f"{label}: {text}" if label else text)
        abstract_note = "\n\n".join(abstract_parts)

        journal_title = _text(article, "Journal/Title")
        issn = _text(article, "Journal/ISSN")
        volume = _text(article, "Journal/JournalIssue/Volume")
        issue = _text(article, "Journal/JournalIssue/Issue")
        year = _text(article, "Journal/JournalIssue/PubDate/Year")
        month = _text(article, "Journal/JournalIssue/PubDate/Month")
        day = _text(article, "Journal/JournalIssue/PubDate/Day")
        date_str = "-".join([x for x in [year, month, day] if x])

        pages = _text(article, "Pagination/MedlinePgn")

        doi = ""
        for article_id in pubmed_article.findall(
            ".//PubmedData/ArticleIdList/ArticleId"
        ):
            if article_id.attrib.get("IdType") == "doi" and article_id.text:
                doi = article_id.text.strip()
                break

        item = {
            "itemType": "journalArticle",
            "title": title,
            "creators": _parse_authors(article),
            "abstractNote": abstract_note,
            "publicationTitle": journal_title,
            "ISSN": issn,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "date": date_str,
            "DOI": doi,
            "PMID": pmid,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            "extra": f"PMID: {pmid}" if pmid else "",
            "language": _text(article, "Language"),
        }
        items.append(item)
    return items


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_pmid_value(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"(\d+)", raw)
    return match.group(1) if match else ""


def _normalize_doi(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.replace("https://doi.org/", "").replace("http://doi.org/", "")
    raw = raw.replace("doi:", "").strip()
    return raw


def _extract_pmid_from_extra(extra_value: Any) -> str:
    extra = str(extra_value or "")
    for line in extra.splitlines():
        text = line.strip()
        if text.lower().startswith("pmid:"):
            return _normalize_pmid_value(text.split(":", 1)[1])
    return ""


def list_existing_identifiers(
    library_type: str,
    library_id: str,
    api_key: str,
    collection_key: str | None = None,
    page_size: int = ZOTERO_PAGE_SIZE,
) -> tuple[set[str], set[str]]:
    normalized_library_id = validate_library_id(library_type, library_id)
    if collection_key:
        base_url = f"{ZOTERO_BASE}/{library_type}/{normalized_library_id}/collections/{collection_key}/items"
    else:
        base_url = f"{ZOTERO_BASE}/{library_type}/{normalized_library_id}/items"

    seen_dois: set[str] = set()
    seen_pmids: set[str] = set()
    start = 0

    while True:
        response = requests.get(
            base_url,
            headers=_zotero_headers(api_key),
            params={
                "limit": page_size,
                "start": start,
                "format": "json",
                "include": "data",
            },
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list):
            break
        if not batch:
            break

        for item in batch:
            data = item.get("data", {}) if isinstance(item, dict) else {}
            doi = _normalize_doi(data.get("DOI"))
            pmid = _normalize_pmid_value(data.get("PMID")) or _extract_pmid_from_extra(
                data.get("extra")
            )
            if doi:
                seen_dois.add(doi)
            if pmid:
                seen_pmids.add(pmid)

        if len(batch) < page_size:
            break
        start += len(batch)

    return seen_dois, seen_pmids


def list_existing_items_info(
    library_type: str,
    library_id: str,
    api_key: str,
    page_size: int = ZOTERO_PAGE_SIZE,
) -> list[dict[str, Any]]:
    normalized_library_id = validate_library_id(library_type, library_id)
    base_url = f"{ZOTERO_BASE}/{library_type}/{normalized_library_id}/items"
    items: list[dict[str, Any]] = []
    start = 0

    while True:
        response = requests.get(
            base_url,
            headers=_zotero_headers(api_key),
            params={
                "limit": page_size,
                "start": start,
                "format": "json",
                "include": "data",
            },
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list) or not batch:
            break

        for item in batch:
            if not isinstance(item, dict):
                continue
            data = item.get("data", {}) or {}
            items.append(
                {
                    "key": str(item.get("key", "")).strip(),
                    "version": _safe_int(item.get("version")),
                    "collections": list(data.get("collections", []) or []),
                    "DOI": _normalize_doi(data.get("DOI")),
                    "PMID": _normalize_pmid_value(data.get("PMID"))
                    or _extract_pmid_from_extra(data.get("extra")),
                }
            )

        if len(batch) < page_size:
            break
        start += len(batch)

    return items


def build_existing_item_indexes(
    existing_items: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    doi_index: dict[str, list[dict[str, Any]]] = {}
    pmid_index: dict[str, list[dict[str, Any]]] = {}

    for item in existing_items:
        doi = _normalize_doi(item.get("DOI"))
        pmid = _normalize_pmid_value(item.get("PMID"))
        if doi:
            doi_index.setdefault(doi, []).append(item)
        if pmid:
            pmid_index.setdefault(pmid, []).append(item)

    return doi_index, pmid_index


def _matching_existing_items(
    doi: str,
    pmid: str,
    doi_index: dict[str, list[dict[str, Any]]],
    pmid_index: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    matched: dict[str, dict[str, Any]] = {}
    for item in doi_index.get(doi, []):
        key = str(item.get("key", "")).strip()
        if key:
            matched[key] = item
    for item in pmid_index.get(pmid, []):
        key = str(item.get("key", "")).strip()
        if key:
            matched[key] = item
    return list(matched.values())


def plan_record_import_actions(
    records: list[dict[str, Any]],
    existing_items: list[dict[str, Any]],
    target_collection_key: str | None = None,
    target_collection_requested: bool = False,
    duplicate_scope: str = "library",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, int]:
    effective_scope = duplicate_scope if duplicate_scope in {"library", "collection"} else "library"
    if effective_scope == "collection" and target_collection_key:
        scoped_existing_items = [
            item
            for item in existing_items
            if target_collection_key in (item.get("collections", []) or [])
        ]
    else:
        scoped_existing_items = []

    doi_index_all, pmid_index_all = build_existing_item_indexes(existing_items)
    doi_index_scope, pmid_index_scope = build_existing_item_indexes(scoped_existing_items)
    action_records: list[dict[str, Any]] = []
    display_records: list[dict[str, Any]] = []
    incoming_dois: set[str] = set()
    incoming_pmids: set[str] = set()
    skipped_existing = 0
    skipped_incoming = 0
    link_existing = 0

    for record in records:
        item = dict(record)
        doi = _normalize_doi(item.get("DOI"))
        pmid = _normalize_pmid_value(item.get("PMID")) or _extract_pmid_from_extra(
            item.get("extra")
        )

        duplicate_in_batch = (doi and doi in incoming_dois) or (pmid and pmid in incoming_pmids)
        if duplicate_in_batch:
            item["_dedup_status"] = "duplicate_incoming"
            item["_planned_action"] = "skip"
            item["_will_import"] = False
            skipped_incoming += 1
            display_records.append(item)
            continue

        matched_existing = _matching_existing_items(doi, pmid, doi_index_all, pmid_index_all)
        matched_in_scope = _matching_existing_items(
            doi,
            pmid,
            doi_index_scope,
            pmid_index_scope,
        )
        if effective_scope == "collection" and matched_in_scope:
            preferred = matched_in_scope[0]
            item["_dedup_status"] = "duplicate_existing"
            item["_planned_action"] = "skip"
            item["_will_import"] = False
            item["_existing_item_key"] = str(preferred.get("key", "")).strip()
            item["_existing_item_version"] = preferred.get("version")
            item["_existing_item_collections"] = list(preferred.get("collections", []) or [])
            skipped_existing += 1
            display_records.append(item)
            continue

        if matched_existing:
            preferred = matched_existing[0]
            in_target = False
            if target_collection_key:
                in_target = any(
                    str(collection_key).strip() == target_collection_key
                    for collection_key in (preferred.get("collections", []) or [])
                )

            if target_collection_requested and not in_target:
                item["_dedup_status"] = "existing_add_to_collection"
                item["_planned_action"] = "link"
                item["_will_import"] = True
                item["_existing_item_key"] = str(preferred.get("key", "")).strip()
                item["_existing_item_version"] = preferred.get("version")
                item["_existing_item_collections"] = list(preferred.get("collections", []) or [])
                action_records.append(item)
                link_existing += 1
                if doi:
                    incoming_dois.add(doi)
                if pmid:
                    incoming_pmids.add(pmid)
                display_records.append(item)
                continue

            item["_dedup_status"] = "duplicate_existing"
            item["_planned_action"] = "skip"
            item["_will_import"] = False
            skipped_existing += 1
            display_records.append(item)
            continue

        item["_dedup_status"] = "new"
        item["_planned_action"] = "create"
        item["_will_import"] = True
        action_records.append(item)
        if doi:
            incoming_dois.add(doi)
        if pmid:
            incoming_pmids.add(pmid)
        display_records.append(item)

    return action_records, display_records, skipped_existing, skipped_incoming, link_existing


def zotero_link_existing_items_to_collection(
    library_type: str,
    library_id: str,
    api_key: str,
    collection_key: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_library_id = validate_library_id(library_type, library_id)
    url = f"{ZOTERO_BASE}/{library_type}/{normalized_library_id}/items"
    payload_items: list[dict[str, Any]] = []

    for item in items:
        item_key = str(item.get("_existing_item_key", "")).strip()
        if not item_key:
            continue
        collections = [str(value).strip() for value in list(item.get("_existing_item_collections", []) or []) if str(value).strip()]
        if collection_key not in collections:
            collections.append(collection_key)
        payload: dict[str, Any] = {
            "key": item_key,
            "collections": collections,
        }
        version = _safe_int(item.get("_existing_item_version"))
        if version is not None:
            payload["version"] = version
        payload_items.append(payload)

    if not payload_items:
        return {"successful": {}, "failed": {}}

    response = requests.post(
        url,
        headers=_zotero_headers(api_key),
        json=payload_items,
        timeout=30,
    )
    response.raise_for_status()
    if response.text:
        return response.json()
    return {"successful": {}, "failed": {}}


def filter_duplicate_records(
    records: list[dict[str, Any]],
    existing_dois: set[str],
    existing_pmids: set[str],
) -> tuple[list[dict[str, Any]], int, int]:
    filtered: list[dict[str, Any]] = []
    incoming_dois: set[str] = set()
    incoming_pmids: set[str] = set()
    skipped_existing = 0
    skipped_incoming = 0

    for record in records:
        doi = _normalize_doi(record.get("DOI"))
        pmid = _normalize_pmid_value(record.get("PMID")) or _extract_pmid_from_extra(
            record.get("extra")
        )

        already_exists = (doi and doi in existing_dois) or (
            pmid and pmid in existing_pmids
        )
        if already_exists:
            skipped_existing += 1
            continue

        duplicate_in_incoming = (doi and doi in incoming_dois) or (
            pmid and pmid in incoming_pmids
        )
        if duplicate_in_incoming:
            skipped_incoming += 1
            continue

        filtered.append(record)
        if doi:
            incoming_dois.add(doi)
        if pmid:
            incoming_pmids.add(pmid)

    return filtered, skipped_existing, skipped_incoming


def _openalex_common_params(email: str | None, api_key: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if email:
        params["mailto"] = email
    if api_key:
        params["api_key"] = api_key
    return params


def fetch_openalex_source_metrics_by_issn(
    issns: list[str],
    email: str | None = None,
    api_key: str | None = None,
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for issn in sorted({item.strip() for item in issns if item and item.strip()}):
        url = f"{OPENALEX_BASE}/sources/{issn}"
        params = _openalex_common_params(email, api_key)
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        payload = response.json()
        stats = payload.get("summary_stats", {}) if isinstance(payload, dict) else {}
        metric = _safe_float(stats.get("2yr_mean_citedness"))
        if metric is not None:
            metrics[issn] = metric
    return metrics


def fetch_openalex_metrics_by_pmids(
    pmids: list[str],
    email: str | None = None,
    api_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    normalized_pmids = sorted({_normalize_pmid_value(pmid) for pmid in pmids if pmid})
    normalized_pmids = [pmid for pmid in normalized_pmids if pmid]
    if not normalized_pmids:
        return {}

    metrics: dict[str, dict[str, Any]] = {}
    pmid_to_source_issn: dict[str, str] = {}

    for start in range(0, len(normalized_pmids), 100):
        pmid_batch = normalized_pmids[start : start + 100]
        params: dict[str, Any] = {
            "filter": f"pmid:{'|'.join(pmid_batch)}",
            "per-page": 200,
            "select": "id,ids,cited_by_count,primary_location",
        }
        params.update(_openalex_common_params(email, api_key))

        response = requests.get(f"{OPENALEX_BASE}/works", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        works = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(works, list):
            continue

        for work in works:
            work_ids = work.get("ids", {}) if isinstance(work, dict) else {}
            pmid = _normalize_pmid_value(work.get("pmid") or work_ids.get("pmid"))
            if not pmid:
                continue

            citation_count = _safe_int(work.get("cited_by_count"))
            primary_location = work.get("primary_location", {}) or {}
            source = primary_location.get("source", {}) or {}

            source_name = str(source.get("display_name", "")).strip()
            source_issn = str(source.get("issn_l", "")).strip()
            if not source_issn:
                issn_list = source.get("issn", [])
                if isinstance(issn_list, list):
                    source_issn = str(next((x for x in issn_list if x), "")).strip()

            source_stats = source.get("summary_stats", {}) or {}
            journal_metric = _safe_float(source_stats.get("2yr_mean_citedness"))

            previous = metrics.get(pmid)
            if previous:
                previous_citation = _safe_int(previous.get("citation_count"))
                if (
                    previous_citation is not None
                    and citation_count is not None
                    and previous_citation > citation_count
                ):
                    continue

            metrics[pmid] = {
                "citation_count": citation_count,
                "journal_metric_2yr_mean_citedness": journal_metric,
                "source_name": source_name,
                "source_issn": source_issn,
                "openalex_work_id": str(work.get("id", "")).strip(),
            }
            if source_issn and journal_metric is None:
                pmid_to_source_issn[pmid] = source_issn

    missing_issns = [
        issn
        for pmid, issn in pmid_to_source_issn.items()
        if _safe_float(metrics.get(pmid, {}).get("journal_metric_2yr_mean_citedness"))
        is None
    ]
    source_metric_map = fetch_openalex_source_metrics_by_issn(
        issns=missing_issns,
        email=email,
        api_key=api_key,
    )

    for pmid, issn in pmid_to_source_issn.items():
        metric = source_metric_map.get(issn)
        if metric is None:
            continue
        existing = metrics.get(pmid)
        if not existing:
            continue
        if _safe_float(existing.get("journal_metric_2yr_mean_citedness")) is None:
            existing["journal_metric_2yr_mean_citedness"] = metric

    return metrics


def backfill_journal_metrics_from_record_issns(
    records: list[dict[str, Any]],
    metrics_by_pmid: dict[str, dict[str, Any]],
    email: str | None = None,
    api_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    pmid_to_issn: dict[str, str] = {}
    for record in records:
        pmid = _normalize_pmid_value(record.get("PMID"))
        if not pmid:
            continue
        existing_metric = _safe_float(
            metrics_by_pmid.get(pmid, {}).get("journal_metric_2yr_mean_citedness")
        )
        if existing_metric is not None:
            continue
        issn = str(record.get("ISSN", "") or "").strip()
        if issn:
            pmid_to_issn[pmid] = issn

    if not pmid_to_issn:
        return metrics_by_pmid

    source_metric_map = fetch_openalex_source_metrics_by_issn(
        issns=list(pmid_to_issn.values()),
        email=email,
        api_key=api_key,
    )
    for pmid, issn in pmid_to_issn.items():
        metric = source_metric_map.get(issn)
        if metric is None:
            continue
        metrics_by_pmid.setdefault(pmid, {})[
            "journal_metric_2yr_mean_citedness"
        ] = metric

    return metrics_by_pmid


def _compute_hybrid_score(
    citation_count: int | None, journal_metric: float | None
) -> float | None:
    if citation_count is None and journal_metric is None:
        return None
    cited_component = math.log1p(max(citation_count or 0, 0))
    journal_component = max(journal_metric or 0.0, 0.0)
    return 0.7 * cited_component + 0.3 * journal_component


def _upsert_extra_metric_lines(
    existing_extra: str,
    citation_count: int | None,
    journal_metric: float | None,
    source_name: str,
    secondary_sort: str,
    snapshot_date: str,
) -> str:
    lines = [
        line.strip() for line in (existing_extra or "").splitlines() if line.strip()
    ]
    managed_prefixes = (
        "OpenAlex Cited By:",
        "OpenAlex Journal 2yr Mean Citedness:",
        "OpenAlex Source:",
        "Secondary Sort:",
        "Metrics Snapshot Date:",
    )
    lines = [line for line in lines if not line.startswith(managed_prefixes)]

    if citation_count is not None:
        lines.append(f"OpenAlex Cited By: {citation_count}")
    if journal_metric is not None:
        lines.append(f"OpenAlex Journal 2yr Mean Citedness: {journal_metric:.4f}")
    if source_name:
        lines.append(f"OpenAlex Source: {source_name}")
    lines.append(f"Secondary Sort: {secondary_sort}")
    lines.append(f"Metrics Snapshot Date: {snapshot_date}")
    return "\n".join(lines)


def apply_secondary_metrics_to_records(
    records: list[dict[str, Any]],
    metrics_by_pmid: dict[str, dict[str, Any]],
    secondary_sort: str,
    attach_to_extra: bool = True,
    snapshot_date: str | None = None,
) -> None:
    metric_date = snapshot_date or dt.date.today().isoformat()
    for record in records:
        pmid = _normalize_pmid_value(record.get("PMID"))
        metric = metrics_by_pmid.get(pmid, {})

        citation_count = _safe_int(metric.get("citation_count"))
        journal_metric = _safe_float(metric.get("journal_metric_2yr_mean_citedness"))
        source_name = str(metric.get("source_name", "")).strip()
        hybrid_score = _compute_hybrid_score(citation_count, journal_metric)

        record["_metric_citation_count"] = citation_count
        record["_metric_journal_2yr_mean_citedness"] = journal_metric
        record["_metric_hybrid_score"] = hybrid_score
        record["_metric_source_name"] = source_name

        if attach_to_extra:
            record["extra"] = _upsert_extra_metric_lines(
                existing_extra=str(record.get("extra", "")),
                citation_count=citation_count,
                journal_metric=journal_metric,
                source_name=source_name,
                secondary_sort=secondary_sort,
                snapshot_date=metric_date,
            )


def secondary_sort_records(
    records: list[dict[str, Any]], secondary_sort: str
) -> list[dict[str, Any]]:
    if secondary_sort == "none":
        return records
    if secondary_sort == "citation_count_desc":
        return sorted(
            records,
            key=lambda row: (
                row.get("_metric_citation_count") is not None,
                row.get("_metric_citation_count") or -1,
            ),
            reverse=True,
        )
    if secondary_sort == "journal_metric_desc":
        return sorted(
            records,
            key=lambda row: (
                row.get("_metric_journal_2yr_mean_citedness") is not None,
                row.get("_metric_journal_2yr_mean_citedness") or -1.0,
                row.get("_metric_citation_count") or -1,
            ),
            reverse=True,
        )
    if secondary_sort == "hybrid_score_desc":
        return sorted(
            records,
            key=lambda row: (
                row.get("_metric_hybrid_score") is not None,
                row.get("_metric_hybrid_score") or -1.0,
            ),
            reverse=True,
        )
    raise ValueError(f"Unsupported secondary sort mode: {secondary_sort}")


def zotero_create_items(
    library_type: str,
    library_id: str,
    api_key: str,
    items: list[dict[str, Any]],
    collection_key: str | None = None,
) -> dict[str, Any]:
    normalized_library_id = validate_library_id(library_type, library_id)
    url = f"{ZOTERO_BASE}/{library_type}/{normalized_library_id}/items"
    payload_items: list[dict[str, Any]] = []
    for item in items:
        out = {
            key: value for key, value in item.items() if not str(key).startswith("_")
        }
        if collection_key:
            existing = list(out.get("collections", []))
            if collection_key not in existing:
                existing.append(collection_key)
            out["collections"] = existing
        payload_items.append(out)

    response = requests.post(
        url,
        headers=_zotero_headers(api_key),
        json=payload_items,
        timeout=30,
    )
    response.raise_for_status()
    if response.text:
        return response.json()
    return {"successful": {}}


def chunked(seq: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def main() -> int:
    args = parse_args()

    if not args.zotero_user_id:
        raise SystemExit(
            "Missing Zotero user id. Pass --zotero-user-id or set ZOTERO_USER_ID."
        )
    if not args.zotero_api_key and not args.dry_run:
        raise SystemExit(
            "Missing Zotero API key. Pass --zotero-api-key or set ZOTERO_API_KEY."
        )

    library_id = args.library_id or args.zotero_user_id
    if not library_id:
        raise SystemExit("Missing Zotero library id.")
    try:
        library_id = validate_library_id(args.library_type, library_id)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    collection_key = (args.collection_key or "").strip() or None
    collection_path = normalize_collection_path(args.collection_path or "")
    auto_create_collection = not args.no_create_collection_if_missing
    skip_duplicates = not args.no_skip_duplicates
    attach_metrics_to_extra = not args.no_attach_metrics_to_extra

    pmids = search_pubmed_ids(
        args.query,
        args.max_results,
        args.ncbi_email,
        args.ncbi_api_key,
        sort=args.pubmed_sort,
    )
    if not pmids:
        print("No PubMed records found.")
        return 0

    root = fetch_pubmed_xml(pmids, args.ncbi_email, args.ncbi_api_key)
    records = parse_pubmed_articles(root)
    if not records:
        print("No parseable PubMed records found.")
        return 0

    print(f"Found {len(records)} records (PubMed sort: {args.pubmed_sort}).")

    should_fetch_secondary_metrics = (
        args.secondary_sort != "none" or attach_metrics_to_extra
    )
    if should_fetch_secondary_metrics:
        try:
            metrics = fetch_openalex_metrics_by_pmids(
                pmids=[str(record.get("PMID", "")).strip() for record in records],
                email=args.openalex_email,
                api_key=args.openalex_api_key,
            )
            apply_secondary_metrics_to_records(
                records=records,
                metrics_by_pmid=metrics,
                secondary_sort=args.secondary_sort,
                attach_to_extra=attach_metrics_to_extra,
            )
        except Exception as exc:
            if args.secondary_sort != "none":
                raise SystemExit(
                    f"Secondary sorting requires metrics, but metric fetch failed: {exc}"
                ) from exc
            print(f"Warning: metric fetch failed, continuing without metrics. {exc}")

    if args.secondary_sort != "none":
        records = secondary_sort_records(records, args.secondary_sort)
        print(f"Applied secondary sort: {args.secondary_sort}")

    target_collection_requested = bool(collection_key or collection_path)
    resolved_collection_key = collection_key
    if not resolved_collection_key and collection_path and args.zotero_api_key and not args.dry_run:
        resolved_collection_key = ensure_collection_path(
            library_type=args.library_type,
            library_id=library_id,
            api_key=args.zotero_api_key,
            collection_path=collection_path,
            auto_create=auto_create_collection,
        )
        print(
            f"Resolved collection path '{collection_path}' -> key '{resolved_collection_key}'"
        )

    if skip_duplicates and args.zotero_api_key:
        try:
            existing_items = list_existing_items_info(
                library_type=args.library_type,
                library_id=library_id,
                api_key=args.zotero_api_key,
            )
            records, display_records, skipped_existing, skipped_incoming, link_existing = plan_record_import_actions(
                records,
                existing_items=existing_items,
                target_collection_key=resolved_collection_key,
                target_collection_requested=target_collection_requested,
            )
            if skipped_existing or skipped_incoming or link_existing:
                print(
                    "Dedup summary: "
                    f"skipped existing={skipped_existing}, "
                    f"skipped incoming duplicate={skipped_incoming}, "
                    f"link to collection={link_existing}"
                )
        except Exception as exc:
            if args.dry_run:
                print(f"Warning: duplicate check failed in dry-run. {exc}")
            else:
                raise SystemExit(f"Duplicate check failed: {exc}") from exc
    elif skip_duplicates and not args.zotero_api_key and args.dry_run:
        print(
            "Note: duplicate check skipped in dry-run because Zotero API key is missing."
        )
        display_records = list(records)
    else:
        display_records = list(records)

    if not records:
        print("No pending actions to import after duplicate filtering.")
        return 0

    if args.dry_run:
        if collection_key:
            print(f"Target collection key: {collection_key}")
        elif collection_path:
            print(f"Target collection path: {collection_path}")
        else:
            print("Target: library root")
        for idx, record in enumerate(display_records, start=1):
            citation_count = record.get("_metric_citation_count")
            journal_metric = record.get("_metric_journal_2yr_mean_citedness")
            metric_text = []
            if citation_count is not None:
                metric_text.append(f"cited_by={citation_count}")
            if journal_metric is not None:
                metric_text.append(f"journal_metric={journal_metric:.4f}")
            action = str(record.get("_planned_action", "create"))
            status = str(record.get("_dedup_status", "new"))
            metric_text.append(f"status={status}")
            metric_text.append(f"action={action}")
            suffix = f" [{' '.join(metric_text)}]" if metric_text else ""
            print(f"{idx}. {record.get('title', '(no title)')}{suffix}")
        return 0

    if not resolved_collection_key and collection_path:
        resolved_collection_key = ensure_collection_path(
            library_type=args.library_type,
            library_id=library_id,
            api_key=args.zotero_api_key,
            collection_path=collection_path,
            auto_create=auto_create_collection,
        )
        print(
            f"Resolved collection path '{collection_path}' -> key '{resolved_collection_key}'"
        )
    elif resolved_collection_key:
        print(f"Using provided collection key: {resolved_collection_key}")
    else:
        print("Importing to library root (no collection selected).")

    total_success = 0
    total_failed = 0
    create_records = [record for record in records if record.get("_planned_action") != "link"]
    link_records = [record for record in records if record.get("_planned_action") == "link"]

    for batch in chunked(create_records, 50):
        if not batch:
            continue
        result = zotero_create_items(
            library_type=args.library_type,
            library_id=library_id,
            api_key=args.zotero_api_key,
            items=batch,
            collection_key=resolved_collection_key,
        )
        success_count = len(result.get("successful", {}) or {})
        failed_count = len(result.get("failed", {}) or {})
        total_success += success_count
        total_failed += failed_count
        time.sleep(0.35)

    for batch in chunked(link_records, 50):
        if not batch or not resolved_collection_key:
            continue
        result = zotero_link_existing_items_to_collection(
            library_type=args.library_type,
            library_id=library_id,
            api_key=args.zotero_api_key,
            collection_key=resolved_collection_key,
            items=batch,
        )
        success_count = len(result.get("successful", {}) or {})
        failed_count = len(result.get("failed", {}) or {})
        total_success += success_count
        total_failed += failed_count
        time.sleep(0.35)

    print(f"Imported to Zotero. Success: {total_success}, Failed: {total_failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
