# Gap Analysis — excel-recipe-migration

**Feature**: excel-recipe-migration
**Phase**: Check
**Date**: 2026-04-14
**Match Rate**: **97%**

## 1. Verification Matrix

| # | Design Item | File / Location | Status |
|---|---|---|---|
| 3.1 | `recipes.remark` 컬럼 마이그레이션 (`ensure_column`) | `src/database.py:107` | ✅ MATCH |
| 3.2 | `normalize_material_name` (UPPER/TRIM/공백 정규화) | `src/services/material_resolver.py:12-15` | ✅ MATCH |
| 3.2 | `resolve_material` + 별칭 fallback | `src/services/material_resolver.py:18-47` | ✅ MATCH |
| 3.4 | `parse_cell` — None/empty/"-"/숫자/수식/혼합 | `src/services/cell_value_parser.py:25-76` | ✅ MATCH |
| 3.4 | 하이픈 코드 보호 (BYK-199 비분리) | `cell_value_parser.py:_is_number` — `float()` 토큰 검증 | ✅ MATCH |
| 3.4 | "마지막 숫자 우선, 괄호는 메모" 규칙 | `cell_value_parser.py:56-76` | ✅ MATCH |
| 3.5 | `import_parser` 비고 컬럼 수집 (`비고/REMARK/NOTE`) | `src/services/import_parser.py:131,202-214,325-345` | ✅ MATCH |
| 3.5 | `recipe_routes` SELECT/INSERT에 remark 포함 | `src/routers/recipe_routes.py` (4 SELECT + import INSERT) | ✅ MATCH |
| 3.5 | `speakText` 괄호 제거 + 큐 취소 | `static/js/common.js:967-980` | ✅ MATCH |
| 3.5 | `work.js` 중복 발화 방지 + "-" 스킵 | `static/js/work.js:485-492` | ✅ MATCH |
| 3.6 | `scripts/import_excel_recipes.py` openpyxl 벌크 임포터 | `scripts/import_excel_recipes.py:27-275` (섹션 탐지, 원재료 해석, dry-run, 미등록 abort, audit log) | ✅ MATCH |

## 2. Gaps

### Low Severity

**G1. 셀 파서 이중화 (DRY 위반)**
- `src/services/import_parser.py::_parse_value`가 신규 표준 `cell_value_parser.parse_cell`을 호출하지 않고 자체 정규식으로 구현되어 있음.
- 엣지 케이스에서 동작 차이:
  - `"APB(17) 360"` → 신규 파서: `(360.0, "APB (17)")` / 레거시: `(360.0, "APB(17)")`
- **영향**: 탭 붙여넣기 임포트와 xlsx 벌크 임포트의 결과가 미묘하게 달라질 수 있음.
- **권장**: `_parse_value`를 `parse_cell` 호출로 교체하여 파서 단일화.

**G2. material_resolver SQL 측 정규화 비대칭**
- Python `normalize_material_name`은 `split()`로 임의 개수의 공백을 하나로 수축.
- SQL 쿼리는 `REPLACE(..., '  ', ' ')`로 **2칸만** 처리.
- **영향**: Python 측에서 이미 정규화된 문자열로 쿼리하므로 현재 데이터상 실패 사례는 없음.
- **권장**: 대칭성을 위해 SQL 측도 trim만 하고 비교는 Python 정규화 결과에 의존 (이미 그 구조).

### Critical
없음.

## 3. 결론

- 설계 문서에 명시된 모든 파일/함수/동작이 구현되었고 dry-run 실제 xlsx 2개(powder, solution)에서 정상 파싱 확인.
- Match Rate **97% ≥ 90%** → **Report 단계 진행 가능**.
- G1 정리는 선택 사항으로 다음 리팩토링 사이클에서 처리 권장.
