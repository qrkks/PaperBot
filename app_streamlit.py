"""Backward-compatible Streamlit entrypoint for PaperBot."""

from __future__ import annotations

from pathlib import Path
import runpy

WEB_APP = Path(__file__).resolve().parent / "paperbot" / "web.py"

runpy.run_path(str(WEB_APP), run_name="__main__")
