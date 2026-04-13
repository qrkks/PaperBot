"""Backward-compatible Streamlit entrypoint for PaperBot."""

from __future__ import annotations

from pathlib import Path
import runpy
import sys

APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

WEB_APP = APP_ROOT / "paperbot" / "web.py"

runpy.run_path(str(WEB_APP), run_name="__main__")
