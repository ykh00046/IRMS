# Design: 테스트 커버리지 기반 + 계량/수식/재고 단위 테스트

- **Feature**: `test-coverage-weighing`
- **작성일**: 2026-06-01
- **PDCA Phase**: Design
- **참조 Plan**: `docs/01-plan/features/test-coverage-weighing.plan.md`

## 1. 아키텍처 개요

```
pytest 8.x + pytest-cov 7.1.0 ──▶ coverage.py
  측정 대상(src/services):
    cell_value_parser.py   parse_cell, _is_number            (순수)
    import_parser.py        _parse_value, parse_import_text    (순수+DB)
    stock_service.py        stock_status / restock / discard / adjust /
                            deduct_for_measurement / reverse_measurement /
                            set_threshold / list_stock / list_logs  (순수+DB)
    material_resolver.py    normalize_material_name / resolve_material /
                            resolve_materials_bulk             (순수+DB)
    recipe_helpers.py       format_display_value               (순수만 대상)

tests/ (신규 5파일)
  - 순수 함수: from src.services... import 직접 호출
  - DB 함수: 각 테스트 파일에서 sqlite3 ":memory:" + 최소 스키마 생성
             (기존 test_material_forecast.py의 _make_db 패턴 준용)
```

`conftest.py`(프로젝트 루트)가 이미 `IRMS_ENV=test` 등 환경을 잡고 루트를
`sys.path`에 추가하므로 import 경로 문제 없음.

## 2. 설정 설계

### 2.1 setup.cfg (신규)
프로젝트에 pytest 설정이 전무 → 신설. 기존 호출 호환 위해 `testpaths=tests` 고정.

```ini
[tool:pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

[coverage:run]
source = src
omit =
    src/main.py
    */__init__.py
    tests/*

[coverage:report]
show_missing = True
skip_covered = False
precision = 1
```

> `--cov`를 `addopts`로 강제하지 않음(기존 `pytest` 호출 호환 + 속도).

### 2.2 requirements-dev.txt
`pytest-cov>=5.0.0` 추가.

### 2.3 측정 명령 (문서화)
```bash
# 도메인 모듈 커버리지 — dotted 모듈 경로 사용(슬래시 경로는 미인식)
python -m pytest tests/test_cell_value_parser.py tests/test_import_parser.py \
  tests/test_stock_service.py tests/test_material_resolver.py \
  tests/test_recipe_helpers_pure.py \
  --cov=src.services.cell_value_parser --cov=src.services.import_parser \
  --cov=src.services.stock_service --cov=src.services.material_resolver \
  --cov=src.services.recipe_helpers --cov-report=term-missing

# 전체 + HTML
python -m pytest --cov=src --cov-report=html --cov-report=term-missing
```

> ⚠️ 설계 노트: `--cov=src/services/x`(슬래시)는 coverage가 모듈로 인식 못 해
> "module-not-imported" 경고 + 데이터 0. 반드시 **dotted 경로**(`src.services.x`).

## 3. 테스트 케이스 설계 (실제 함수 기준)

### 3.1 cell_value_parser.parse_cell (P0)
None/빈문자/공백 → (None,None) · `"-"` → (None,"-") · 순수숫자 · 수식(`=`)은 텍스트
보존 · `"12.50 (HR10)"`→(12.5,"(HR10)") · `"APB(17) 360"`→(360,"APB (17)") ·
하이픈코드 `BYK-199` 비분리 · "마지막 숫자 우선" · `_is_number` 헬퍼.

### 3.2 stock_service (P0/P1, in-memory DB)
- `stock_status`: negative/low(임계 이하)/ok/임계0
- `restock`: 잔량 증가+로그, 0이하 ValueError
- `discard`: 차감, note 필수, 0이하 ValueError
- `adjust`: 절대수량 설정(delta 계산), note 필수
- `deduct_for_measurement`: 차감+로그 / weight 0 skip / **recipe_item_id 멱등(중복 차감 방지)** / 음수잔량 negative 플래그
- `reverse_measurement`: 환원+로그삭제 / 없으면 noop
- `set_threshold`: 설정 / 음수 ValueError
- `list_stock`/`list_logs`: 상태 포함 조회

### 3.3 material_resolver (P1, in-memory DB)
- `normalize_material_name`: None/빈→"" / 대문자·trim / 내부공백 축약
- `resolve_material`: 이름 대소문자 무시 / 비활성 제외 / 별칭 매칭 / 미지명 None / 빈이름 None
- `resolve_materials_bulk`: 배치 매핑

### 3.4 import_parser (P2)
- `_parse_value`: `"-"`→(None,None) skip / 빈·None / 천단위콤마 제거 / 순수숫자 / 혼합
- `parse_import_text`(in-memory DB): 빈입력 에러 / happy path 2항목 / `-`셀 스킵 /
  제품명 carry-over / 비고 컬럼 / 신규자재 자동등록 경고 / 필드누락 에러

### 3.5 recipe_helpers.format_display_value (P2)
(weight,text) 조합 4가지 경우.

## 4. 검증/완료 기준

1. 신규 테스트 전부 PASS
2. 계량 핵심 3모듈(cell_value_parser/material_resolver/stock_service) ≥ 90%
3. import_parser는 핵심 파싱 경로 커버(헤더 추론 엣지는 범위 외)
4. 전체 회귀 0, HTML/term 리포트 산출

## 5. 변경 파일 목록

- `setup.cfg`(신규), `requirements-dev.txt`(수정)
- `tests/test_cell_value_parser.py` / `test_import_parser.py` /
  `test_stock_service.py` / `test_material_resolver.py` /
  `test_recipe_helpers_pure.py` (신규)
- `src/services/*.py` (버그 발견 시)

➡️ Next: `/pdca do test-coverage-weighing`
