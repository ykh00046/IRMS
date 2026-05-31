# 자재 소모량 예측·발주 추천 — Completion Report

| 항목 | 값 |
|------|------|
| Feature | `material-forecast` |
| Phase | Report (PDCA 완료) |
| Level | Dynamic |
| 완료일 | 2026-06-01 |
| Match Rate | **~99%** |
| 품질 점수 | **88/100** (Critical/High 0건) |
| pytest | **65/65 passed** (4.0s) — 기존 54 + 신규 11 |
| 선행 사이클 | material-stock-tracking (2026-04) |

## 1. 요약

`material-stock-tracking`이 매 계량마다 `material_stock_logs`에 축적해 온 **소모 이력을 활용**해,
원재료별 **일평균 소모량 → 예상 소진일 → 권장 발주량 → 발주 긴급도**를 산출하는 예측·발주 추천 기능을 추가했다.
당시 명시적 Out of Scope였던 "소비 추세 예측"과 "발주 추천"을 정확히 구현했다.

기존 재고/계량 로직은 **읽기만** 하므로 회귀 0건. 신규 라우터/서비스 추가 + materials 컬럼 2개로 구성.

## 2. 산출물

| 파일 | 구분 | 역할 |
|------|------|------|
| `src/services/forecast_service.py` | 신규 | 예측 계산 엔진(순수 함수): 이동평균·소진일·권장량·상태 |
| `src/routers/forecast_routes.py` | 신규 | `GET /forecast/materials`, `GET /forecast/export`, `PATCH /materials/{id}/forecast-params` (manager) |
| `src/routers/models.py` | 수정 | `ForecastParamsBody` (lead/cycle, ge=0) |
| `src/routers/api.py` | 수정 | forecast 라우터 등록 |
| `src/db/schema.py` | 수정 | materials: `lead_time_days`, `reorder_cycle_days` |
| `src/db/migrations.py` | 수정 | `ensure_column` 2개 (멱등) |
| `templates/management.html` | 수정 | "소모예측·발주" 탭 + 패널 + 요약 배너 + 파라미터 모달 |
| `static/js/forecast.js` | 신규 | 조회/필터/내보내기/파라미터 설정 UI |
| `tests/test_material_forecast.py` | 신규 | 11개 테스트 (서비스 10 + 라우트 1) |

## 3. 예측 모델 (확정)

- **데이터원**: `material_stock_logs` 중 `reason='measurement'` 음수 delta (계량 단일 진실원, 취소분 자동 제외)
- **일평균**: `후행 N일 총소모 / N` (기본 30일, 60/90 선택). 단순이동평균 — 현장 설명가능성 우선
- **잔여일수**: `현재고 / 일평균` (소모 0이면 `no_data`로 0 나눗셈 회피)
- **권장 발주량**: `max(0, 일평균 × 목표커버리지 − 현재고)`
- **상태**: `urgent`(리드타임 내 소진) / `soon`(리드타임×1.5 내) / `ok` / `no_data`
- **파라미터**: 원재료별 리드타임(기본 7일)·목표커버리지(기본 30일), 0이면 전역 기본값

### 검증된 계산 예 (스모크)
현재고 45g, 30일 270g 소모(9g/일) → 잔여 5일, 소진 예상 2026-06-05, 권장 발주 **225g**, 상태 **urgent** ✓

## 4. PDCA 사이클 결과

| 단계 | 결과 |
|------|------|
| **Plan** | 문제정의·범위·성공기준 6항·자율결정 6항 확정 |
| **Design** | 스키마/알고리즘/API/UI/권한/테스트/변경파일 설계 |
| **Do** | 9개 파일 구현, 마이그레이션·라우트 등록 검증 |
| **Check (Gap)** | gap-detector Match 98% → 보강 후 ~99% |
| **Check (Quality)** | code-analyzer 88/100, Critical/High 0, Low 2건 반영 |
| **Iterate** | T7 라우트 테스트 추가, 설계서 정합화, 보안 Low 2건 수정 |
| **QA** | pytest 65/65, 기능 수치 정확, 템플릿 컴파일 OK |

## 5. 권한·보안

- 모든 forecast 엔드포인트 **manager 이상**(operator 403). PATCH는 audit log 기록.
- 입력검증: `window_days` Query(ge=7, le=365), lead/cycle Field(ge=0) + 서비스 재검증.
- XSS: forecast.js 전 필드 `escapeHtml`. CSV 수식 인젝션 방어(`_csv_safe`).

## 6. 성공 기준 충족

| # | 기준 | 충족 |
|---|------|:----:|
| 1 | 일평균·예상 소진일 정확 산출 | ✅ |
| 2 | 권장 발주량·상태(urgent/soon/ok) 산출 | ✅ |
| 3 | 이력 없는 자재 `no_data`, 0 나눗셈 없음 | ✅ |
| 4 | 발주 임박 우선 표시 + CSV 내보내기 | ✅ |
| 5 | operator 차단, manager 전용 | ✅ |
| 6 | 기존 기능 회귀 0 (전체 pytest 통과) | ✅ |

## 7. 향후 과제 (Out of Scope → 차기 후보)

- 트레이 클라이언트 발주 임박 푸시 알림 (별도 PDCA)
- 외부 구매/ERP 연동 자동 발주
- 계절성·요일 가중 등 고도화 모델
- `material_stock_logs` 대용량 대비 `created_at` 인덱스

## 8. 운영자 확인 필요

- 원재료별 **리드타임/목표 커버리지** 초기 설정(미설정 시 7일/30일 기본 적용)
- 현장 발주 주기와 분석기간(기본 30일) 정합성 검토

## 9. 브라우저 스모크 QA 및 결함 수정 (2026-06-01 추가)

격리 임시 DB(`IRMS_DATA_DIR=tmp_qa_forecast`, 데모 시드) + 샘플 소비 이력 90건 주입 후
Playwright로 **인증된 manager 세션(120206/함지안)** 전 구간을 실제 검증했다.

### 검증 결과 (PASS)
| 항목 | 결과 |
|------|------|
| 로그인 → `/management` → "소모예측·발주" 탭 렌더 | PASS (6행, 긴급순 정렬) |
| `GET /api/forecast/materials` (세션) | 200, summary 정확 (urgent 1 / ok 2 / no_data 3) |
| 계산 정확성 | BYK-199: 재고 30g ÷ 10g/일 = **3일 urgent**, 발주 270g (=10×30−30), 소진예상 2026-06-03 ✓ |
| 요약 배너 | "⚠ 발주 권장 1건 (긴급 1, 임박 0)" 노출 ✓ |
| `GET /forecast/export?only_reorder=true` | 200, `text/csv`, `attachment; filename="irms-forecast-2026-06-01.csv"`, urgent 행만 포함 ✓ (분석 L1 라이브 보강) |

### 🐛 결함 발견 및 수정 — forecast.js CSRF 누락
- **증상**: "설정"(발주 파라미터) 모달 저장 시 `PATCH /api/materials/{id}/forecast-params`가 **403**. 모달이 닫히지 않고 오류 표시 → 파라미터 저장 기능 **동작 불가**.
- **원인**: `forecast.js`의 `submitModal()`이 원시 `fetch`로 `x-csrftoken` 헤더 없이 PATCH 전송. Management 화면에는 공통 CSRF 래퍼(`IRMS.request`)가 로드되지 않아(`admin_users.js`와 동일 상황) 헤더를 직접 붙여야 함. (라이브 대조: 헤더 없음=403, 헤더 있음=200)
- **수정**: `csrftoken` 쿠키를 읽어 `x-csrftoken` 헤더로 전송하는 `csrfToken()` 헬퍼 추가(`admin_users.js` 패턴 답습), `submitModal()` 헤더에 적용.
- **재검증**: 패치된 JS 로드 후 실제 모달로 BYK-199 커버리지 90 설정 → **200, 모달 닫힘, 영속**(발주량 870g=10×90−30으로 재계산) ✓. 전체 pytest **65/65** 유지.

> 본 결함은 단위/통합 테스트(서버 직접 호출)로는 드러나지 않고 **브라우저 세션 + CSRF 미들웨어** 경로에서만 발생 → 메모리 `feedback_browser_smoke_pattern`의 실효성 재확인.
