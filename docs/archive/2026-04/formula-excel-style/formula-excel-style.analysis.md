# formula-excel-style Analysis Report

> **Analysis Type**: Gap Analysis (Design vs Implementation)
>
> **Project**: IRMS
> **Analyst**: Claude (gap-detector)
> **Date**: 2026-04-10
> **Design Doc**: [formula-excel-style.design.md](../02-design/features/formula-excel-style.design.md)

---

## 1. Match Rate Summary

```
Overall Match Rate: 100%

  Match:              56 items (100%)
  Missing in impl:     0 items (0%)
  Added in impl:       2 items (minor utilities, no gap)
  Changed:             0 items (0%)
```

---

## 2. Gap Analysis (Design vs Implementation)

### 2.1 DB Schema & Migration — 4/4 (100%)

| Design Requirement | Status |
|---|---|
| Migration: `col_type='formula'` → `numeric`, NULL formula fields, `is_readonly=0` | Match |
| `ss_cells.value`에 수식 원문 저장 (스키마 변경 없음) | Match |
| `ColumnCreate` 모델: `formulaType`/`formulaParams` 제거 | Match |
| `ss_columns` CHECK 제약: `'formula'` 제외 | Match |

### 2.2 Backend Formula Engine — 14/14 (100%)

| Design Requirement | Status |
|---|---|
| `spreadsheet_formulas.py` 전면 재작성 | Match |
| `is_formula()` — `=` 접두사 확인 | Match |
| `evaluate_cell()` — 수식 계산, 오류 시 `#ERR` | Match |
| `evaluate_row()` — 행 내 수식 셀 일괄 계산 | Match |
| `_parse_cell_ref()` — 셀 참조 파싱 (행 번호 무시) | Match |
| `_expand_range()` — 범위 확장 | Match |
| 셀 참조 문법 `[A-Z]{1,2}[0-9]+` | Match |
| 리터럴, 셀 참조, 산술, 괄호, SUM, ROUND 지원 | Match |
| AST 기반 파서 (eval 미사용) | Match |
| `_SAFE_OPS` 화이트리스트 | Match |
| `_SAFE_FUNCTIONS = {"SUM", "ROUND"}` | Match |
| 수식 길이 제한 200자 | Match |
| 0 나누기 → `#ERR` | Match |
| 미존재 셀 참조 → 0.0 | Match |

### 2.3 Backend Routes — 9/9 (100%)

| Design Requirement | Status |
|---|---|
| `save_sheet`: 수식 원문 그대로 DB 저장 | Match |
| `save_sheet`: `evaluate_row()` 결과는 응답에만 포함 | Match |
| `_load_sheet_data`: 수식 셀 `{formula, display}` 반환 | Match |
| `_load_sheet_data`: 일반 셀 문자열 반환 | Match |
| `CalcRequest` 모델 삭제 | Match |
| `POST /calculate` 엔드포인트 삭제 | Match |
| `_col_dict()` formula 필드 처리 제거 | Match |
| 컬럼 생성 `colType='formula'` 불허 | Match |
| 컬럼 INSERT에서 formula_type/params 제거 | Match |

### 2.4 Frontend — 15/15 (100%)

| Design Requirement | Status |
|---|---|
| `formulaMap` 객체로 수식 원문 관리 | Match |
| 로드 시 `{formula, display}` 셀 감지 → display 표시 | Match |
| 수식 셀 배경색 `#e8f4fd` | Match |
| 편집 시작: 수식 원문 표시 | Match |
| 편집 종료: formulaMap 업데이트, placeholder 표시 | Match |
| 저장 시 수식 원문 전송 | Match |
| `applyFormulaCellStyle()` → `applyFormulaStyles()` 대체 | Match |
| 컬럼 모달 수식 UI 제거 | Match |
| `addColumn()` formula 분기 제거 | Match |
| `renderColumnList()` formula 라벨 제거 | Match |
| `ssCalculate()` 함수 제거 | Match |
| `ss-formula-config` HTML 제거 | Match |
| `formula` select option 제거 | Match |
| `.ss-formula-cell` CSS 제거 | Match |
| `.ss-formula-config` CSS 제거 | Match |

### 2.5 Error Handling — 5/5 (100%)

| Error Scenario | Expected | Status |
|---|---|---|
| 잘못된 수식 `=A1+` | `#ERR` | Match |
| 미존재 셀 참조 `=Z1` | 0으로 처리 | Match |
| 0 나누기 `=A1/0` | `#ERR` | Match |
| 수식 > 200자 | `#ERR` | Match |
| 순환 참조 | 해당 없음 (수식 셀 제외) | Match |

### 2.6 Functional Requirements (Plan) — 9/9 (100%)

| ID | Requirement | Status |
|---|---|---|
| FR-01 | `=`로 시작하는 셀 → 수식 인식 | Match |
| FR-02 | 엑셀식 셀 참조 (A1, B3, AA1) | Match |
| FR-03 | 같은 행 내 가로 계산 | Match |
| FR-04 | `SUM(B1:E1)` 범위 합산 | Match |
| FR-05 | `ROUND(expr, digits)` 반올림 | Match |
| FR-06 | 수식 셀 결과 표시 + 배경색 구분 | Match |
| FR-07 | 수식 셀 편집 시 원문 표시 | Match |
| FR-08 | 기존 수식 컬럼 타입/모달 UI 제거 | Match |
| FR-09 | 오류 시 `#ERR` 표시 | Match |

---

## 3. Security Review

| Check | Status |
|---|---|
| `eval()` 미사용 | Pass |
| 연산자 화이트리스트 | Pass |
| 함수 화이트리스트 (SUM, ROUND) | Pass |
| 수식 길이 제한 (200자) | Pass |
| 미허용 AST 노드 거부 | Pass |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-04-10 | Initial analysis |
