#!/usr/bin/env python3
"""Backward-compatible CLI wrapper for PaperBot."""

from __future__ import annotations

from paperbot.core import *  # noqa: F401,F403
from paperbot.core import main


if __name__ == "__main__":
    raise SystemExit(main())
