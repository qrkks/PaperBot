# PaperBot

PaperBot helps you search PubMed, review results, and send selected records into Zotero with a safer, researcher-friendly workflow.

It includes:

- a CLI importer: `pubmed_to_zotero.py`
- a Streamlit web app: `app_streamlit.py`

## Features

- Search PubMed from a query string
- Import into Zotero personal or group libraries
- Select or auto-create Zotero collection paths
- Separate PubMed sorting from local secondary reranking
- Optional OpenAlex-based citation and journal metrics
- Preview before import
- Re-check duplicate status against the current Zotero state
- Import from preview cache or history using current form settings
- Avoid accidental duplicate records by DOI/PMID matching
- Link existing Zotero items into a target collection instead of recreating them
- Block ambiguous same-name collection paths

## Quick Start

### 1. Create a virtual environment

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure credentials

Zotero:

- Create an API key: <https://www.zotero.org/settings/keys>
- Use your numeric Zotero user or group ID, not your email address

Optional APIs:

- NCBI E-utilities key/email for higher request limits
- OpenAlex email/key for citation and journal metrics

You can either set environment variables, place them in a local `.env`, or enter values in the web UI.

## Environment Variables

Example values:

```powershell
$env:ZOTERO_USER_ID="1234567"
$env:ZOTERO_API_KEY="your_zotero_api_key"
$env:ZOTERO_LIBRARY_TYPE="users"
$env:ZOTERO_LIBRARY_ID="1234567"
$env:ZOTERO_COLLECTION_PATH="ProjectA/Review"

$env:NCBI_EMAIL="you@example.com"
$env:NCBI_API_KEY="your_ncbi_api_key"

$env:OPENALEX_EMAIL="you@example.com"
$env:OPENALEX_API_KEY="your_openalex_api_key"
```

See [`.env.example`](./.env.example) for a full template.

Configuration priority in the web app is:

1. `.paperbot_streamlit_settings.json`
2. system environment variables / local `.env`
3. built-in defaults

## CLI Usage

Preview without writing to Zotero:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --max-results 20 --dry-run
```

Import into a personal library:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --max-results 20
```

Import into a group library:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --library-type groups --library-id 123456
```

Import into a collection path:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --collection-path "ProjectA/Review/2026Q2"
```

Use separate search and rerank controls:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --pubmed-sort relevance --secondary-sort citation_count_desc
```

## Web App

Run:

```powershell
streamlit run .\app_streamlit.py
```

Then open the local URL shown in the terminal, usually `http://localhost:8501`.

If a local `.env` exists, the app now reads it automatically at startup.

### Recommended workflow

1. Enter a PubMed query
2. Set result count and Zotero target
3. Load existing collections when needed
4. Run preview first
5. Review `Status` and `Action`
6. Re-check status if library, collection, or duplicate settings changed
7. Import only the rows you want

### Status and action model

- `new` + `create new item`: record is not in Zotero and can be created
- `duplicate_existing` + `no change`: record already exists in the relevant scope
- `existing_add_to_collection` + `add existing to target collection`: record exists in Zotero but is not yet in the target collection
- `duplicate_incoming` + `no change`: duplicate inside the current candidate batch

## Duplicate and Collection Behavior

- Duplicate matching uses DOI and PMID
- You can evaluate duplicates against the whole library or only the target collection
- Preview cache and history imports are re-evaluated against the current form settings before import
- Ambiguous collection paths are blocked if Zotero contains duplicate same-name collections under the same parent
- Collection path separator is `/`

## Project Files

- `pubmed_to_zotero.py`: CLI and core import logic
- `app_streamlit.py`: Streamlit UI
- `test_pubmed_to_zotero.py`: unit tests
- `requirements.txt`: dependencies

Local-only files are intentionally ignored:

- `.paperbot_streamlit_settings.json`
- `.paperbot_history.json`
- local virtual environments
- `.env`

## Privacy

This repository is suitable for open source, but your personal data should stay local.

Do not commit:

- API keys
- `.env`
- local Zotero settings/history
- personal research queries if you want them private

## Development

Run a quick syntax check:

```powershell
python -m py_compile app_streamlit.py pubmed_to_zotero.py test_pubmed_to_zotero.py
```

Run tests:

```powershell
python -m unittest -v
```

## License

This project is released under the MIT License. See [LICENSE](./LICENSE).
