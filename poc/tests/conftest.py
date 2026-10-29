"""Shared pytest fixtures.

Mocking rule (see memory/workflow.md): mock everything external —
syft, grype, Docker socket, Telegram.

This conftest also establishes BEACONRY_MOCK + DATA_DIR + sys.path tweaks that
hub.py and demo.py need to be importable from tests. By doing it here, test
modules can `import hub` or `from poc import demo` without each setting env.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("BEACONRY_MOCK", "1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "mock-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")
os.environ.setdefault("MIN_CVSS", "0")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="beaconry-pytest-"))

_HUB_DIR = Path(__file__).resolve().parents[1] / "hub"
if str(_HUB_DIR) not in sys.path:
    sys.path.insert(0, str(_HUB_DIR))
