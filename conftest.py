"""Pytest configuration for local test collection."""

from __future__ import annotations

import sys
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
TEST_DATA_DIR = PROJECT_ROOT / ".tmp-tests" / "pytest-data"

# 무조건 덮어쓴다(setdefault 금지) — 셸에 IRMS_DATA_DIR/IRMS_ENV 가 이미 export 되어
# 있으면 테스트가 개발/운영 DB(data/)를 오염시킬 수 있기 때문.
os.environ["IRMS_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["IRMS_ENV"] = "test"
os.environ["IRMS_REQUIRE_SESSION_SECRET"] = "false"
os.environ["IRMS_SESSION_SECRET"] = "0" * 64
os.environ["IRMS_SEED_DEMO_DATA"] = "false"
os.environ["IRMS_REQUIRE_TRAY_API_TOKEN"] = "false"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
