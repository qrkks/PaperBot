#!/usr/bin/env python3
"""
Search PubMed and import results into Zotero.

Usage example:
python pubmed_to_zotero.py --query "glioblastoma immunotherapy 2025" --max-results 20
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any
from xml.etree import ElementTree

import requests


EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ZOTERO_BASE = "https://api.zotero.org"
ZOTERO_PAGE_SIZE = 100


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
        parent = str(data.get("parentCollection", "")).strip() or None

        if parent and parent in by_key:
            parent_path = _resolve_path(parent)
            path = f"{parent_path}/{name}" if parent_path else name
        else:
            path = name

        memo[collection_key] = path
        visiting.remove(collection_key)
        return path

    path_to_key: dict[str, str] = {}
    for key in by_key:
        path = _resolve_path(key)
        if path:
            path_to_key[path] = key
    return path_to_key


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


def _build_parent_name_index(collections: list[dict[str, Any]]) -> dict[tuple[str | None, str], str]:
    index: dict[tuple[str | None, str], str] = {}
    for collection in collections:
        key = str(collection.get("key", "")).strip()
        data = collection.get("data", {}) or {}
        name = str(data.get("name", "")).strip()
        parent = str(data.get("parentCollection", "")).strip() or None
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


def search_pubmed_ids(query: str, retmax: int, email: str | None, api_key: str | None) -> list[str]:
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": query,
        "retmax": retmax,
        "retmode": "json",
        "sort": "relevance",
    }
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key

    response = _request(f"{EUTILS_BASE}/esearch.fcgi", params)
    payload = response.json()
    id_list = payload.get("esearchresult", {}).get("idlist", [])
    return [str(pmid) for pmid in id_list]


def fetch_pubmed_xml(pmids: list[str], email: str | None, api_key: str | None) -> ElementTree.Element:
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
        volume = _text(article, "Journal/JournalIssue/Volume")
        issue = _text(article, "Journal/JournalIssue/Issue")
        year = _text(article, "Journal/JournalIssue/PubDate/Year")
        month = _text(article, "Journal/JournalIssue/PubDate/Month")
        day = _text(article, "Journal/JournalIssue/PubDate/Day")
        date_str = "-".join([x for x in [year, month, day] if x])

        pages = _text(article, "Pagination/MedlinePgn")

        doi = ""
        for article_id in pubmed_article.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if article_id.attrib.get("IdType") == "doi" and article_id.text:
                doi = article_id.text.strip()
                break

        item = {
            "itemType": "journalArticle",
            "title": title,
            "creators": _parse_authors(article),
            "abstractNote": abstract_note,
            "publicationTitle": journal_title,
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
        out = dict(item)
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
        raise SystemExit("Missing Zotero user id. Pass --zotero-user-id or set ZOTERO_USER_ID.")
    if not args.zotero_api_key and not args.dry_run:
        raise SystemExit("Missing Zotero API key. Pass --zotero-api-key or set ZOTERO_API_KEY.")

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

    pmids = search_pubmed_ids(args.query, args.max_results, args.ncbi_email, args.ncbi_api_key)
    if not pmids:
        print("No PubMed records found.")
        return 0

    root = fetch_pubmed_xml(pmids, args.ncbi_email, args.ncbi_api_key)
    records = parse_pubmed_articles(root)
    if not records:
        print("No parseable PubMed records found.")
        return 0

    print(f"Found {len(records)} records.")

    if args.dry_run:
        if collection_key:
            print(f"Target collection key: {collection_key}")
        elif collection_path:
            print(f"Target collection path: {collection_path}")
        else:
            print("Target: library root")
        for idx, record in enumerate(records, start=1):
            print(f"{idx}. {record.get('title', '(no title)')}")
        return 0

    resolved_collection_key = collection_key
    if not resolved_collection_key and collection_path:
        resolved_collection_key = ensure_collection_path(
            library_type=args.library_type,
            library_id=library_id,
            api_key=args.zotero_api_key,
            collection_path=collection_path,
            auto_create=auto_create_collection,
        )
        print(f"Resolved collection path '{collection_path}' -> key '{resolved_collection_key}'")
    elif resolved_collection_key:
        print(f"Using provided collection key: {resolved_collection_key}")
    else:
        print("Importing to library root (no collection selected).")

    total_success = 0
    total_failed = 0
    for batch in chunked(records, 50):
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

    print(f"Imported to Zotero. Success: {total_success}, Failed: {total_failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
