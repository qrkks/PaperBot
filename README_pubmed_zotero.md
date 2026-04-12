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

Optional for OpenAlex (secondary metrics/sort):

```powershell
$env:OPENALEX_EMAIL="you@example.com"
$env:OPENALEX_API_KEY="your_openalex_api_key"
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

Use separated sort controls:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --pubmed-sort relevance --secondary-sort citation_count_desc
```

Skip duplicates already in Zotero (default behavior) and disable it only when needed:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy"
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --no-skip-duplicates
```

Do not write metrics into Zotero `extra`:

```powershell
python .\pubmed_to_zotero.py --query "glioblastoma AND immunotherapy" --secondary-sort journal_metric_desc --no-attach-metrics-to-extra
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
4. Select `PubMed API sort` and `Secondary sort (local rerank)` independently.
5. Optional: enable `Attach citation/journal metrics to Zotero extra`.
6. Optional: enable `Skip duplicates already in Zotero (DOI/PMID)`, then choose duplicate scope:
   - `Whole library`
   - `Target collection only`
7. Enable `Remember credentials locally` to auto-fill next time.
8. Optional: click `Load existing collections` and pick a full path from dropdown.
9. Or type a new collection path manually, for example `ProjectA/Review/2026Q2`.
10. Keep `Preview only` enabled first.
11. After preview, you can click `Import previewed records now` to import without re-querying PubMed/OpenAlex.
12. Uncheck preview and run import if you prefer a full fresh run.
13. Preview now shows all records with dedup status (`new`, `duplicate_existing`, `duplicate_incoming`), while only `new` records are imported.
14. In Preview Cache, the first column is selectable. Use `Select non-duplicates`, `Select all`, `Select none` for batch selection.
15. The app now stores lightweight history for preview/import runs. Use the `History` panel to inspect old runs and reload their config.
16. History entries can now select rows and import the saved article list using the current form settings.
17. Preview Cache and History now share the same selectable table UI, and `Status` is color-highlighted.
18. Preview Cache import and History import both re-evaluate duplicates using the current page settings before writing to Zotero.
19. If Preview Cache or History statuses are stale relative to the current form, the app asks you to refresh/review them before import.
20. `Re-check duplicates/actions` shows the current evaluation target and reports how many rows changed after re-checking Zotero.
21. Selected collection path is now persisted in the form and reset automatically when you switch to a different Zotero library.
22. If Zotero contains duplicate same-name collections under the same parent, the app flags those paths as ambiguous and blocks imports to them until you clean them up.
23. The main action button now changes with mode: `Run preview` in preview mode, `Run and import` in write mode.
24. Preview Cache and History buttons use shorter labels consistently: `Import selected`, `Re-check status`, and `Load config`.

## Notes

- PubMed results are imported in batches of 50 for Zotero API stability.
- The importer writes `PMID` into `extra` and `url`.
- Collection path separator is `/`.
- If both collection key and collection path are provided, collection key wins.
- PubMed API sort supports: `relevance`, `pub_date`, `Author`, `JournalName`.
- Secondary sort supports: `none`, `citation_count_desc`, `journal_metric_desc`, `hybrid_score_desc`.
- Citation/journal metrics are fetched from OpenAlex and stored as snapshot metadata in Zotero `extra` by default.
- Duplicate check defaults to ON and uses DOI/PMID against existing items in the target library.
- When an item already exists in the library but not in the target collection, the app links the existing item into the target collection instead of creating a duplicate.
- Streamlit saved settings file: `.paperbot_streamlit_settings.json` (project root).
- Streamlit history file: `.paperbot_history.json` (project root).
