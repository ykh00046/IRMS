# Plan: 테스트 커버리지 기반 구축 + 계량/수식/재고 도메인 단위 테스트

- **Feature**: `test-coverage-weighing`
- **작성일**: 2026-06-01
- **레벨**: Dynamic
- **PDCA Phase**: Plan

## 1. 배경 / 문제 정의

현재 `tests/`에는 13개 테스트 파일이 있으나 **출입관리·라우터 통합·스모크**에
집중되어 있고, 계량·수식·재고 계산을 담당하는 **순수/도메인 서비스 함수에 대한
격리 단위 테스트가 사실상 없다.** 대상 모듈:

- `cell_value_parser` — Excel 셀의 숫자/수식/혼합텍스트 분리 (`parse_cell`)
- `import_parser` — 붙여넣기 텍스트 → 레시피 파싱 (`parse_import_text`, `_parse_value`)
- `stock_service` — 재고 차감/입고/조정/폐기/잔량상태 (`deduct_for_measurement`, `restock`, …)
- `material_resolver` — 자재명 정규화·ID 해석 (`resolve_material`)
- `recipe_helpers` — 표시값 조합 (`format_display_value`)

이 함수들은 잘못 동작해도 **조용히 틀린 계량/재고값을 산출**할 수 있는데
회귀를 잡아줄 단위 테스트가 없다. 또한 **커버리지 측정 설정(pytest-cov)이
프로젝트에 없어**(setup.cfg/pytest.ini/pyproject.toml 부재) "어디가 검증
안 됐는지" 객관적 측정이 불가능하다.

> ※ `pytest-cov 7.1.0`은 환경에 설치돼 있으나 **설정·문서·실사용이 없음.**

## 2. 목표 (Success Criteria)

1. **커버리지 측정 기반 구축**
   - `setup.cfg` 신설 → `[tool:pytest]` + `[coverage:run]`/`[coverage:report]`
   - `requirements-dev.txt`에 `pytest-cov` 명시
   - term-missing + HTML 리포트가 명령 한 줄로 가능, 측정법 문서화
2. **대상 도메인 모듈 단위 테스트 작성**
   - 순수 함수는 직접 호출, DB 함수는 **in-memory SQLite**로 검증
3. **계량 핵심 3개 모듈(cell_value_parser/material_resolver/stock_service) 라인 커버리지 ≥ 90%**
4. 신규 테스트 포함 **전체 테스트 그린**(기존 회귀 0)

## 3. 범위 (Scope)

### In
- `setup.cfg` 신설, `requirements-dev.txt` 수정
- 위 5개 모듈 단위 테스트 신규 작성
- 작성 중 발견되는 **명백한 계산 버그 수정**
- 커버리지 측정 방법 문서화

### Out
- `recipe_helpers`의 DB 의존 함수(`fetch_recipe_items`, `find_chain_root`,
  `fetch_chain`, `ensure_material`) — 후속 통합 테스트
- `import_parser`의 복잡한 헤더 자동추론 엣지케이스 100% 커버 (핵심 경로만)
- 라우터/미들웨어 커버리지 확대, CI 게이트(`--cov-fail-under`) — 후속

## 4. 핵심 기능 우선순위

| 우선순위 | 항목 | 사유 |
|---|---|---|
| P0 | `parse_cell` "마지막 숫자 우선"/혼합텍스트 | 모든 레시피 값 진입점 |
| P0 | `deduct_for_measurement` 멱등 차감 | 재고 차감 정확성·중복 방지 |
| P1 | `restock/discard/adjust` + `stock_status` | 입출고·잔량 판단 |
| P1 | `resolve_material` 이름/별칭 해석 | 자재 매칭 |
| P2 | `parse_import_text` happy path/carry-over | 그리드→레시피 |
| P2 | `format_display_value` | 표시 |

## 5. 리스크

- 단위 테스트가 기존 잠재 버그를 드러낼 수 있음 → 발견 시 수정(Act)
- `setup.cfg` 신설이 기존 `python -m pytest tests` 동작을 바꾸지 않도록 `testpaths=tests` 고정

## 6. 산출물

- `setup.cfg` (신규)
- `requirements-dev.txt` (수정)
- `tests/test_cell_value_parser.py` / `test_import_parser.py` /
  `test_stock_service.py` / `test_material_resolver.py` /
  `test_recipe_helpers_pure.py` (신규)
- 필요 시 `src/services/*.py` 버그 수정

➡️ Next: `/pdca design test-coverage-weighing`
