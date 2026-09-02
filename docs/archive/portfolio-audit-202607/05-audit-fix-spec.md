# IRMS(BRM) 감사 발견 수정 실행 지시서 (05-audit-fix-spec)

> 작성: 2026-07-12 · 근거: [03-audit.md](./03-audit.md) 중 F-1(Critical) · F-2(High) · F-3(High) 3건 — **다른 발견은 이 문서의 스코프 밖**
> 대상 코드: `C:\X\IRMS` (FastAPI + SQLite WAL + Jinja2, 규제성 DHR 데이터, 운영은 serve.py 자동 git pull)
> 이 문서는 코딩 에이전트가 추가 판단 없이 그대로 실행할 수 있는 수준을 목표로 한다. 라인 번호는 2026-07-12 시점 파일 기준.

## 공통 전제·주의

- **운영 DB(`data/irms.db`)는 규제성 DHR 데이터다.** 개발 중에는 절대 열지 않는다. 운영 DB 접근은 §1.6의 배포 전 사전 점검(읽기 전용 `mode=ro` URI) 1회로 한정한다.
- 테스트: `python -m pytest tests -v` (현재 313개 — 전부 green 유지 + 신규 추가분 green). 테스트는 `conftest.py`가 `IRMS_DATA_DIR`을 `.tmp-tests/`로 강제하므로 운영 데이터와 격리된다.
- 마이그레이션은 `src/db/migrations.py`의 기존 규율을 따른다: 멱등 실행(`ensure_column`/`CREATE ... IF NOT EXISTS`), 1회성 변환은 `has_migration`/`record_migration` 가드, `init_db()`(create_app 시 호출)의 `with get_connection()` 트랜잭션 안에서 실행되어 **실패 시 전체 롤백 + 서버 기동 실패**(fail-loud).
- 커밋은 건별 분리(F-2 → F-3 → F-1 순 권장, §5). 각 커밋 전 전체 pytest green 확인.

---

## 1. F-1 [Critical] 제품 LOT 채번 경쟁 + UNIQUE 제약 부재

### 1.1 진단 재확인 — 감사와 일치 (확실)

- `src/services/blend_service.py:485-501` `generate_product_lot`: `SELECT ... LIKE base%`로 최대 순번을 읽어 `+1` — read-then-write. 호출부 `create_blend_record`(566행)는 채번→INSERT 사이에 아무 잠금도 잡지 않는다.
- Python `sqlite3` 기본 `isolation_level=""`에서 **SELECT는 트랜잭션을 열지 않고** INSERT 시점에야 DEFERRED 트랜잭션이 시작된다. 요청마다 별도 연결(`db/connection.py:9`, sync 라우트 = FastAPI 스레드풀) + WAL이므로, 두 스레드가 같은 `max_seq`를 읽고 직렬화된 두 INSERT가 모두 같은 LOT을 기록한다.
- `src/db/migrations.py:243-245`의 `idx_blend_records_lot`은 **비유니크** — DB가 중복을 거르지 못한다. `blend_records` 스키마(198-220행)에도 UNIQUE 없음.
- 노출 경로: `POST /api/blend/records`(단건), `POST /api/blend/records/bulk`(다른 동시 단건과 충돌 가능). `GET /blend/next-lot`(93-101행)은 읽기 전용 미리보기라 무해.
- 영향 재확인: `product_lot`은 점도 연계(`viscosity_readings.lot_no` 매칭 + `migrations.py:270-281` 백필이 `ORDER BY br.id LIMIT 1`로 **먼저 만든 기록에 몰아줌**), DHR PDF/Excel 파일명, 역추적의 1차 키 — 중복 시 두 배치가 비가역적으로 뒤섞인다. 감사 판단 그대로.

### 1.2 수정 대안 비교와 선택

두 계층을 **모두** 적용한다(감사 권고 (1)+(2)+(3) 통합):

| 계층 | 대안 | 판정 |
|---|---|---|
| 예방 | **(a) `BEGIN IMMEDIATE`로 채번+INSERT를 단일 쓰기 트랜잭션으로 직렬화** | **채택** — RESERVED 락을 선획득해 두 번째 쓰기 트랜잭션이 `busy_timeout`(5초, connection.py:12) 동안 대기 → 채번 SELECT가 항상 직전 커밋을 본다. 단일 프로세스 스레드풀·교차 프로세스 모두 커버 |
| 예방(대안) | 채번을 `INSERT ... SELECT max+1` 단문으로 | 기각 — LOT suffix가 문자열이라 max 계산에 파싱이 필요(`suffix.isdigit()` 필터)해 순수 SQL로 옮기면 비정규 레거시 LOT에서 오동작 위험. BEGIN IMMEDIATE가 더 단순·전면적 |
| 봉인 | **(b) `product_lot` UNIQUE 인덱스 마이그레이션 + 위반 시 재채번 재시도** | **채택** — (a)를 우회하는 미래의 다른 쓰기 경로·수동 조작까지 DB가 최종 방어. 기존 중복은 마이그레이션에서 감사 로그를 남기며 결정적으로 재채번(§1.3(1)) |
| 봉인(대안) | 중복 발견 시 마이그레이션 실패로 세워두고 수동 정리 요구 | 기각 — 운영 서버가 기동 불능이 되는 기간이 생기고, 정리 절차가 사람마다 달라짐. 대신 **결정적 자동 재채번 + audit_logs 전량 기록 + 배포 전 사전 점검으로 규모를 먼저 파악**하는 쪽이 규제 추적성·가용성 모두에서 낫다 |

**재채번 정책(중복 발견 시)**: 각 중복 그룹에서 **최소 id(최초 저장분)의 LOT을 보존**하고 나머지 행만 재채번한다. 이미 발행된 DHR 문서 대부분은 먼저 저장된 기록의 것이므로 문서-DB 불일치를 최소화한다. 재채번 값은 기존 채번 규칙과 동일한 "같은 base의 다음 빈 순번"(비정규 레거시 LOT은 `-2` 접미). 연계 점도는 `blend_record_id`로 물린 행만 `lot_no`를 함께 갱신하고, `blend_record_id`가 NULL인 판독은 보존된 대표 LOT 소속으로 남긴다(사전 점검에서 책임자 확인 대상). 모든 변경은 `audit_logs(action='product_lot_dedup')`에 old→new로 남긴다.

### 1.3 파일별 변경 내용

#### (1) `src/db/migrations.py`

**(1-a)** 243-245행의 비유니크 인덱스 생성을 **삭제**:

```python
    # 삭제할 3줄:
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_blend_records_lot ON blend_records(product_lot)"
    )
```

(남겨두면 매 기동마다 IF NOT EXISTS가 — 아래 마이그레이션이 DROP한 뒤 — 비유니크 인덱스를 재생성한다. 반드시 코드에서 제거하고 기존 DB의 인덱스는 마이그레이션 안에서 DROP한다.)

**(1-b)** `apply_schema_migrations` 끝(392행 `drop_orphan_chat_tables` 블록 뒤)에 추가:

```python
    # 감사 F-1: product_lot 전역 유일 봉인 — 채번 경쟁(read-then-write)으로 생겼을 수
    # 있는 기존 중복을 정리(재채번 + audit_logs 기록)한 뒤 UNIQUE 인덱스를 만든다.
    # 실패(예: 정리 후에도 위반)하면 init_db 트랜잭션 전체가 롤백되고 서버 기동이
    # 실패한다 — 조용히 넘어가지 않는다(fail-loud).
    if not has_migration(connection, "blend_records_product_lot_unique"):
        dedup_product_lots(connection)
        connection.execute("DROP INDEX IF EXISTS idx_blend_records_lot")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_blend_records_lot_unique "
            "ON blend_records(product_lot)"
        )
        record_migration(connection, "blend_records_product_lot_unique")
```

**(1-c)** 모듈 수준 함수 추가(파일 끝, `standardize_recipe_units_to_grams` 아래 — 테스트에서 직접 임포트한다):

```python
def dedup_product_lots(connection: sqlite3.Connection) -> list[dict]:
    """중복 product_lot 정리 (감사 F-1 마이그레이션 보조).

    각 중복 그룹에서 최소 id(최초 저장분)의 LOT 은 보존하고, 나머지 행을 재채번한다.
    - 정규형({product_name}{YYMMDD}{seq}) LOT: 같은 base 의 다음 빈 순번(2자리 zero-pad)
    - 비정규(레거시) LOT: 원 라벨 뒤에 "-2", "-3" … 접미(라벨 식별성 보존)
    - 연계 점도: blend_record_id 로 물린 viscosity_readings.lot_no 를 함께 갱신
    - 모든 변경을 audit_logs(action='product_lot_dedup')에 old→new 로 기록
    반환: [{"id", "old", "new"}, ...]
    """
    from .audit import write_audit_log  # 같은 패키지 — 순환 없음(audit 은 queries/time_utils 만 의존)

    dup_rows = connection.execute(
        """
        SELECT id, product_lot, product_name, work_date FROM blend_records
        WHERE product_lot IN (
            SELECT product_lot FROM blend_records
            GROUP BY product_lot HAVING COUNT(*) > 1
        )
        ORDER BY product_lot, id
        """
    ).fetchall()
    if not dup_rows:
        return []
    taken = {
        str(r["product_lot"])
        for r in connection.execute("SELECT product_lot FROM blend_records").fetchall()
    }
    keep_seen: set[str] = set()
    changes: list[dict] = []
    for row in dup_rows:
        old = str(row["product_lot"])
        if old not in keep_seen:
            keep_seen.add(old)  # 그룹 대표(최소 id) — 원 LOT 유지
            continue
        # base 는 generate_product_lot 과 동일 규칙으로 재계산 (제품명 + YYMMDD)
        digits = "".join(ch for ch in str(row["work_date"]) if ch.isdigit())
        yymmdd = digits[2:8] if len(digits) >= 8 else digits[-6:]
        base = f"{str(row['product_name']).strip()}{yymmdd}"
        suffix = old[len(base):] if old.startswith(base) else None
        if suffix is not None and suffix.isdigit():
            seq = int(suffix)
            while True:
                seq += 1
                new = f"{base}{seq:02d}"
                if new not in taken:
                    break
        else:
            n = 1
            while True:
                n += 1
                new = f"{old}-{n}"
                if new not in taken:
                    break
        taken.add(new)
        connection.execute(
            "UPDATE blend_records SET product_lot = ?, updated_at = ? WHERE id = ?",
            (new, utc_now_text(), int(row["id"])),
        )
        connection.execute(
            "UPDATE viscosity_readings SET lot_no = ? WHERE blend_record_id = ? AND lot_no = ?",
            (new, int(row["id"]), old),
        )
        write_audit_log(
            connection,
            action="product_lot_dedup",
            target_type="blend_record",
            target_id=int(row["id"]),
            target_label=new,
            details={"old": old, "new": new},
        )
        changes.append({"id": int(row["id"]), "old": old, "new": new})
    return changes
```

(실행 순서 안전성: `audit_logs`는 `schema.py` executescript에서, `viscosity_readings`는 `apply_schema_migrations` 상단(163-176행)에서 이 블록보다 먼저 생성된다.)

#### (2) `src/services/blend_service.py` — `create_blend_record` (541-609행)

566-584행(채번 + 헤더 INSERT)을 다음으로 교체. 시그니처·details INSERT(586행 이하)는 그대로:

```python
    # 감사 F-1: 채번+INSERT 원자화. 쓰기 락을 선획득(BEGIN IMMEDIATE)해 동시 요청의
    # 채번을 직렬화한다(WAL 에서 리더는 라이터를 막지 않으므로 명시 락이 필요).
    # 이미 트랜잭션 안이면(create_bulk 루프의 2번째 이후 호출 등) 그대로 진행.
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    # UNIQUE(product_lot) 위반 시 재채번 재시도 — BEGIN IMMEDIATE 하에서는 사실상
    # 발생하지 않지만(단일 라이터), 교차 프로세스 등 방어적 재시도를 둔다.
    last_error: sqlite3.IntegrityError | None = None
    cur = None
    for _attempt in range(3):
        product_lot = generate_product_lot(connection, product_name, work_date)
        try:
            cur = connection.execute(
                """
                INSERT INTO blend_records
                    (product_lot, recipe_id, product_name, ink_name, position, worker,
                     work_date, work_time, total_amount, scale, status, note,
                     worker_sign, reactor, manual_entry, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_lot, recipe_id, product_name.strip(), ink_name, position, worker.strip(),
                    work_date, work_time, float(total_amount), scale,
                    (note or "").strip() or None, worker_sign,
                    int(reactor) if reactor is not None else None,
                    1 if manual_entry else 0,
                    created_by, created_at, created_at,
                ),
            )
            break
        except sqlite3.IntegrityError as exc:
            if "product_lot" not in str(exc):
                raise
            last_error = exc
    else:
        raise last_error  # 3회 모두 위반 — 비정상 상황을 그대로 드러낸다(500)
    record_id = int(cur.lastrowid)
```

주의사항(구현 시 지킬 것):
- SQLite에서 실패한 INSERT는 문장 단위로만 중단되고 열려 있는 트랜잭션은 유지되므로 같은 트랜잭션 안에서의 재시도가 유효하다.
- 라우트(`blend_routes.py` `blend_create`)의 후속 `get_blend_record`·`write_audit_log`·`connection.commit()`은 같은 트랜잭션에 포함된다 — **변경 불필요**. 커밋까지 쓰기 락이 유지되는 구간은 수 ms.
- `create_bulk`(683-738행)는 첫 `create_blend_record` 호출이 IMMEDIATE 트랜잭션을 열고 이후 호출은 `in_transaction` 가드로 건너뛴다 — **변경 불필요**.
- `generate_product_lot` 자체와 `GET /blend/next-lot`은 **변경 불필요**(미리보기 값이 저장 시 달라질 수 있는 것은 기존과 동일한 표시 시맨틱).

#### (3) 신규 테스트 파일 `tests/test_blend_lot_unique.py`

기존 관례(test_blend.py의 in-memory 스키마 헬퍼, test_route_coverage.py의 앱 레벨 검증)를 따른다:

```python
"""product_lot 채번 경쟁 + UNIQUE 봉인 (감사 F-1) 회귀 테스트."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from src.db.migrations import dedup_product_lots
from src.services import blend_service as bs


_SCHEMA = """
CREATE TABLE blend_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_lot TEXT NOT NULL, recipe_id INTEGER, product_name TEXT NOT NULL,
    ink_name TEXT, position TEXT, worker TEXT NOT NULL, work_date TEXT NOT NULL,
    work_time TEXT, total_amount REAL NOT NULL, scale TEXT,
    status TEXT NOT NULL DEFAULT 'completed', note TEXT, reactor INTEGER,
    manual_entry INTEGER NOT NULL DEFAULT 0,
    worker_sign TEXT, created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT
);
CREATE TABLE blend_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blend_record_id INTEGER NOT NULL, material_id INTEGER,
    material_code TEXT, material_name TEXT NOT NULL, material_lot TEXT,
    ratio REAL, theory_amount REAL, actual_amount REAL,
    sequence_order INTEGER NOT NULL DEFAULT 0,
    manual_entry INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE viscosity_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, lot_no TEXT,
    viscosity REAL, measured_date TEXT, created_at TEXT, blend_record_id INTEGER
);
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
    actor_user_id INTEGER, actor_username TEXT, actor_display_name TEXT,
    actor_access_level TEXT, target_type TEXT, target_id TEXT, target_label TEXT,
    details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_blend_records_lot_unique ON blend_records(product_lot);
"""


def _connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _file_db(path) -> sqlite3.Connection:
    conn = _connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    return conn


def _create(conn, *, product="잉크A", date="2026-07-12"):
    return bs.create_blend_record(
        conn, recipe_id=None, product_name=product, ink_name=None, position=None,
        worker="QA", work_date=date, work_time=None, total_amount=100.0, scale=None,
        note=None, details=[{"material_name": "A", "ratio": 100,
                             "theory_amount": 100, "actual_amount": 100}],
        created_by="QA", created_at="2026-07-12T00:00:00Z",
    )


def test_concurrent_create_yields_distinct_lots(tmp_path):
    """[red 재현] 수정 전: 두 스레드가 같은 max_seq 를 읽어 동일 LOT 2행이 저장됐다.
    수정 후: BEGIN IMMEDIATE 직렬화로 항상 서로 다른 순번이 발번된다."""
    db = tmp_path / "race.db"
    _file_db(db).close()

    orig = bs.generate_product_lot

    def slow_generate(connection, product_name, work_date):
        lot = orig(connection, product_name, work_date)
        time.sleep(0.2)  # 채번→INSERT 사이 경쟁 창을 벌린다
        return lot

    bs.generate_product_lot, results, errors = slow_generate, [], []

    def worker():
        conn = _connect(db)
        try:
            rid = _create(conn)
            conn.commit()
            results.append(rid)
        except Exception as exc:  # noqa: BLE001 — 수정 전 거동 관찰용
            errors.append(exc)
        finally:
            conn.close()

    try:
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
    finally:
        bs.generate_product_lot = orig

    assert not errors, errors
    check = _connect(db)
    lots = [r["product_lot"] for r in check.execute(
        "SELECT product_lot FROM blend_records ORDER BY id")]
    check.close()
    assert len(lots) == 2 and len(set(lots)) == 2, lots  # 수정 전: 동일 LOT 2개 → red


def test_create_retries_on_unique_violation(tmp_path, monkeypatch):
    """UNIQUE 위반 시 재채번 재시도로 저장이 성공한다."""
    db = tmp_path / "retry.db"
    conn = _file_db(db)
    first = _create(conn)          # 잉크A26071201
    conn.commit()
    taken = conn.execute("SELECT product_lot FROM blend_records WHERE id=?", (first,)).fetchone()[0]

    orig, calls = bs.generate_product_lot, {"n": 0}

    def dup_first(connection, product_name, work_date):
        calls["n"] += 1
        return taken if calls["n"] == 1 else orig(connection, product_name, work_date)

    monkeypatch.setattr(bs, "generate_product_lot", dup_first)
    rid = _create(conn)
    conn.commit()
    lots = {r["product_lot"] for r in conn.execute("SELECT product_lot FROM blend_records")}
    conn.close()
    assert calls["n"] >= 2 and len(lots) == 2


def test_dedup_product_lots_renumbers_and_relinks(tmp_path):
    """마이그레이션 보조: 중복 그룹의 최소 id 보존, 나머지 재채번 + 점도 lot_no 동기 + 감사 기록."""
    conn = _file_db(tmp_path / "dedup.db")
    conn.execute("DROP INDEX idx_blend_records_lot_unique")  # 중복이 있던 '수정 전' DB 재연
    ins = ("INSERT INTO blend_records (product_lot, product_name, worker, work_date, "
           "total_amount, created_at) VALUES (?, ?, 'QA', ?, 100, 't')")
    conn.execute(ins, ("잉크A26071201", "잉크A", "2026-07-12"))                 # id=1 — 보존
    conn.execute(ins, ("잉크A26071201", "잉크A", "2026-07-12"))                 # id=2 — 재채번 대상
    conn.execute(ins, ("잉크A26071202", "잉크A", "2026-07-12"))                 # 02는 이미 점유
    conn.execute(ins, ("LEGACY-라벨", "잉크B", "2026-07-12"))                    # id=4
    conn.execute(ins, ("LEGACY-라벨", "잉크B", "2026-07-12"))                    # id=5 — 비정규 재채번
    conn.execute("INSERT INTO viscosity_readings (product_id, lot_no, viscosity, created_at, "
                 "blend_record_id) VALUES (1, '잉크A26071201', 50, 't', 2)")     # id=2 에 물린 점도
    conn.execute("INSERT INTO viscosity_readings (product_id, lot_no, viscosity, created_at, "
                 "blend_record_id) VALUES (1, '잉크A26071201', 51, 't', NULL)")  # 미연계 — 보존
    changes = dedup_product_lots(conn)

    rows = {int(r["id"]): r["product_lot"] for r in conn.execute(
        "SELECT id, product_lot FROM blend_records")}
    assert rows[1] == "잉크A26071201"            # 최소 id 보존
    assert rows[2] == "잉크A26071203"            # 02 점유 → 다음 빈 순번 03
    assert rows[5] == "LEGACY-라벨-2"            # 비정규 → 접미
    assert {c["id"] for c in changes} == {2, 5}
    # 물린 점도만 lot_no 동기, 미연계 판독은 대표 LOT 소속으로 보존
    visc = [r["lot_no"] for r in conn.execute(
        "SELECT lot_no FROM viscosity_readings ORDER BY id")]
    assert visc == ["잉크A26071203", "잉크A26071201"]
    # 감사 기록
    audits = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_logs WHERE action='product_lot_dedup'").fetchone()["n"]
    assert audits == 2
    # 정리 후 UNIQUE 봉인이 성립
    conn.execute("CREATE UNIQUE INDEX idx_blend_records_lot_unique ON blend_records(product_lot)")
    conn.close()


def test_app_startup_seals_unique_index():
    """create_app → init_db 마이그레이션 후 product_lot 에 유니크 인덱스가 존재한다."""
    from fastapi.testclient import TestClient
    from src.main import create_app

    TestClient(create_app())  # init_db 실행
    from src.db import get_connection
    with get_connection() as conn:
        idx = {r["name"]: r["unique"] for r in conn.execute(
            "PRAGMA index_list(blend_records)").fetchall()}
    assert idx.get("idx_blend_records_lot_unique") == 1
    assert "idx_blend_records_lot" not in idx
```

red/green: `test_concurrent_create_yields_distinct_lots`는 수정 전 코드(비유니크 + 락 없음)에서 동일 LOT 2행이 저장되어 **red**, 수정 후 green. 나머지는 신규 기능 고정 테스트.

### 1.4 수용 기준

1. `python -m pytest tests -v` 전부 green (기존 313 + 신규 5).
2. 테스트 DB(`.tmp-tests`) 기준 `PRAGMA index_list(blend_records)`에 `idx_blend_records_lot_unique(unique=1)` 존재, 구 `idx_blend_records_lot` 부재.
3. `tools/smoke_irms.py --mode development --seed-demo-data` 통과(배합 저장 경로 포함).
4. 배포 후 운영에서: 같은 제품·같은 날 연속 2건 저장 → 순번 `01`, `02` 정상 발번(현장 스모크 1회).

### 1.5 롤백

- **마이그레이션은 되돌리지 않는다.** 재채번 결과는 `audit_logs(action='product_lot_dedup')`의 old→new 기록으로 전량 추적 가능하며, UNIQUE 인덱스는 그대로 두는 것이 안전하다(제거할 이유가 없음).
- 코드 롤백이 필요하면 `git revert`로 (2)의 서비스 변경만 되돌린다 — UNIQUE 인덱스가 남아 있으므로 경쟁 시 중복 대신 500이 나는 "감사 권고 (1)만 적용된 상태"로 후퇴한다(데이터 오염은 계속 차단됨).
- 비상시(유니크 제약이 운영을 막는 예기치 못한 상황) 최후 수단: 서버 중지 → `DROP INDEX idx_blend_records_lot_unique; DELETE FROM schema_migrations WHERE name='blend_records_product_lot_unique';` → 구버전 코드로 기동. 실행 전 반드시 `backups/` 최신 백업 확인. (serve.py가 업데이트 직전 자동 백업을 만들므로 배포 시점 백업은 항상 존재.)

### 1.6 배포 절차 (운영 DB 사전 점검 포함 — 이 건만 특별 절차)

1. **배포 전, 운영 PC에서 읽기 전용 사전 점검** (서버 가동 중이어도 무해 — `mode=ro`):

   ```powershell
   python -c "import sqlite3,json; con=sqlite3.connect(r'file:data/irms.db?mode=ro',uri=True); con.row_factory=sqlite3.Row; d=[dict(r) for r in con.execute('SELECT product_lot, COUNT(*) AS n, GROUP_CONCAT(id) AS ids FROM blend_records GROUP BY product_lot HAVING n>1 ORDER BY product_lot')]; a=[dict(r) for r in con.execute('SELECT lot_no, COUNT(*) AS n FROM viscosity_readings WHERE blend_record_id IS NULL AND lot_no IN (SELECT product_lot FROM blend_records GROUP BY product_lot HAVING COUNT(*)>1) GROUP BY lot_no')]; print(json.dumps({'dup_lots': d, 'unlinked_visc_on_dup': a}, ensure_ascii=False, indent=1))"
   ```

2. **`dup_lots`가 비어 있으면**: 그대로 배포(git push → serve.py 자동 pull·백업·재기동 → 기동 시 마이그레이션이 UNIQUE 봉인만 수행).
3. **`dup_lots`가 있으면**: 배포 전에 책임자에게 결과를 전달하고 다음을 확정한다 —
   - 각 그룹에서 최소 id가 보존됨을 안내. 재채번될 기록(나머지 id)의 **이미 출력·보관된 DHR 문서는 새 LOT으로 재출력** 필요(`dhr_cache`는 record 변경으로 자동 무효화되므로 재출력만 하면 됨).
   - `unlinked_visc_on_dup`에 항목이 있으면: 해당 점도 판독이 어느 배치 것인지 책임자가 판정 → 재채번된 배치의 것이면 배포 **후** 점도 화면에서 판독의 LOT을 수정(또는 admin이 `blend_record_id` 연계 지정). 마이그레이션은 이 판독을 건드리지 않고 대표 LOT에 남긴다.
   - 확정 후 배포. 기동 로그에 오류가 없는지, `audit_logs`의 `product_lot_dedup` 건수가 사전 점검의 (중복 행 수 − 그룹 수)와 일치하는지 확인:

   ```powershell
   python -c "import sqlite3; con=sqlite3.connect(r'file:data/irms.db?mode=ro',uri=True); print(con.execute(\"SELECT COUNT(*) FROM audit_logs WHERE action='product_lot_dedup'\").fetchone()[0])"
   ```

---

## 2. F-2 [High] 배합 기록 soft 취소 무인증

### 2.1 진단 재확인 — 감사와 일치 (확실)

- `src/routers/blend_routes.py:606-647` `blend_cancel`: `hard=True` 분기만 `has_access_level(current_user, "manager")` 검사(618행). 기본 분기(soft)는 `get_current_user(request, required=False)`로 받은 `current_user`가 **None이어도** 634-636행 `UPDATE ... SET status='canceled'`를 실행한다. 라우터 데코레이터에도 `Depends` 게이트 없음(PUT:418, approve:483과 대조).
- UI(`static/js/status.js:23-25`)는 항상 `hard: 1`만 보낸다 — soft 경로는 **API 직접 호출 전용의 열린 문**. CSRF 토큰은 화면 진입만으로 발급되므로 방어가 안 된다.
- 취소된 기록은 `list_blend_records`(`status != 'canceled'`)·export-all·대시보드에서 사라지고, **복원 엔드포인트가 없다**(전 라우트 검색으로 재확인). 감사 판단 그대로.
- 기존 테스트 영향 확인: `tests/test_route_coverage.py::test_blend_bulk_and_delete_routes`(169행)가 soft 취소를 호출하지만 그 시점에 admin 세션이 살아 있어 **수정 후에도 통과한다** — 기존 테스트 수정 불필요.

### 2.2 수정 대안 비교와 선택

| 대안 | 내용 | 판정 |
|---|---|---|
| A. **soft·hard 모두 책임자(manager) 게이트 + 책임자 전용 복원(restore) 엔드포인트** | hard 분기의 기존 검사(`current_user is None or not has_access_level(…, "manager")` → 403)를 함수 첫머리로 끌어올려 양 분기에 일원화. 복원은 감사 수정 방향("un-cancel 경로를 책임자 전용으로") 반영 | **채택** |
| B. soft는 작업자(blend worker) 세션이면 허용 | 현장에서 자기 오입력을 즉시 취소하는 UX는 좋지만, ① 현행 UI에 soft 취소 버튼 자체가 없고 ② DHR 기록을 목록에서 숨기는 행위의 권한을 세션만 있으면 되는 수준으로 남기는 것은 규제 관점에서 부적절. 필요해지면 "작업자는 자기 기록·당일분만" 같은 정책 설계와 UI가 같이 가야 함 — 후속 과제 | 기각 |
| C. `dependencies=[Depends(require_access_level("manager"))]` 데코레이터로 게이트 | PUT·approve와 같은 관용구라 매력적이나, 함수 본문의 hard 분기 검사와 이중이 됨. 본문 첫머리 일원화(A)가 코드가 더 단순 | 기각(효과 동일, 스타일 차이) — 단, 신규 restore 엔드포인트는 본문 분기가 없으므로 데코레이터 방식 사용 |

취소 사유(reason)는 선택 쿼리 파라미터로 받아 감사 로그 details에 남긴다(스키마 변경 없음). 필수화는 UI(status.js) 변경을 수반하므로 후속 과제로 기록만 한다.

### 2.3 파일별 변경 내용 — `src/routers/blend_routes.py`

**(1)** `blend_cancel`(606-647행) 교체:

```python
    @router.delete("/blend/records/{record_id}")
    def blend_cancel(
        record_id: int,
        request: Request,
        hard: bool = Query(default=False),
        reason: str | None = Query(default=None, max_length=500),
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        # 감사 F-2: soft/hard 모두 책임자 전용. soft 취소도 DHR 기록을 목록·출력·
        # 대시보드에서 숨기는 행위다(종전에는 soft 가 무인증이었다).
        # 인증을 404 조회보다 먼저 — 비인증 호출자에게 기록 존재 여부를 흘리지 않는다.
        # 상태 코드는 기존 hard 분기 관례를 보존: 미로그인·비책임자 모두 403.
        # (get_current_user(required=True)의 401 로 바꾸면 기존
        #  test_blend_hard_delete_requires_manager 의 403 기대가 깨진다.)
        current_user = get_current_user(request, required=False)
        if current_user is None or not has_access_level(current_user, "manager"):
            raise HTTPException(status_code=403, detail="FORBIDDEN")
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        if hard:
            result = record_delete_service.delete_blend_record(connection, record_id)
            if result is None:
                raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
            write_audit_log(
                connection,
                action="blend_record_deleted",
                actor=current_user,
                target_type="blend_record",
                target_id=str(result.record_id),
                target_label=result.product_lot,
                details={"reason": reason} if reason else None,
            )
            connection.commit()
            return {"deleted": result.record_id}

        connection.execute(
            "UPDATE blend_records SET status = 'canceled', updated_at = ? WHERE id = ?",
            (utc_now_text(), record_id),
        )
        write_audit_log(
            connection,
            action="blend_record_cancel",
            actor=current_user,
            target_type="blend_record",
            target_id=str(record_id),
            target_label=record["product_lot"],
            details={"reason": reason} if reason else None,
        )
        connection.commit()
        return {"canceled": record_id}
```

**(2)** 바로 아래에 복원 엔드포인트 추가(취소가 유일한 은닉 경로였고 복구 수단이 없었다 — API 전용, UI 버튼은 후속):

```python
    @router.post(
        "/blend/records/{record_id}/restore",
        dependencies=[Depends(require_access_level("manager"))],
    )
    def blend_restore(
        record_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """소프트 취소된 배합 기록 복원 (책임자 전용, 감사 F-2)."""
        record = blend_service.get_blend_record(connection, record_id)
        if not record:
            raise HTTPException(status_code=404, detail="배합 기록을 찾을 수 없습니다.")
        if record["status"] != "canceled":
            raise HTTPException(status_code=400, detail="취소 상태의 기록이 아닙니다.")
        current_user = get_current_user(request, required=False)
        connection.execute(
            "UPDATE blend_records SET status = 'completed', updated_at = ? WHERE id = ?",
            (utc_now_text(), record_id),
        )
        write_audit_log(
            connection,
            action="blend_record_restore",
            actor=current_user,
            target_type="blend_record",
            target_id=str(record_id),
            target_label=record["product_lot"],
        )
        connection.commit()
        return {"restored": record_id}
```

**(3)** 파일 상단 docstring(9-28행)의 엔드포인트 목록 갱신:

```
    DELETE /blend/records/{id}                기록 취소/삭제(soft/hard 모두 책임자 전용)
    POST   /blend/records/{id}/restore        soft 취소 복원 (책임자 전용)
```

(참고: `write_audit_log`의 `details`는 `details or {}` 처리라 `None` 전달 무해. `get_current_user`·`has_access_level`·`require_access_level`은 이미 임포트되어 있음 — 38행.)

### 2.4 테스트 (수정 전 red → 수정 후 green)

`tests/test_route_coverage.py`에 추가(같은 파일의 `_client`/`_admin_login`/`_csrf` 관용구 재사용 — `test_blend_hard_delete_requires_manager`(233행) 패턴):

```python
def test_blend_soft_cancel_requires_manager():
    """[red 재현] 수정 전: 비로그인 soft 취소가 200 으로 통과해 DHR 기록이 숨겨졌다."""
    client = _client()
    headers = _admin_login(client)
    prod = "SC" + uuid.uuid4().hex[:6].upper()
    worker = "취소작업" + uuid.uuid4().hex[:6]
    client.post("/api/recipes/import", json={"raw_text": f"반제품명\t원료A\n{prod}\t100",
                                             "force": True}, headers=headers)
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/auth/logout", headers=headers)  # 관리 세션 종료

    client.get("/api/blend/records")
    headers = _csrf(client)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    created = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-12",
        "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100,
                     "theory_amount": 100, "actual_amount": 100}],
    }, headers=headers)
    assert created.status_code == 200, created.text
    rid = created.json()["id"]

    # 작업자 세션만(책임자 아님) → 403 (수정 전: 200 — red)
    res = client.request("DELETE", f"/api/blend/records/{rid}", headers=headers)
    assert res.status_code == 403
    from src.db import get_connection
    with get_connection() as conn:
        assert conn.execute("SELECT status FROM blend_records WHERE id=?",
                            (rid,)).fetchone()["status"] == "completed"

    # 책임자 → 200, canceled + 감사 로그
    headers = _admin_login(client)
    res = client.request("DELETE", f"/api/blend/records/{rid}?reason=test", headers=headers)
    assert res.status_code == 200
    with get_connection() as conn:
        assert conn.execute("SELECT status FROM blend_records WHERE id=?",
                            (rid,)).fetchone()["status"] == "canceled"
        assert conn.execute(
            "SELECT 1 FROM audit_logs WHERE action='blend_record_cancel' AND target_id=?",
            (str(rid),)).fetchone() is not None


def test_blend_restore_is_manager_only_and_restores():
    client = _client()
    headers = _admin_login(client)
    prod = "RS" + uuid.uuid4().hex[:6].upper()
    worker = "복원작업" + uuid.uuid4().hex[:6]
    client.post("/api/recipes/import", json={"raw_text": f"반제품명\t원료A\n{prod}\t100",
                                             "force": True}, headers=headers)
    client.post("/api/workers", json={"name": worker}, headers=headers)
    client.post("/api/blend/session/login", json={"worker": worker}, headers=headers)
    rid = client.post("/api/blend/records", json={
        "product_name": prod, "worker": worker, "work_date": "2026-07-12",
        "total_amount": 100,
        "details": [{"material_name": "A", "ratio": 100,
                     "theory_amount": 100, "actual_amount": 100}],
    }, headers=headers).json()["id"]
    assert client.request("DELETE", f"/api/blend/records/{rid}",
                          headers=headers).status_code == 200

    # 비로그인 복원 → 401
    anon = _client()
    anon.get("/api/blend/records")
    assert anon.post(f"/api/blend/records/{rid}/restore",
                     headers=_csrf(anon)).status_code == 401
    # 책임자 복원 → 200 + completed + 감사 로그 + 취소 아닌 기록은 400
    res = client.post(f"/api/blend/records/{rid}/restore", headers=headers)
    assert res.status_code == 200 and res.json()["restored"] == rid
    from src.db import get_connection
    with get_connection() as conn:
        assert conn.execute("SELECT status FROM blend_records WHERE id=?",
                            (rid,)).fetchone()["status"] == "completed"
        assert conn.execute(
            "SELECT 1 FROM audit_logs WHERE action='blend_record_restore' AND target_id=?",
            (str(rid),)).fetchone() is not None
    assert client.post(f"/api/blend/records/{rid}/restore",
                       headers=headers).status_code == 400
```

(예상 상태 코드 근거: `blend_cancel`은 기존 hard 분기 관례를 보존해 미로그인·작업자 세션·비책임자 모두 **403** — 작업자 blend 세션은 `get_current_user`가 보는 세션 키(`mgr_worker_id`/`user_id`)가 아니라 `current_user=None`이 된다. 신규 `blend_restore`는 `require_access_level` 의존성 관용구를 쓰므로 미로그인은 **401**, 로그인+비책임자는 403 — PUT·approve와 동일.)

### 2.5 수용 기준

1. `python -m pytest tests -v` 전부 green — 특히 기존 `test_blend_bulk_and_delete_routes`(admin 세션의 soft 취소 200)와 `test_blend_hard_delete_requires_manager`(작업자 세션 hard 403)가 그대로 통과.
2. 수동 확인(개발 서버): 비로그인 상태에서 `DELETE /api/blend/records/{id}` 직접 호출(curl/브라우저 콘솔) → 401. 책임자 로그인 후 status 화면 삭제(hard) 정상 동작.
3. 감사 로그 화면(admin_users.html)에서 `blend_record_cancel`/`blend_record_restore` 항목에 actor가 기록된다.

### 2.6 롤백

- `git revert <F-2 커밋>` — 스키마 변경 없음, 즉시 원상 복구. 롤백하면 무인증 취소 구멍이 되살아나므로 임시 완화책 없이 재수정을 우선할 것.

---

## 3. F-3 [High] import 미리보기가 자재를 영구 커밋

### 3.1 진단 재확인 — 감사와 일치 (확실)

- `src/services/import_parser.py:182` — 파서가 헤더의 미등록 자재를 만나면 `_auto_register_material`(8-24행)로 그 자리에서 `INSERT INTO materials`. 파싱과 등록이 결합된 구조.
- `src/routers/recipe_import_routes.py:29-33` — preview 라우트가 `with get_connection() as connection:`으로 감싼다. **sqlite3 Connection의 `with`는 정상 종료 시 commit** — 미리보기만으로 자재가 `미분류`로 영구 등록된다.
- 대조: 실제 임포트(35-208행)는 파싱 에러 시 `raise HTTPException`이 `with`를 예외 종료시켜 롤백되고, 성공 시 자재+레시피가 함께 커밋 — 임포트 경로의 시맨틱은 정상. preview만 "항상 정상 종료 → 항상 커밋"이라 문제. 감사 판단 그대로.
- 프런트 확인: preview 응답의 `material_id`는 `static/js/common/mappers.js:82-92` `mapPreview`가 표시용으로만 매핑(등록 요청에 재사용하지 않음). 실제 임포트는 `raw_text`를 서버가 재파싱한다 — **preview에서 INSERT를 폐기해도 흐름이 깨지지 않는다.**

### 3.2 수정 대안 비교와 선택

| 대안 | 내용 | 판정 |
|---|---|---|
| A. **preview 라우트에서 명시적 rollback** | 파싱은 그대로 두고 preview만 트랜잭션을 폐기. 5줄 변경, 파서·임포트 경로 무변경 | **채택** |
| B. INSERT 지연 — 파서에서 자동 등록을 분리해 "미등록 자재 목록"을 반환하고 커밋 단계에서만 등록 | 구조적으로 더 깨끗하지만(감사도 "근본적으로는"으로 제안), `parsed_rows[].items`가 실제 `material_id`를 요구(임포트가 그 id로 `recipe_items`를 바로 INSERT — recipe_import_routes.py:172-179)하므로 placeholder id + 2차 해석 단계가 파서·임포트 라우트·기존 테스트(test_import_parser.py의 자동 등록 검증 포함)에 걸쳐 필요 | 기각(후속) — import_parser 리팩토링 사이클에서 수행 |

**선택 근거**: 임포트 경로가 이미 "예외 → `with` 롤백"이라는 트랜잭션 시맨틱에 의존하는 코드베이스이므로, preview에 명시적 rollback을 두는 것은 **기존 구조와 동일한 관용구**다. 사용자 가시 계약("미리보기는 부작용 없음")을 최소 변경으로 즉시 회복하고, 파서 분리는 별도 사이클로 미룬다. rollback 후에도 preview 응답의 경고("새 원재료를 자동 등록했습니다")와 `material_id`(폐기된 임시 id)는 표시용으로 유효하다 — 실제 등록은 `/recipes/import` 커밋 시 재수행된다.

### 3.3 파일별 변경 내용

#### (1) `src/routers/recipe_import_routes.py` — `import_preview`(29-33행) 교체

```python
    @router.post("/recipes/import/preview")
    def import_preview(body: ImportRequest) -> dict[str, Any]:
        # 감사 F-3: 미리보기는 무부작용이어야 한다. parse_import_text 는 미등록 자재를
        # 그 자리에서 INSERT 하는데(파싱·등록 결합), sqlite3 Connection 을 `with` 로
        # 감싸면 정상 종료 시 commit 되어 미리보기만으로 자재가 영구 등록됐다.
        # → 명시적 rollback 으로 INSERT 를 폐기한다. 실제 등록은 /recipes/import 만.
        #   (응답의 material_id 는 표시용 임시값 — mappers.js mapPreview 참조)
        connection = get_connection()
        try:
            result = parse_import_text(connection, body.raw_text)
        finally:
            connection.rollback()
            connection.close()
        return result
```

#### (2) `src/services/import_parser.py:184` — 경고 문구 시제 정정(선택이지만 권장)

```python
                header_warnings.append({"level": 3, "message": f"새 원재료가 자동 등록됩니다: {header.strip()}", "row": current_row_index})
```

("등록했습니다" → "등록됩니다": preview에서는 아직 등록 전, import에서도 커밋 시점 등록이므로 양쪽 모두 정확해진다. 기존 테스트 `test_import_parser.py:134`는 `"자동 등록" in message` 부분 일치라 **깨지지 않는다.** 문구를 바꾸지 않기로 하면 이 항목만 생략 가능 — 나머지 지시는 독립.)

### 3.4 테스트 (수정 전 red → 수정 후 green)

`tests/test_route_coverage.py`에 추가(같은 파일 관용구 재사용):

```python
def test_import_preview_has_no_side_effect_on_materials():
    """[red 재현] 수정 전: 미리보기만으로 신규 자재가 materials 에 영구 커밋됐다."""
    client = _client()
    headers = _admin_login(client)
    name = "미리보기자재" + uuid.uuid4().hex[:6]
    prod = "PV" + uuid.uuid4().hex[:6].upper()
    raw = f"반제품명\t{name}\n{prod}\t12.5"

    res = client.post("/api/recipes/import/preview", json={"raw_text": raw}, headers=headers)
    assert res.status_code == 200
    assert any("자동 등록" in w["message"] for w in res.json()["warnings"])

    from src.db import get_connection
    with get_connection() as conn:  # 수정 전: 행이 존재 → red
        assert conn.execute("SELECT 1 FROM materials WHERE name = ?",
                            (name,)).fetchone() is None

    # 반복 미리보기도 무부작용(마스터 오염 누적 없음)
    client.post("/api/recipes/import/preview", json={"raw_text": raw}, headers=headers)
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM materials WHERE name = ?",
                            (name,)).fetchone()["n"] == 0

    # 실제 임포트는 여전히 자재를 등록하고 레시피를 만든다(회귀 방지)
    res2 = client.post("/api/recipes/import", json={"raw_text": raw, "force": True},
                       headers=headers)
    assert res2.status_code == 200 and res2.json()["created_count"] == 1
    with get_connection() as conn:
        assert conn.execute("SELECT 1 FROM materials WHERE name = ?",
                            (name,)).fetchone() is not None
```

### 3.5 수용 기준

1. `python -m pytest tests -v` 전부 green — 특히 `tests/test_import_parser.py`(파서 자동 등록 단위 테스트 — 파서 자체는 무변경이라 그대로 통과)와 `tests/test_recipe_management.py`.
2. 수동 확인(개발 서버, 책임자 로그인): 관리 화면에서 미등록 자재가 포함된 텍스트 붙여넣기 → 미리보기 → "새 원재료" 경고 표시 확인 → **등록하지 않고** 자재 목록 조회 시 해당 자재 부재. 이어서 등록 실행 → 자재·레시피 생성 확인.
3. 미리보기를 3회 반복해도 `materials`에 잔여 행 0.

### 3.6 롤백

- `git revert <F-3 커밋>` — 스키마 변경 없음. 롤백 시 미리보기 오염이 재발하므로, 재수정 전까지 "미리보기에 새 자재명이 있으면 임포트 확정 전 반복 미리보기 자제"를 책임자에게 공지.
- 과거에 이미 오염된 유령 자재(있다면)는 이 수정의 범위 밖 — 자재 관리 화면에서 `미분류` + 레시피 미사용 항목을 책임자가 수동 정리(참고용 점검 쿼리: `SELECT id, name FROM materials WHERE category='미분류' AND is_active=1 AND id NOT IN (SELECT DISTINCT material_id FROM recipe_items)`).

---

## 4. 배포 (3건 공통)

1. 각 건 커밋 전 `python -m pytest tests -v` 전 항목 green.
2. `tools/smoke_irms.py --mode development --seed-demo-data` 통과 확인.
3. git push → 운영 PC의 `serve.py`(run_auto.bat)가 주기 pull 감시로 자동 반영: **업데이트 직전 자동 DB 백업**(`backups/irms_*.db`) 후 재시작 → 기동 시 `init_db()`가 마이그레이션 적용. F-1은 §1.6의 사전 점검을 push **전에** 완료할 것.
4. 배포 후 관찰: 기동 로그 무오류, 배합 저장·기록 조회·DHR PDF 출력 스모크 1회.

## 5. 권장 실행 순서와 소요 추정

| 순서 | 건 | 근거 | 추정 |
|---|---|---|---|
| 1 | **F-2** (soft 취소 무인증) | 열려 있는 무인증 쓰기 API — 노출 시간 자체가 리스크. 변경이 라우트 1곳+엔드포인트 1개로 가장 작고 다른 건과 독립 | 1~2h |
| 2 | **F-3** (preview 커밋) | 5줄 수정 + 라우트 테스트. 마스터 오염이 누적형이라 빠를수록 정리 비용이 작다 | 1~2h |
| 3 | **F-1** (LOT 채번) | Critical이지만 유일하게 **운영 DB 사전 점검·책임자 협의·마이그레이션**이 필요한 건 — 절차를 갖춰 마지막에. 코드 자체는 마이그레이션+서비스 2파일 | 0.5~1일 (사전 점검·협의 포함) |

합계 약 1~1.5일. 커밋은 건별 분리(각각 독립 롤백 가능), push는 F-2·F-3을 먼저 내보내고 F-1은 사전 점검 완료 후 별도 push를 권장.
