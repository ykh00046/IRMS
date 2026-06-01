# 완료 보고서: test-coverage-weighing

- **Feature**: `test-coverage-weighing`
- **완료일**: 2026-06-01
- **레벨**: Dynamic
- **최종 매칭률**: 96%
- **PDCA**: Plan → Design → Do → Check → Act → Report ✅

## 1. 개요

테스트 커버리지를 **수치로 측정할 기반(pytest-cov + setup.cfg 신설)** 을 구축하고,
계량/수식/재고 도메인의 서비스 함수에 대한 **단위 테스트 67개**(신규 5파일)를 작성했다.
순수 함수는 직접, DB 의존 함수는 **in-memory SQLite**로 검증했다.

## 2. 산출물

| 파일 | 변경 | 내용 |
|---|---|---|
| `setup.cfg` | 신규 | `[tool:pytest]` + `[coverage:run]`/`[coverage:report]` |
| `requirements-dev.txt` | 수정 | `pytest-cov>=5.0.0` 추가 |
| `tests/test_cell_value_parser.py` | 신규 | 셀 값/수식 파싱 |
| `tests/test_stock_service.py` | 신규 | 재고 차감/입고/조정/폐기/멱등 |
| `tests/test_material_resolver.py` | 신규 | 자재명 정규화·해석 |
| `tests/test_import_parser.py` | 신규 | 붙여넣기 파싱·carry-over·비고 |
| `tests/test_recipe_helpers_pure.py` | 신규 | 표시값 조합 |
| `docs/01~04/...` | 신규 | PDCA 문서 4종 |

## 3. 핵심 지표

```
신규 단위 테스트 : 67개 (5개 신규 파일)
전체 스위트       : 116 passed (기존 49 + 신규 67), 회귀 0, 19.9s

커버리지(검증됨):
  cell_value_parser.py   100.0%   ← 계량 핵심
  material_resolver.py   100.0%   ← 계량 핵심
  stock_service.py        96.2%   ← 계량 핵심
  import_parser.py        83.4%   (핵심 파싱 경로 커버, 헤더 추론 엣지는 범위 외)
  recipe_helpers.py       46.9%   (순수 format_display_value 100%, DB함수 범위 외)
```

목표 "계량 핵심 3모듈 ≥ 90%" → **100/100/96.2%로 초과 달성**.

## 4. 버그 / 발견

- **명백한 계산 버그는 발견되지 않음.** 대상 함수들은 설계대로 동작(멱등 차감,
  음수잔량 플래그, "마지막 숫자 우선", 별칭 해석 등 기대값 일치).
- 단위 테스트는 현재 동작을 **회귀 기준선으로 고정**하는 안전망으로 기능.

> 정직성 노트: 작업 중 컨텍스트 손실로 존재하지 않는 함수(`consume_fifo` 등)를
> 가정한 초안이 한 차례 만들어졌으나, 실제 소스 확인 후 전량 폐기·재작성했다.
> 모든 테스트는 실제 함수 시그니처에 대해 PASS함을 실행으로 검증했다.

## 5. 커버리지 측정 방법 (운영 가이드)

```bash
pip install -r requirements-dev.txt

# 도메인 모듈 커버리지 — 반드시 dotted 모듈 경로(src.services.x) 사용
python -m pytest tests/test_cell_value_parser.py tests/test_import_parser.py \
  tests/test_stock_service.py tests/test_material_resolver.py \
  tests/test_recipe_helpers_pure.py \
  --cov=src.services.cell_value_parser --cov=src.services.import_parser \
  --cov=src.services.stock_service --cov=src.services.material_resolver \
  --cov=src.services.recipe_helpers --cov-report=term-missing

# 전체 + HTML 리포트(htmlcov/index.html)
python -m pytest --cov=src --cov-report=html --cov-report=term-missing
```

> ⚠️ `--cov=src/services/x`(슬래시)는 coverage가 모듈로 인식 못 해 데이터 0.
> 반드시 점(.) 표기.

## 6. 한계 / 후속 권장

1. **recipe_helpers DB 함수**: in-memory SQLite 통합 테스트로 확대(후속).
2. **import_parser 헤더 자동추론**: hybrid/위치역추론 엣지케이스 보강(후속).
3. **라우터/미들웨어 계층**: 별도 PDCA로 커버리지 확대.
4. **CI 게이트**: 안정화 후 `--cov-fail-under=80` 등 회귀 자동 차단 검토.

## 7. 학습 (Learnings)

- 순수 함수는 즉시, DB 함수는 in-memory SQLite로 — 둘 다 빠르게 단위 테스트 가능.
  기존 `test_material_forecast.py`의 `_make_db()` 패턴이 재사용 가능한 표준.
- pytest-cov는 `--cov` 인자에 **반드시 dotted 모듈 경로**. 슬래시 경로는 조용히
  데이터 0(경고만) → 측정 결과를 항상 눈으로 확인할 것.
- **CLAUDE.md 부정확**: `material_resolver.py`는 실재하나, 컨텍스트 추정만으로
  함수명을 가정하면 안 됨. 코드를 먼저 읽을 것(이번에 두 번 교정).

## 8. PDCA 요약

| Phase | 결과 |
|---|---|
| Plan | 목표/범위/성공기준 정의(계량 핵심 3모듈 90%) |
| Design | setup.cfg/cov 설정 + 실제 함수 기준 케이스 설계 |
| Do | pytest-cov 도입 + 테스트 5파일 작성 |
| Check | 실제 소스 대비 검증, 버그 미발견, 커버리지 측정 |
| Act | import_parser 케이스 보강(75.5→83.4%), stale 문서 전면 교정 |
| Report | 본 문서 (Match 96%) |

🎉 **Feature 완료** — 최종 매칭률 96%, 전체 116 passed
