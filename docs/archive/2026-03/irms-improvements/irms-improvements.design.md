# IRMS Improvements Design

> Plan 문서(irms-improvements.plan.md) 기반 상세 설계서

## 1. Design Overview

| Item | Detail |
|------|--------|
| Plan Reference | `docs/01-plan/features/irms-improvements.plan.md` |
| Affected Files | 6개 (database.py, api.py, common.js, work.js, management.js, insight.js) + 2개 신규 |
| Implementation Order | A (P0) -> B (P1) -> C (P2) -> D (P3) |

## 2. Implementation Order & Dependencies

```
A-1 (unit 변환 방지)
  |
  v
A-2 (XSS 방어) -----> B-1 (count 집계) -----> C-1 (스키마 보완)
                  |                         |
                  +-> B-2 (CDN 로컬화)      +-> C-2 (상태 전이)
                  |                         |
                  +-> B-3 (debounce)        +-> C-3 (deprecated API)
                                            |
                                            +-> D-1 (SQL 패턴)
```

A-1은 독립적으로 먼저 수행. A-2 완료 후 B 항목은 병렬 진행 가능.
C 항목은 B 완료 후 병렬 진행 가능. D는 마지막.

---

## 3. Detailed Design Per Item

### A-1: unit 변환 중복 실행 방지

**Target**: `src/database.py`

**Strategy**: `schema_migrations` 메타 테이블 도입. 마이그레이션 이름별 실행 여부를 기록하여 1회만 실행.

**Changes**:

```python
# database.py - init_db() 내 스키마 생성에 추가
"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""
```

```python
# 새 함수: 마이그레이션 실행 여부 체크
def has_migration(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
    ).fetchone()
    return row is not None

def record_migration(connection: sqlite3.Connection, name: str) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (name, utc_now_text()),
    )
```

```python
# standardize_recipe_units_to_grams 수정
def standardize_recipe_units_to_grams(connection: sqlite3.Connection) -> None:
    if has_migration(connection, "standardize_units_to_grams"):
        return  # 이미 실행됨 - 스킵

    # ... 기존 변환 로직 유지 ...

    record_migration(connection, "standardize_units_to_grams")
```

**Verification**: 서버 2회 재시작 후 value_weight 값이 변하지 않음을 확인.

---

### A-2: XSS 방어

**Target**: `static/js/common.js`, `static/js/work.js`, `static/js/management.js`, `static/js/insight.js`

**Strategy**: `common.js`에 `escapeHtml()` 유틸 함수를 추가하고, innerHTML에 삽입되는 모든 사용자 유래 데이터에 적용.

**Changes (common.js)**:

```javascript
// common.js - IRMS 네임스페이스에 추가
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// window.IRMS 에 등록
window.IRMS = {
  // ... 기존 항목 ...
  escapeHtml,
};
```

**적용 대상 (innerHTML에 사용자 데이터 삽입하는 모든 위치)**:

| File | Line(s) | 이스케이프 대상 필드 |
|------|---------|---------------------|
| work.js | 107-133 (buildRows) | productName, position, inkName, createdBy |
| work.js | 154-164 (renderLog) | productName, inkName, createdBy |
| work.js | 277-278 (renderWeighingPanel) | productName, inkName, position |
| work.js | 304-307 (renderWeighingPanel) | materialName, productName, position, inkName |
| work.js | 311-312 (renderWeighingPanel) | materialName, productName |
| management.js | 252-265 (renderHistory) | productName, inkName, createdBy |
| management.js | 217-234 (renderPreview) | productName, position, inkName |
| insight.js | 58-68 (renderWeightTable) | materialName, colorGroup |
| insight.js | 81-91 (renderCountTable) | materialName, colorGroup |
| insight.js | 113-114 (renderBars) | label(materialName) |

**적용 패턴**:
```javascript
// Before:
`<td>${recipe.productName}</td>`

// After:
`<td>${IRMS.escapeHtml(recipe.productName)}</td>`
```

**Verification**: `<script>alert(1)</script>` 를 제품명으로 등록 시도 후 화면에 텍스트로 표시되는지 확인.

---

### B-1: 통계 count 집계 수정

**Target**: `src/routers/api.py` - `stats_consumption()`

**Before** (line 496):
```sql
SUM(CASE WHEN m.unit_type = 'count' THEN COALESCE(ri.value_weight, 0) ELSE 0 END) AS total_count
```

**After**:
```sql
SUM(CASE WHEN m.unit_type = 'count' AND ri.value_text IS NOT NULL THEN 1 ELSE 0 END) AS total_count
```

**Rationale**: count 타입 원재료는 `value_text`에 코드값(APB, NPB 등)이 저장되고 `value_weight`는 NULL임. 따라서 `value_text`가 존재하는 행을 카운트하는 것이 올바른 집계.

**Verification**: completed 레시피에 count 타입 원재료가 포함된 경우 Insight 화면에서 0이 아닌 정상 카운트 표시.

---

### B-2: CDN 라이브러리 로컬화

**Target**: `templates/management.html`, 신규 `static/vendor/` 디렉토리

**Strategy**: JSpreadsheet CE와 jSuites를 로컬 vendor 디렉토리에 저장.

**디렉토리 구조**:
```
static/vendor/
  jspreadsheet/
    jspreadsheet.min.css
    jspreadsheet.min.js    (index.min.js)
  jsuites/
    jsuites.min.css
    jsuites.min.js
```

**다운로드 소스** (현재 CDN URL 기준):
- `https://cdn.jsdelivr.net/npm/jspreadsheet-ce/dist/jspreadsheet.min.css`
- `https://cdn.jsdelivr.net/npm/jspreadsheet-ce/dist/index.min.js`
- `https://cdn.jsdelivr.net/npm/jsuites/dist/jsuites.min.css`
- `https://cdn.jsdelivr.net/npm/jsuites/dist/jsuites.min.js`

**management.html 변경**:
```html
<!-- Before (CDN) -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/jspreadsheet-ce/dist/jspreadsheet.min.css" />
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/jsuites/dist/jsuites.min.css" />
...
<script src="https://cdn.jsdelivr.net/npm/jsuites/dist/jsuites.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/jspreadsheet-ce/dist/index.min.js"></script>

<!-- After (Local) -->
<link rel="stylesheet" href="/static/vendor/jspreadsheet/jspreadsheet.min.css" />
<link rel="stylesheet" href="/static/vendor/jsuites/jsuites.min.css" />
...
<script src="/static/vendor/jsuites/jsuites.min.js"></script>
<script src="/static/vendor/jspreadsheet/jspreadsheet.min.js"></script>
```

**Verification**: 네트워크 연결 끊은 상태에서 Management 페이지 로드 시 스프레드시트 UI 정상 렌더링.

---

### B-3: debounce 추가

**Target**: `static/js/common.js`, `static/js/work.js`, `static/js/management.js`

**Changes (common.js)**:
```javascript
function debounce(fn, delay) {
  let timer = null;
  return function (...args) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { fn.apply(this, args); }, delay);
  };
}

// window.IRMS 에 등록
window.IRMS = {
  // ... 기존 항목 ...
  debounce,
};
```

**적용 (work.js)**:
```javascript
// Before:
searchInput.addEventListener("input", render);

// After:
searchInput.addEventListener("input", IRMS.debounce(render, 300));
```

**적용 (management.js)**:
```javascript
// Before:
historySearch.addEventListener("input", renderHistory);

// After:
historySearch.addEventListener("input", IRMS.debounce(renderHistory, 300));
```

**Verification**: 검색 입력 시 브라우저 Network 탭에서 300ms 내 중복 요청이 발생하지 않음.

---

### C-1: DB 스키마 갭 보완

**Target**: `src/database.py` - `apply_schema_migrations()`

**추가할 컬럼**:

| Table | Column | Definition | Purpose |
|-------|--------|------------|---------|
| recipes | note | TEXT | 비고 |
| recipes | cancel_reason | TEXT | 취소 사유 |
| recipes | started_by | TEXT | 작업 시작자 |
| recipes | started_at | TEXT | 작업 시작 시각 |
| recipes | raw_input_hash | TEXT | 붙여넣기 원문 SHA256 |
| recipes | raw_input_text | TEXT | 붙여넣기 원문 |
| recipes | revision_of | INTEGER | 개정 원본 레시피 ID |

**Changes (database.py)**:
```python
def apply_schema_migrations(connection: sqlite3.Connection) -> None:
    # 기존 마이그레이션
    ensure_column(connection, "recipe_items", "measured_at", "TEXT")
    ensure_column(connection, "recipe_items", "measured_by", "TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_recipe_items_measured_at ON recipe_items(measured_at)"
    )
    standardize_recipe_units_to_grams(connection)

    # C-1: 누락 컬럼 추가
    ensure_column(connection, "recipes", "note", "TEXT")
    ensure_column(connection, "recipes", "cancel_reason", "TEXT")
    ensure_column(connection, "recipes", "started_by", "TEXT")
    ensure_column(connection, "recipes", "started_at", "TEXT")
    ensure_column(connection, "recipes", "raw_input_hash", "TEXT")
    ensure_column(connection, "recipes", "raw_input_text", "TEXT")
    ensure_column(connection, "recipes", "revision_of", "INTEGER REFERENCES recipes(id)")
```

**API 변경 (api.py - import_recipes)**:
```python
# import 시 raw_input 저장
import hashlib

raw_hash = hashlib.sha256(body.raw_text.encode()).hexdigest()

# INSERT 시 raw_input_hash, raw_input_text 포함
```

**API 변경 (api.py - update_recipe_status)**:
```python
# cancel 시 reason 저장
if body.action == "cancel" and body.reason:
    connection.execute(
        "UPDATE recipes SET cancel_reason = ? WHERE id = ?",
        (body.reason, recipe_id),
    )
```

---

### C-2: 상태 전이 정합성

**Target**: `src/routers/api.py` - `update_recipe_status()`

**Before**: `complete` 액션이 `pending -> completed` 직접 전이 허용

**After**: `complete` 액션에서 `pending` 상태의 경우, 자동으로 `started_by/at`를 기록한 후 `completed`로 전환 (원스텝 완료 지원하되 추적 데이터 보전).

```python
if body.action == "complete" and current_status == "pending":
    allowed = True
    next_status = "completed"
    # 자동으로 시작 기록 (추적성 보전)
    now = utc_now_text()
    connection.execute(
        "UPDATE recipes SET started_by = ?, started_at = ? WHERE id = ? AND started_at IS NULL",
        ("auto", now, recipe_id),
    )
```

**Rationale**: 계량 모드를 거치지 않고 Work 화면에서 직접 완료하는 것도 현장에서 유효한 시나리오. 단, 시작 시각을 자동 기록하여 추적성을 확보.

---

### C-3: deprecated API 교체

**Target**: `src/database.py:8-9`

**Before**:
```python
from datetime import datetime

def utc_now_text() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()
```

**After**:
```python
from datetime import datetime, timezone

def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "")
```

**Note**: `.replace("+00:00", "")` 로 기존 포맷(`2026-03-07T15:00:00`)과 호환 유지. 기존 DB 데이터와 비교 쿼리에 영향 없음.

---

### D-1: SQL 패턴 개선

**Target**: `src/database.py` - `ensure_column()`

**Before**:
```python
def ensure_column(connection, table_name, column_name, column_def):
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
```

**After**:
```python
_ALLOWED_TABLES = frozenset({"materials", "material_aliases", "recipes", "recipe_items", "schema_migrations"})

def ensure_column(connection, table_name, column_name, column_def):
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"Unknown table: {table_name}")
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
```

---

## 4. File Change Summary

| File | Changes | Lines (est.) |
|------|---------|-------------|
| `src/database.py` | A-1 (migration table), C-1 (columns), C-3 (datetime), D-1 (whitelist) | ~40 |
| `src/routers/api.py` | B-1 (count query), C-1 (raw_input save, cancel_reason), C-2 (state transition) | ~25 |
| `static/js/common.js` | A-2 (escapeHtml), B-3 (debounce) | ~20 |
| `static/js/work.js` | A-2 (escape calls), B-3 (debounce bind) | ~15 |
| `static/js/management.js` | A-2 (escape calls), B-3 (debounce bind) | ~10 |
| `static/js/insight.js` | A-2 (escape calls) | ~10 |
| `templates/management.html` | B-2 (local vendor paths) | ~4 |
| `static/vendor/` (new) | B-2 (JSpreadsheet/jSuites local files) | 4 files |

## 5. Rollback Strategy

- **DB 변경 전**: `data/irms.db` 파일 백업 (`irms.db.bak`)
- **schema_migrations 테이블**: 새 테이블이므로 DROP으로 롤백 가능
- **컬럼 추가**: SQLite는 ALTER DROP COLUMN 미지원이지만, 추가 컬럼은 NULL 허용이므로 무해
- **JS 변경**: git 기반 롤백 또는 파일 백업

## 6. Test Checklist

| # | Test | Expected |
|---|------|----------|
| T-1 | 서버 2회 재시작 후 recipe_items.value_weight 값 불변 | PASS |
| T-2 | `<script>alert(1)</script>` 제품명 등록 후 Work/Management 화면 | 텍스트 표시, 스크립트 미실행 |
| T-3 | Insight > count 타입 원재료 집계 | 0이 아닌 정상 카운트 |
| T-4 | 네트워크 차단 후 Management 스프레드시트 로드 | 정상 렌더링 |
| T-5 | 검색창 빠른 타이핑 (10자) | API 요청 1-2회 (debounce) |
| T-6 | 레시피 취소 시 cancel_reason 저장 | DB에 사유 기록 |
| T-7 | pending -> complete 직접 전이 시 started_at 자동 기록 | DB에 시각 기록 |
| T-8 | Import 시 raw_input_hash/text 저장 | DB에 원문+해시 기록 |
