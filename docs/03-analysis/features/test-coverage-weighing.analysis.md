# Gap 분석: test-coverage-weighing

- **Feature**: `test-coverage-weighing`
- **작성일**: 2026-06-01
- **PDCA Phase**: Check
- **참조**: Plan / Design 문서

## 1. 설계 대비 구현 매칭

| Design 항목 | 구현 | 상태 |
|---|---|---|
| `setup.cfg` 신설(pytest+coverage) | 생성 완료 | ✅ |
| `requirements-dev.txt` pytest-cov 추가 | 추가 완료 | ✅ |
| dotted 경로 측정 + 문서화 | 본 보고 §5, design §2.3 | ✅ |
| cell_value_parser 단위 테스트 | `tests/test_cell_value_parser.py` | ✅ |
| stock_service 단위 테스트(in-memory DB) | `tests/test_stock_service.py` | ✅ |
| material_resolver 단위 테스트(in-memory DB) | `tests/test_material_resolver.py` | ✅ |
| import_parser 단위 테스트 | `tests/test_import_parser.py` | ✅ |
| recipe_helpers 순수함수 테스트 | `tests/test_recipe_helpers_pure.py` | ✅ |
| 계량 핵심 3모듈 ≥ 90% | 100/100/96.2% | ✅ |
| 전체 테스트 회귀 0 | 116 passed | ✅ |

## 2. 측정 결과 (검증됨)

```
신규 단위 테스트 : 67개 (5개 신규 파일)
전체 스위트       : 116 passed (기존 49 + 신규 67), 회귀 0, 19.9s

커버리지(대상 모듈):
  cell_value_parser.py   100.0%   (42/42)     ← 계량 핵심 ✅
  material_resolver.py   100.0%   (17/17)     ← 계량 핵심 ✅
  stock_service.py        96.2%   (79, 3 miss) ← 계량 핵심 ✅
  import_parser.py        83.4%   (163, 27 miss)
  recipe_helpers.py       46.9%   (순수 format_display_value만 100%, DB함수 제외)
```

## 3. 발견 사항 (버그)

이번 사이클에서 **명백한 계산 버그는 발견되지 않았다.** 대상 도메인 함수들은
설계대로 동작했다(멱등 차감, 음수잔량 플래그, "마지막 숫자 우선" 규칙,
별칭 해석 등 모두 기대값 일치). 단위 테스트는 **현재 동작을 회귀 기준선으로
고정(characterization)** 하는 안전망 역할을 한다.

> 정직성 노트: 본 작업 중간에 컨텍스트 손실로 `consume_fifo`/`compute_balance`
> 같은 **존재하지 않는 함수**를 가정한 초안이 있었으나, 실제 소스 확인 후
> 전량 폐기하고 실제 함수(`deduct_for_measurement` 등) 기준으로 재작성했다.

## 4. 잔여 갭 / 한계 (의도된 범위)

- **`stock_service` 미커버 3줄(94,140,146)**: `deduct_for_measurement`의
  `row is None`(자재 부재) 가드, `_apply_delta`의 `invalid reason`/`material not found`
  방어 분기 — 정상 흐름 밖 예외 경로. 핵심 계량 로직은 100% 커버.
- **`import_parser` 83.4%**: 미커버 27줄은 대부분 **헤더 자동추론 엣지케이스**
  (hybrid 헤더 감지, position/product_name 위치 역추론, 자재 첫 사용 경고 분기).
  Plan §3(Out)에서 "복잡한 헤더 자동추론 100% 커버"를 범위 외로 명시. 핵심
  파싱 경로(_parse_value, happy path, carry-over, 비고, 신규자재, 필드누락)는 커버.
- **`recipe_helpers` 46.9%**: 미커버는 전부 DB 함수(Plan §3 Out). 순수
  `format_display_value`는 100%.

## 5. 측정 방법 (재현)

```bash
pip install -r requirements-dev.txt
python -m pytest tests/test_cell_value_parser.py tests/test_import_parser.py \
  tests/test_stock_service.py tests/test_material_resolver.py \
  tests/test_recipe_helpers_pure.py \
  --cov=src.services.cell_value_parser --cov=src.services.import_parser \
  --cov=src.services.stock_service --cov=src.services.material_resolver \
  --cov=src.services.recipe_helpers --cov-report=term-missing
# 전체 + HTML
python -m pytest --cov=src --cov-report=html --cov-report=term-missing
```

## 6. 매칭률

**96%** — 설계 10개 항목 중 10개 충족. 커버리지 목표는 "계량 핵심 3모듈 ≥90%"로
정의했고 100/100/96.2%로 초과 달성. import_parser/recipe_helpers의 미달분은
Plan에서 명시적으로 범위 제외한 영역.

➡️ Next: `/pdca report test-coverage-weighing`
