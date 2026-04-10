# formula-excel-style Design Document

> **Summary**: 수식 입력 방식을 JSON/컬럼 단위에서 엑셀 스타일 셀 단위(`=A1+B1`)로 전환
>
> **Plan**: `docs/01-plan/features/formula-excel-style.plan.md`
> **Date**: 2026-04-10
> **Status**: Draft

---

## 1. Architecture Overview

### 1.1 현재 구조 (Before)

```
컬럼 생성 시 수식 타입 선택 → JSON 파라미터 입력 → 저장 시 서버 계산
┌──────────────┐    ┌──────────────────────┐    ┌──────────────────┐
│ Column Modal │ →  │ ss_columns 테이블     │ →  │ spreadsheet_     │
│ colType=formula   │ formula_type=SUM     │    │ formulas.py      │
│ JSON params  │    │ formula_params=JSON  │    │ calculate_row()  │
└──────────────┘    └──────────────────────┘    └──────────────────┘
```

### 1.2 변경 후 구조 (After)

```
셀에 =수식 직접 입력 → 그대로 저장 → 로드/저장 시 서버 계산
┌──────────────┐    ┌──────────────────────┐    ┌──────────────────┐
│ Cell input   │ →  │ ss_cells 테이블       │ →  │ spreadsheet_     │
│ "=B1+C1"     │    │ value="=B1+C1"       │    │ formulas.py      │
│              │    │ (수식 원문 저장)       │    │ evaluate_cell()  │
└──────────────┘    └──────────────────────┘    └──────────────────┘
```

---

## 2. DB Schema Changes

### 2.1 ss_columns 테이블

**변경 없음** (하위 호환). 기존 `formula_type`, `formula_params`, `col_type='formula'` 필드는 유지하되 더 이상 사용하지 않는다. 기존 수식 컬럼이 있으면 `col_type`을 `numeric`으로 변경하고 `formula_type`/`formula_params`를 NULL로 설정하는 마이그레이션을 적용한다.

```sql
-- Migration: 기존 수식 컬럼 → numeric 전환
UPDATE ss_columns
   SET col_type = 'numeric',
       formula_type = NULL,
       formula_params = NULL,
       is_readonly = 0
 WHERE col_type = 'formula';
```

### 2.2 ss_cells 테이블

**변경 없음**. `value` 필드에 수식 원문(`=B1+C1`)이 그대로 저장된다. `=`로 시작하면 수식, 아니면 일반 값.

### 2.3 ColumnCreate 모델 변경

```python
# Before
class ColumnCreate(BaseModel):
    name: str
    colType: str = "numeric"          # text | numeric | formula
    formulaType: str | None = None    # SUM | WEIGHTED | CUSTOM
    formulaParams: dict | None = None # JSON

# After
class ColumnCreate(BaseModel):
    name: str
    colType: str = "numeric"          # text | numeric (formula 제거)
```

---

## 3. Backend: Formula Engine 재설계

### 3.1 파일: `src/routers/spreadsheet_formulas.py` (전면 재작성)

#### 3.1.1 셀 참조 파서

```
셀 참조 문법: [A-Z]{1,2}[0-9]+
  A1 → col=0, row=0
  B3 → col=1, row=2
  AA1 → col=26, row=0

행 번호는 무시하고 같은 행 내 참조로 처리 (레시피 시트 특성)
```

#### 3.1.2 지원 문법

| 종류 | 예시 | 설명 |
|------|------|------|
| 리터럴 | `123`, `3.14` | 숫자 상수 |
| 셀 참조 | `A1`, `B1` | 같은 행의 해당 열 값 |
| 산술 | `+`, `-`, `*`, `/` | 사칙 연산 |
| 괄호 | `(A1+B1)*2` | 우선순위 |
| SUM | `SUM(B1:E1)` | 범위 합계 (같은 행) |
| ROUND | `ROUND(A1*0.75, 2)` | 반올림 |

#### 3.1.3 핵심 함수 설계

```python
def is_formula(value: str | None) -> bool:
    """값이 수식인지 판별 (=로 시작)"""

def evaluate_cell(
    expression: str,       # "=B1+C1*2" (= 포함)
    row_values: dict[int, float],  # {colIndex: value} 같은 행의 모든 값
) -> str:
    """수식 계산. 오류 시 '#ERR' 반환"""

def evaluate_row(
    columns: list[dict],        # 컬럼 목록
    cell_values: dict[int, str], # {colIndex: raw_string}
) -> dict[int, str]:
    """행 내 모든 수식 셀을 계산하여 결과 반환"""

def _parse_cell_ref(ref: str) -> int:
    """'B1' → colIndex 1, 'AA1' → colIndex 26 (행 번호 무시)"""

def _expand_range(start: str, end: str) -> list[int]:
    """'B1:E1' → [1, 2, 3, 4] (같은 행 내 colIndex 범위)"""
```

#### 3.1.4 AST 파서 변경

기존 `_eval_node` 구조 유지. 변경점:

| 항목 | Before | After |
|------|--------|-------|
| 변수 참조 | `c0`, `c1` (colIndex 숫자) | `A1`, `B1` (엑셀식) |
| 함수 호출 | 미지원 | `SUM()`, `ROUND()` via `ast.Call` 처리 |
| 수식 감지 | `formula_type` 필드 | `value.startswith("=")` |
| 오류 표현 | `None` 반환 | `"#ERR"` 문자열 반환 |

#### 3.1.5 보안 유지

- AST 기반 파서 유지 (`eval()` 미사용)
- 허용 연산자 화이트리스트 (`_SAFE_OPS`)
- 허용 함수 화이트리스트: `{"SUM", "ROUND"}`만
- 수식 길이 제한: 200자
- 0 나누기 → `#ERR`

---

## 4. Backend: Routes 변경

### 4.1 `spreadsheet_routes.py` 변경사항

#### 4.1.1 `save_sheet` 엔드포인트

```
Before:
1. 셀 저장 시 formula 컬럼은 건너뜀
2. calculate_row()로 수식 컬럼 계산
3. 계산 결과를 별도 INSERT

After:
1. 모든 셀 값 그대로 저장 (수식 원문 포함)
2. evaluate_row()로 수식 셀 결과 계산
3. 계산 결과는 응답에만 포함 (DB에는 수식 원문 유지)
```

#### 4.1.2 `_load_sheet_data` 함수

```
Before:
1. 셀 값 로드
2. calculate_row()로 수식 컬럼 재계산
3. 계산 결과를 cell_values에 merge

After:
1. 셀 값 로드 (수식 원문 포함)
2. evaluate_row()로 수식 셀 재계산
3. 응답에 formula(원문)과 display(계산결과) 모두 포함
```

#### 4.1.3 응답 형식 변경

```json
{
  "rows": [
    {
      "rowIndex": 0,
      "cells": {
        "0": "잉크A",
        "1": "100",
        "2": "200",
        "3": {
          "formula": "=B1+C1",
          "display": "300"
        }
      }
    }
  ]
}
```

수식 셀은 `{formula, display}` 객체로, 일반 셀은 문자열로 반환.

#### 4.1.4 제거 대상

- `CalcRequest` 모델 — 삭제
- `POST /calculate` 엔드포인트 — 삭제
- `ColumnCreate`에서 `formulaType`, `formulaParams` 필드 — 삭제
- 컬럼 생성 시 `colType='formula'` 허용 — 삭제
- `_col_dict()`에서 formulaType/formulaParams 처리 — 삭제

---

## 5. Frontend: spreadsheet_editor.js 변경

### 5.1 수식 감지 & 표시

```
셀 로드 시:
- 응답의 cells 값이 객체(formula+display)면 → 수식 셀
  - 표시: display 값
  - 배경색: #e8f4fd (기존과 동일)
  - 셀 선택/편집 시: formula 원문 표시

- 문자열이면 → 일반 셀
```

### 5.2 셀 데이터 처리 흐름

```javascript
// 시트 로드 시: 서버 응답 → JSpreadsheet 데이터 변환
function renderSheet(data) {
  // cells 값이 객체면 display 표시, formula는 별도 저장
  formulaMap = {};  // { "colIdx_rowIdx": "=B1+C1" }

  columns.forEach((col, ci) => {
    const cellVal = cells[String(col.colIndex)];
    if (typeof cellVal === "object" && cellVal.formula) {
      formulaMap[`${ci}_${ri}`] = cellVal.formula;
      rowArr.push(cellVal.display || "");
    } else {
      rowArr.push(cellVal || "");
    }
  });
}

// 저장 시: 수식 원문 복원하여 서버 전송
function collectSheetData() {
  // formulaMap에 수식이 있으면 현재 표시값 대신 수식 원문 전송
  // 사용자가 셀에 새 =수식을 입력했으면 그 값 전송
}
```

### 5.3 수식 셀 편집 UX

```
1. 수식 셀 클릭 → 셀에 수식 원문("=B1+C1") 표시
2. 편집 → 수식 수정 가능
3. 포커스 해제 → 다시 이전 계산값 표시 (실제 재계산은 저장 시)
4. 새 =수식 입력 → formulaMap 업데이트, isDirty = true
```

### 5.4 제거 대상

- `applyFormulaCellStyle()` — 새 로직으로 대체
- 컬럼 모달의 수식 타입/파라미터 UI
- `addColumn()`의 formula 분기
- `renderColumnList()`의 formula 타입 라벨
- `transferToImport()`의 formula 컬럼 필터링

---

## 6. Frontend: management.html 변경

### 6.1 컬럼 모달 수정

```html
<!-- 제거: ss-formula-config 전체 -->
<!-- 제거: colType select의 formula 옵션 -->

<!-- 남기기: -->
<select id="ss-new-col-type" class="select">
  <option value="numeric">숫자</option>
  <option value="text">텍스트</option>
</select>
```

---

## 7. Implementation Order

| 순서 | 파일 | 작업 | 의존성 |
|------|------|------|--------|
| 1 | `src/database.py` | 마이그레이션: 기존 formula 컬럼 → numeric 전환 | 없음 |
| 2 | `src/routers/spreadsheet_formulas.py` | 전면 재작성: 엑셀식 파서 | 없음 |
| 3 | `src/routers/spreadsheet_routes.py` | 저장/로드 로직 변경, CalcRequest 삭제 | #2 |
| 4 | `templates/management.html` | 컬럼 모달 수식 UI 제거 | 없음 |
| 5 | `static/js/spreadsheet_editor.js` | 수식 셀 감지/표시/편집/저장 | #3 |
| 6 | `static/js/common.js` | `ssCalculate()` 함수 제거 | #3 |

---

## 8. Error Handling

| 상황 | 표시 | 서버 동작 |
|------|------|-----------|
| 잘못된 수식 (`=A1+`) | `#ERR` | evaluate_cell → `"#ERR"` 반환 |
| 존재하지 않는 셀 참조 (`=Z1`) | 0으로 처리 | 범위 밖 colIndex → 0.0 |
| 0으로 나누기 (`=A1/0`) | `#ERR` | ZeroDivisionError → `"#ERR"` |
| 수식 길이 초과 (200자) | `#ERR` | 파싱 거부 |
| 순환 참조 | 해당 없음 | 같은 행 내 수식→수식 참조 없음 (수식 셀 값은 numeric_values에서 제외) |

---

## 9. Test Scenarios

| # | 입력 | 기대 결과 |
|---|------|-----------|
| 1 | `=B1+C1` (B1=100, C1=200) | `300` |
| 2 | `=B1*0.75` (B1=100) | `75` |
| 3 | `=SUM(B1:D1)` (B1=10, C1=20, D1=30) | `60` |
| 4 | `=ROUND(B1/3, 2)` (B1=10) | `3.33` |
| 5 | `=(B1+C1)*D1` (B1=2, C1=3, D1=4) | `20` |
| 6 | `=B1/0` | `#ERR` |
| 7 | `=INVALID(B1)` | `#ERR` |
| 8 | `hello` (= 없음) | `hello` (일반 텍스트) |
| 9 | `=SUM(A1:A1)` (A1=5) | `5` |
| 10 | `=ROUND(SUM(B1:D1)*0.1, 1)` | 중첩 함수 |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-04-10 | Initial design |
