# Recipe Spreadsheet Editor Design Document

> **Summary**: 앱 내 스프레드시트 에디터로 제품별 레시피 생성·편집·수식 계산·등록 연계
>
> **Project**: IRMS
> **Author**: Claude
> **Date**: 2026-04-10
> **Status**: Draft
> **Planning Doc**: [recipe-spreadsheet-editor.plan.md](../../01-plan/features/recipe-spreadsheet-editor.plan.md)

---

## 1. Overview

### 1.1 Design Goals

- Management 페이지에 "레시피 편집" 탭 추가 (기존 탭 유지)
- 제품별 탭(시트) CRUD + 행(테스트) CRUD
- 서버 사이드 수식 계산 (SUM, 가중합 등)
- 편집 시트에서 선택 행을 기존 Import 플로우로 전달

### 1.2 Design Principles

- 기존 코드 최소 변경 — 새 탭/라우터/테이블로 분리
- JSpreadsheet CE 재사용 — 이미 포함된 vendor 활용
- 명시적 저장 — 실험 단계이므로 자동 저장 없음

---

## 2. Architecture

### 2.1 Component Diagram

```
┌──────────────────────────────────────────────────────┐
│  Browser (Management Page)                           │
│  ┌────────┬──────────┬──────────┬──────────────────┐ │
│  │Import  │ History  │ Lookup   │ 레시피 편집 (NEW)│ │
│  └────────┴──────────┴──────────┴──────────────────┘ │
│                                    │                  │
│          JSpreadsheet CE ◄─────────┘                  │
│              │                                        │
└──────────────┼────────────────────────────────────────┘
               │ REST API
┌──────────────▼────────────────────────────────────────┐
│  FastAPI (spreadsheet_routes.py)                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐│
│  │Product   │  │Sheet     │  │Formula Engine        ││
│  │CRUD      │  │Save/Load │  │(Python server-side)  ││
│  └──────────┘  └──────────┘  └──────────────────────┘│
│                       │                               │
└───────────────────────┼───────────────────────────────┘
                        │
┌───────────────────────▼───────────────────────────────┐
│  SQLite (data/irms.db)                                │
│  ┌──────────────────┐  ┌────────────────────────────┐ │
│  │ss_products       │  │ss_columns                  │ │
│  │ss_rows           │  │ss_cells                    │ │
│  └──────────────────┘  └────────────────────────────┘ │
└───────────────────────────────────────────────────────┘
```

### 2.2 Data Flow

```
제품 탭 선택 → GET /api/spreadsheet/products/{id}/sheet
             → 서버: DB에서 컬럼/행/셀 로드 + 수식 계산
             → 응답: { columns, rows (셀 값 + 계산값 포함) }
             → JSpreadsheet 렌더링

셀 편집 후 저장 → POST /api/spreadsheet/products/{id}/save
               → 서버: 셀 값 저장 + 수식 재계산
               → 응답: { rows (재계산된 값 포함) }
               → JSpreadsheet 갱신

시트→등록 전달 → 프론트에서 선택 행을 TSV로 변환
              → Import 탭의 스프레드시트에 로드 (기존 플로우)
```

---

## 3. Data Model

### 3.1 Database Schema

```sql
-- 제품 (탭 단위)
CREATE TABLE IF NOT EXISTS ss_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 컬럼 정의 (제품별)
CREATE TABLE IF NOT EXISTS ss_columns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES ss_products(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    col_index INTEGER NOT NULL,
    col_type TEXT NOT NULL CHECK (col_type IN ('text', 'numeric', 'formula')),
    formula_type TEXT,          -- NULL, 'SUM', 'WEIGHTED', 'CUSTOM'
    formula_params TEXT,        -- JSON: {"source_columns": [3,4,5]} 또는 {"weights": {"3": 0.75, "4": 0.28}}
    is_readonly INTEGER NOT NULL DEFAULT 0,
    UNIQUE(product_id, col_index)
);

-- 행 (테스트/레시피 항목)
CREATE TABLE IF NOT EXISTS ss_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES ss_products(id) ON DELETE CASCADE,
    row_index INTEGER NOT NULL,
    UNIQUE(product_id, row_index)
);

-- 셀 값
CREATE TABLE IF NOT EXISTS ss_cells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id INTEGER NOT NULL REFERENCES ss_rows(id) ON DELETE CASCADE,
    column_id INTEGER NOT NULL REFERENCES ss_columns(id) ON DELETE CASCADE,
    value TEXT,                  -- 모든 값을 TEXT로 저장 (숫자도 문자열)
    UNIQUE(row_id, column_id)
);

CREATE INDEX IF NOT EXISTS idx_ss_columns_product ON ss_columns(product_id, col_index);
CREATE INDEX IF NOT EXISTS idx_ss_rows_product ON ss_rows(product_id, row_index);
CREATE INDEX IF NOT EXISTS idx_ss_cells_row ON ss_cells(row_id);
```

### 3.2 Entity Relationships

```
[ss_products] 1 ──── N [ss_columns]   (제품의 컬럼 정의)
[ss_products] 1 ──── N [ss_rows]      (제품의 행)
[ss_rows]     1 ──── N [ss_cells]     (행의 셀 값)
[ss_columns]  1 ──── N [ss_cells]     (컬럼의 셀 값)
```

### 3.3 고정 컬럼 vs 재료 컬럼

| col_index | name | col_type | 용도 |
|-----------|------|----------|------|
| 0 | 제품명 | text | 제품 이름 (자동 채움, 읽기 전용) |
| 1 | 위치 | text | Position (e.g., "55%") |
| 2 | 잉크명 | text | Ink name |
| 3+ | (재료명) | numeric | 재료별 목표량 (g) |
| N+ | (수식명) | formula | TOTAL, BINDER 등 계산 컬럼 |

---

## 4. API Specification

### 4.1 Endpoint List

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | /api/spreadsheet/products | 제품 목록 조회 | manager |
| POST | /api/spreadsheet/products | 제품 생성 | manager |
| PATCH | /api/spreadsheet/products/{id} | 제품 이름/설명 수정 | manager |
| DELETE | /api/spreadsheet/products/{id} | 제품 삭제 | manager |
| GET | /api/spreadsheet/products/{id}/sheet | 시트 전체 로드 (컬럼+행+셀+계산) | manager |
| POST | /api/spreadsheet/products/{id}/save | 시트 전체 저장 (덮어쓰기) | manager |
| POST | /api/spreadsheet/products/{id}/columns | 컬럼 추가 | manager |
| DELETE | /api/spreadsheet/columns/{col_id} | 컬럼 삭제 | manager |
| POST | /api/spreadsheet/products/{id}/rows | 행 추가 | manager |
| DELETE | /api/spreadsheet/rows/{row_id} | 행 삭제 | manager |
| POST | /api/spreadsheet/calculate | 수식 계산 (미리보기) | manager |

### 4.2 Detailed Specification

#### `GET /api/spreadsheet/products`

**Response (200):**
```json
{
  "items": [
    { "id": 1, "name": "55%(powder)", "description": null, "columnCount": 8, "rowCount": 16, "updatedAt": "2026-04-10T..." }
  ]
}
```

#### `POST /api/spreadsheet/products`

**Request:**
```json
{ "name": "55%(solution)", "description": "솔루션 레시피" }
```

**Response (201):**
```json
{ "id": 2, "name": "55%(solution)", "description": "솔루션 레시피", "createdAt": "..." }
```

**Errors:** `409 PRODUCT_NAME_EXISTS`

#### `GET /api/spreadsheet/products/{id}/sheet`

**Response (200):**
```json
{
  "product": { "id": 1, "name": "55%(powder)" },
  "columns": [
    { "id": 1, "name": "제품명", "colIndex": 0, "colType": "text", "isReadonly": true },
    { "id": 2, "name": "위치", "colIndex": 1, "colType": "text", "isReadonly": false },
    { "id": 3, "name": "잉크명", "colIndex": 2, "colType": "text", "isReadonly": false },
    { "id": 4, "name": "RAVEN", "colIndex": 3, "colType": "numeric", "isReadonly": false },
    { "id": 5, "name": "BLACK", "colIndex": 4, "colType": "numeric", "isReadonly": false },
    { "id": 10, "name": "TOTAL", "colIndex": 8, "colType": "formula", "isReadonly": true, "formulaType": "SUM", "formulaParams": { "sourceColumns": [3,4,5,6,7] } }
  ],
  "rows": [
    {
      "id": 1,
      "rowIndex": 0,
      "cells": {
        "0": "55%(powder)",
        "1": "55%",
        "2": "Test-1",
        "3": "100",
        "4": "50",
        "8": "250"
      }
    }
  ]
}
```

#### `POST /api/spreadsheet/products/{id}/save`

**Request:**
```json
{
  "rows": [
    {
      "rowIndex": 0,
      "cells": { "0": "55%(powder)", "1": "55%", "2": "Test-1", "3": "100", "4": "50" }
    },
    {
      "rowIndex": 1,
      "cells": { "0": "55%(powder)", "1": "55%", "2": "Test-2", "3": "120", "4": "60" }
    }
  ]
}
```

**Response (200):**
```json
{
  "saved": true,
  "rowCount": 2,
  "rows": [
    {
      "rowIndex": 0,
      "cells": { "0": "55%(powder)", "1": "55%", "2": "Test-1", "3": "100", "4": "50", "8": "250" }
    }
  ]
}
```

> 응답에 수식 컬럼 계산 결과가 포함되어 프론트에서 갱신.

#### `POST /api/spreadsheet/calculate`

**Request:**
```json
{
  "formulaType": "SUM",
  "formulaParams": { "sourceColumns": [3,4,5] },
  "values": { "3": "100", "4": "50", "5": "30" }
}
```

**Response (200):**
```json
{ "result": "180" }
```

### 4.3 Error Codes

| Code | Key | Description |
|------|-----|-------------|
| 404 | PRODUCT_NOT_FOUND | 제품을 찾을 수 없음 |
| 409 | PRODUCT_NAME_EXISTS | 이미 존재하는 제품명 |
| 400 | INVALID_FORMULA | 수식 파라미터 오류 |
| 400 | COLUMN_LIMIT_EXCEEDED | 컬럼 수 제한 초과 (max 30) |

---

## 5. Formula Engine

### 5.1 Supported Formula Types

| Type | Description | Params | Example |
|------|-------------|--------|---------|
| `SUM` | 지정 컬럼 합계 | `sourceColumns: [3,4,5]` | TOTAL = SUM(재료1, 재료2, ...) |
| `WEIGHTED` | 가중합 | `weights: {"3": 0.75, "4": 0.28}` | BINDER = (D*0.75)+(E*0.28)+... |
| `CUSTOM` | 파이썬 수식 문자열 | `expression: "c3 * 0.5 + c4"` | 반사량, 안료량 등 |

### 5.2 계산 로직 (Python)

```python
def calculate_formula(formula_type: str, formula_params: dict, row_values: dict[int, float]) -> str | None:
    if formula_type == "SUM":
        cols = formula_params.get("sourceColumns", [])
        return str(sum(row_values.get(c, 0.0) for c in cols))

    if formula_type == "WEIGHTED":
        weights = formula_params.get("weights", {})
        return str(sum(row_values.get(int(c), 0.0) * w for c, w in weights.items()))

    if formula_type == "CUSTOM":
        expr = formula_params.get("expression", "")
        # 안전한 수식 평가 (ast.literal_eval 기반 또는 제한된 eval)
        namespace = {f"c{i}": row_values.get(i, 0.0) for i in row_values}
        return str(eval(expr, {"__builtins__": {}}, namespace))

    return None
```

### 5.3 수식 보안

- `CUSTOM` 수식은 `eval` 대신 `ast` 기반 안전 파서 사용
- 허용 연산: `+`, `-`, `*`, `/`, `()`, 숫자, `cN` 변수만
- 금지: 함수 호출, import, 속성 접근, 문자열 조작

---

## 6. UI/UX Design

### 6.1 Management 페이지 탭 구성

```
┌───────────┬──────────┬──────────┬───────────────┐
│ 레시피 등록│ 등록 이력 │ 레시피 조회│ 레시피 편집 ◄─│ NEW
└───────────┴──────────┴──────────┴───────────────┘
```

### 6.2 레시피 편집 탭 레이아웃

```
┌─────────────────────────────────────────────────────┐
│  레시피 편집                                         │
├─────────────────────────────────────────────────────┤
│  제품 탭: [55%(powder)] [55%(solution)] [+ 추가]     │
├─────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────┐  │
│  │  JSpreadsheet                                 │  │
│  │  ┌──────┬─────┬──────┬──────┬──────┬────────┐│  │
│  │  │제품명│위치 │잉크명│RAVEN │BLACK │ TOTAL  ││  │
│  │  ├──────┼─────┼──────┼──────┼──────┼────────┤│  │
│  │  │(auto)│55%  │Test-1│ 100  │ 50   │  150 * ││  │
│  │  │(auto)│55%  │Test-2│ 120  │ 60   │  180 * ││  │
│  │  │      │     │      │      │      │        ││  │
│  │  └──────┴─────┴──────┴──────┴──────┴────────┘│  │
│  │  * = 수식 계산 (읽기 전용, 배경색 구분)       │  │
│  └───────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────┤
│  [행 추가] [행 삭제] [컬럼 관리]   [저장] [등록 전달]│
└─────────────────────────────────────────────────────┘
```

### 6.3 컬럼 관리 모달

```
┌─────────────────────────────────────┐
│  컬럼 관리                          │
├─────────────────────────────────────┤
│  재료 컬럼:                         │
│  [RAVEN     ] [numeric ▼] [삭제]   │
│  [BLACK     ] [numeric ▼] [삭제]   │
│  [+ 재료 컬럼 추가]                 │
│                                     │
│  수식 컬럼:                         │
│  [TOTAL     ] SUM(RAVEN,BLACK,...) │
│  [BINDER    ] WEIGHTED(...)        │
│  [+ 수식 컬럼 추가]                 │
├─────────────────────────────────────┤
│                        [닫기]       │
└─────────────────────────────────────┘
```

### 6.4 User Flow

```
Management 진입
  → "레시피 편집" 탭 클릭
  → 제품 탭 목록 로드 (빈 경우 "제품 추가" 안내)
  → 제품 탭 선택 → 시트 로드 (컬럼+행+셀+계산값)
  → 셀 편집 → 수식 컬럼은 편집 불가 (읽기 전용)
  → [저장] → 서버 저장 + 수식 재계산 → 시트 갱신
  → [등록 전달] → 선택 행을 TSV로 변환 → Import 탭에 로드
```

---

## 7. Security Considerations

- [x] 수식 CUSTOM 타입: `ast` 기반 안전 파서 (eval 제한)
- [x] 입력 검증: 제품명 길이 제한 (max 100), 컬럼 수 제한 (max 30)
- [x] SQL Injection: SQLite 파라미터 바인딩 사용
- [x] XSS: JSpreadsheet CE 자체 이스케이프 + 서버 응답 JSON
- [x] 인증: manager 접근 레벨 필수 (require_access_level)

---

## 8. Implementation Guide

### 8.1 File Structure

```
src/routers/
├── spreadsheet_routes.py     # NEW: 스프레드시트 API 라우터
├── spreadsheet_formulas.py   # NEW: 수식 엔진

src/database.py               # MODIFY: 마이그레이션 추가

templates/
├── management.html           # MODIFY: "레시피 편집" 탭 추가

static/js/
├── spreadsheet_editor.js     # NEW: 편집 탭 JS 로직
├── common.js                 # MODIFY: API 함수 추가

static/css/
├── spreadsheet_editor.css    # NEW: 편집 탭 스타일
```

### 8.2 Implementation Order

1. **DB 마이그레이션** — `ss_products`, `ss_columns`, `ss_rows`, `ss_cells` 테이블 생성
2. **수식 엔진** — `spreadsheet_formulas.py` (SUM, WEIGHTED, CUSTOM 계산)
3. **API 라우터** — `spreadsheet_routes.py` (CRUD + sheet load/save + calculate)
4. **JS API 함수** — `common.js`에 IRMS.spreadsheet* 함수 추가
5. **HTML 탭** — `management.html`에 "레시피 편집" 탭 추가
6. **편집 UI** — `spreadsheet_editor.js` + `spreadsheet_editor.css`
7. **등록 연계** — 선택 행 → Import 탭 전달 로직

### 8.3 App 등록

```python
# src/app.py (또는 main.py)
from .routers.spreadsheet_routes import build_router as build_spreadsheet_router

spreadsheet_router = build_spreadsheet_router()
app.include_router(spreadsheet_router, prefix="/api/spreadsheet", tags=["spreadsheet"])
```

---

## 9. Conventions

### 9.1 Naming

| Target | Rule | Example |
|--------|------|---------|
| DB 테이블 | `ss_` 접두사 (spreadsheet 약어) | `ss_products`, `ss_cells` |
| API 경로 | `/api/spreadsheet/...` | `/api/spreadsheet/products` |
| JS 함수 | `IRMS.ss*` 또는 `IRMS.spreadsheet*` | `IRMS.ssLoadSheet()` |
| CSS 클래스 | `ss-` 접두사 | `.ss-toolbar`, `.ss-tab` |
| Python 함수 | `snake_case` | `calculate_formula()` |

### 9.2 JSON 변환

| Python (snake_case) | JS (camelCase) |
|---------------------|----------------|
| col_index | colIndex |
| col_type | colType |
| formula_type | formulaType |
| formula_params | formulaParams |
| is_readonly | isReadonly |
| row_index | rowIndex |
| source_columns | sourceColumns |
| created_at | createdAt |
| updated_at | updatedAt |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-10 | Initial draft | Claude |
