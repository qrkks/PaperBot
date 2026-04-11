# PubMed to Zotero

This project provides two ways to import papers from PubMed into Zotero:

1. CLI script: `pubmed_to_zotero.py`
2. Web UI: `app_streamlit.py`

## Install

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Required config

Create a Zotero API key:

- <https://www.zotero.org/settings/keys>

Find your Zotero user id:

- <https://www.zotero.org/settings/security>
- Use the numeric user ID only (for example `1234567`), not your email address.

Set environment variables (recommended):

```powershell
$env:ZOTERO_USER_ID="your_user_id"
$env:ZOTERO_API_KEY="your_api_key"
```

Optional for NCBI:

```powershell
$env:NCBI_EMAIL="you@example.com"
$env:NCBI_API_KEY="your_ncbi_api_key"
```

## CLI usage

Preview only:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --max-results 20 --dry-run
```

Import into personal library:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --max-results 20
```

Import into group library:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --max-results 20 --library-type groups --library-id 123456
```

Import into a collection by full path (auto-create missing path by default):

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --max-results 20 --collection-path "ProjectA/Review/2026Q2"
```

Import into a collection by collection key (highest priority):

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --max-results 20 --collection-key "ABCD1234"
```

Disable auto-create for a missing collection path:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --max-results 20 --collection-path "ProjectA/Review" --no-create-collection-if-missing
```

## Web UI usage (input form)

Run:

```powershell
streamlit run .\app_streamlit.py
```

Then open the local URL shown in terminal (usually `http://localhost:8501`).

In the UI:

1. Enter PubMed query.
2. Set max results.
3. Fill Zotero user id and API key.
4. Enable `Remember credentials locally` to auto-fill next time.
5. Optional: click `Load existing collections` and pick a full path from dropdown.
6. Or type a new collection path manually, for example `ProjectA/Review/2026Q2`.
7. Keep `Preview only` enabled first.
8. Uncheck preview and run import.

## Notes

- PubMed results are imported in batches of 50 for Zotero API stability.
- The importer writes `PMID` into `extra` and `url`.
- Collection path separator is `/`.
- If both collection key and collection path are provided, collection key wins.
- Streamlit saved settings file: `.paperbot_streamlit_settings.json` (project root).
