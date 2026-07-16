"""테스트 격리 보조 — src.db.connection 의 DATA_DIR/DATABASE_PATH 바인딩 보호.

배경: tests/test_item_code_master.py · test_match_item_codes.py 의 _new_conn(tmp_path)
헬퍼가 src.db.connection 모듈 전역(DATA_DIR/DATABASE_PATH)을 tmp_path 로 바꾼 뒤
복구하지 않는다. 그 뒤로 get_connection() 을 쓰는 테스트(test_recipe_management 등)가
그 tmp_path DB 에 접근하게 되어, P3(item-code)부터는 마스터가 비어있지 않은 그 DB
때문에 미지 자재(원료A/원료B)가 차단(400)되는 회귀로 이어진다.

src/db/connection.py 는 `from ..config import DATA_DIR, DATABASE_PATH` 로 값을
복사해 두므로, importlib.reload(cfg) 만으로는 connection 쪽 바인딩이 갱신되지 않는다.
따라서 각 테스트 후 connection 의 바인딩을 config 기본값으로 되돌려 격리를 보장한다.
기존 테스트 코드(원문)는 건드리지 않는다.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_db_path_bindings():
    """각 테스트 전후로 src.db.connection 의 DATA_DIR/DATABASE_PATH 스냅샷/복구.

    테스트가 이 전역들을 바꾸면(직접 또는 _new_conn 경유) 다음 테스트에 영향을 주므로,
    원래 값으로 되돌린다. 단 테스트 도중에는 자유롭게 바꿀 수 있게 한다.
    """
    import src.db.connection as dbconn

    yield
    # 복구: config 의 기본(루트 conftest 가 잡은 IRMS_DATA_DIR)으로 되돌린다.
    # config 를 reload 하면 환경변수 기반 값으로 갱신되므로 일관성이 보장된다.
    import importlib

    import src.config as cfg

    importlib.reload(cfg)
    dbconn.DATA_DIR = cfg.DATA_DIR
    dbconn.DATABASE_PATH = cfg.DATABASE_PATH
