"""Pytest configuration for local test collection."""

from __future__ import annotations

import sys
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
TEST_DATA_DIR = PROJECT_ROOT / ".tmp-tests" / "pytest-data"

os.environ.setdefault("IRMS_DATA_DIR", str(TEST_DATA_DIR))
os.environ.setdefault("IRMS_ENV", "test")
os.environ.setdefault("IRMS_REQUIRE_SESSION_SECRET", "false")
os.environ.setdefault("IRMS_SESSION_SECRET", "0" * 64)
os.environ.setdefault("IRMS_SEED_DEMO_DATA", "false")
os.environ.setdefault("IRMS_REQUIRE_TRAY_API_TOKEN", "false")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
