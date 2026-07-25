"""Pytest configuration for local test collection."""

from __future__ import annotations

import shutil
import sys
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
TEST_DATA_DIR = PROJECT_ROOT / ".tmp-tests" / "pytest-data"

# 실행마다 테스트 DB 를 새로 만든다. 예전에는 이 디렉터리가 누적돼(레시피 973건·이용자
# 356명·감사로그 3849행까지 불어남) 실행할 때마다 다른 테스트가 401 로 깨졌다 —
# 전부 위양성이었고(새 DB 로는 전건 통과), 그 소음 때문에 진짜 회귀를 구별할 수 없었다.
# 공용 admin 계정의 단일 세션 토큰이 누적된 데이터와 얽히는 것이 원인으로 추정된다.
# 산출물은 gitignore 된 재생성 가능 디렉터리라 지워도 잃을 것이 없다.
shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)

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
