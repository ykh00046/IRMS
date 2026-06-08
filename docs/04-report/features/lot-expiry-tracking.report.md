# 완료 보고서 — lot-expiry-tracking (자재 LOT·유통기한 관리)

> PDCA 완료 2026-06-02 · Match Rate **99%** · 164 passed (회귀 0) · Level: Dynamic

## 1. 요약

잉크·경화제·첨가제 등 유통기한이 있는 화학 자재를 **입고 LOT 단위**로 추적하고,
만료·임박 LOT을 운영 대시(`/dashboard`) 진입 시 즉시 인지시키는 기능을 추가했다.
변질 자재의 레시피 투입(품질 사고)과 기한 임박분 방치로 인한 폐기 손실을 예방한다.

검증된 `forecast-dashboard-alert` + `stock_service` 패턴을 답습한 **가산적 설계**로,
`materials.stock_quantity` 및 계량 차감 경로(`stock_service.deduct_for_measurement`)는
**전혀 건드리지 않았다**. LOT은 독립된 유통기한·추적 레이어다.

## 2. PDCA 흐름

| 단계 | 산출물 | 결과 |
|------|--------|------|
| Plan | `docs/01-plan/features/lot-expiry-tracking.plan.md` | 목표 5·성공기준 8·비목표 명시 |
| Design | `docs/02-design/features/lot-expiry-tracking.design.md` | 데이터/서비스/API/프런트/테스트 설계 |
| Do | 서비스·라우터·마이그·프런트·테스트 구현 | app import OK, 13 테스트 통과 |
| Check | `docs/03-analysis/lot-expiry-tracking.analysis.md` | gap-detector Match 99% |
| Act | `no_expiry` 배지 중립화(설계 일치) | 편차 1건 해소 → 100% |
| QA | 서비스 생명주기 + 인증 라우터 통합 | 전 항목 통과 |
| Report | 본 문서 | 완료 |

## 3. 변경 파일

**신규**
- `src/services/lot_service.py` — 상태판정/등록/소진/폐기/목록/대시집계
- `src/routers/lot_routes.py` — operator 조회 + manager 쓰기 + CSV(튜플 반환)
- `static/js/lot.js` — 유통기한·LOT 탭 컨트롤러
- `tests/test_lot_expiry_tracking.py` — 13 테스트(L1~L9)
- `docs/01-plan|02-design|03-analysis` 각 문서

**수정(가산적)**
- `src/db/migrations.py` — `material_lots` 테이블 + 인덱스 2종 + `_ALLOWED_TABLES`
- `src/routers/models.py` — `LotCreateBody`/`LotConsumeBody`/`LotDiscardBody`
- `src/routers/api.py` — `lot_op_router`/`lot_mgr_router` include
- `src/routers/dashboard_routes.py` — `GET /dashboard/expiry-alert`
- `templates/management.html` — "유통기한·LOT" 탭 + 모달 2종
- `templates/dashboard.html` — 만료 임박 카드
- `static/js/dashboard.js` — `loadExpiryAlert()`

## 4. API

| Method | Path | 권한 | 설명 |
|--------|------|------|------|
| GET | `/api/materials/lots` | operator | 전체 active LOT + 상태 |
| GET | `/api/materials/{id}/lots` | operator | 자재별(소진·폐기 포함 옵션) |
| POST | `/api/materials/{id}/lots` | manager | LOT 등록 |
| POST | `/api/lots/{lot_id}/consume` | manager | 소진(잔여 0→depleted) |
| POST | `/api/lots/{lot_id}/discard` | manager | 폐기(사유 필수) |
| GET | `/api/lots/export` | manager | CSV(수식 인젝션 방어) |
| GET | `/api/dashboard/expiry-alert` | manager | 만료/임박 상위 N 요약 |

## 5. 핵심 설계 결정

1. **재고와 분리**: stock_quantity·계량 차감 경로 무변경. 결합도 0, 회귀 리스크 최소. 추후 FIFO 통합은 별도 PDCA.
2. **유통기한 상태 4분류**: 만료(expired) / 임박(expiring_soon, 기본 30일) / 정상(ok) / 무기한(no_expiry).
3. **노이즈 제로 대시**: 만료+임박만, 0건이면 카드 미노출(`forecast-dashboard-alert` 정책 일관).
4. **권한 분리**: 조회 operator(선입선출 판단) / 쓰기·대시 manager(`stock_routes` 정책 답습).
5. **CSRF**: 쓰기는 `csrftoken` 쿠키→`x-csrftoken` 헤더(메모리 `project_management_csrf`).

## 6. 동시 작업 주의 (중요)

본 기능 구현 중, **다른 세션이 동시에 `order-sheet-erp`(발주서·ERP 전송) 기능을 같은
저장소에서 개발 중**이었음. 공유 파일(`migrations.py`, `models.py`, `api.py`,
`schema.py`, `config.py`)을 양측이 편집했으나 **테이블/서비스/라우터가 완전히 분리**되어
논리적 충돌은 없음. 본 작업은 각 공유 파일 편집 직전 재읽기로 동시 변경분(purchase_orders 등)을
보존하며 진행함. 커밋 전 `git diff`로 양 기능 변경이 모두 온전한지 최종 확인 필요.

## 7. 차기 후보

1. **FIFO 자동 차감 통합** — 계량 시 가장 임박한 LOT부터 자동 소진(현재 수동 소진).
2. **입고 시 LOT 등록 연계** — `원재료 재고` 입고 버튼에서 LOT 동시 등록.
3. **만료 트레이/푸시 알림** — 웹 카드에 더해 능동 푸시(트레이 회귀 이력 주의).
4. **바코드/QR LOT 스캔 입력** — 현장 입력 효율.

## 8. 메트릭

- 테스트: 13 신규 / 전체 **164 passed**, 회귀 0
- Match Rate: 99% (편차 1건 Act 해소 → 실질 100%)
- 신규 LOC: 서비스 ~290, 라우터 ~210, JS ~300, 테스트 ~260
