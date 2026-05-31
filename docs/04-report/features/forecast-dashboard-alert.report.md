# 완료 보고서 — forecast-dashboard-alert (운영 대시 발주 임박 알림)

> R2 추가기능 PDCA 완료 · 2026-06-01 · Match Rate 99% · 71/71 pytest

## 1. 개요

material-forecast(0499ece) 후속 R2 기능. report §7 차기 후보 ①(발주 임박 능동 알림)의
**가치**를, 취약한 트레이 클라이언트 대신 **안전한 웹 스택(운영 대시 상단 알림 카드)** 으로
실현하고, 후보 ④(소비쿼리 인덱스)를 같은 사이클에 통합했다.

## 2. 변경 사항

| 영역 | 파일 | 내용 |
|------|------|------|
| 서비스 | `src/services/forecast_service.py` | `forecast_alert()` 헬퍼 — compute_forecast 재사용, urgent/soon 상위 N 압축 |
| API | `src/routers/dashboard_routes.py` | `GET /api/dashboard/forecast-alert` (manager) |
| 마이그레이션 | `src/db/migrations.py` | `idx_stock_logs_reason_created (reason, created_at)` |
| 템플릿 | `templates/dashboard.html` | 발주 임박 알림 섹션(기본 hidden) |
| 프런트 | `static/js/dashboard.js` | `loadForecastAlert()` — 0건 미노출, 상위 항목 렌더 |
| 스타일 | `static/css/dashboard.css` | 알림 카드 + 상태 배지(대시 미로드분 복제) |
| 테스트 | `tests/test_forecast_dashboard_alert.py` | 단위/라우트 6건(A1~A6) |

## 3. 핵심 설계 결정

- **트레이 대신 웹**: 메모리 `feedback_tts_pyinstaller_failure`(트레이 PyInstaller 회귀)
  근거로, 같은 가치를 회귀 위험 낮은 manager 대시보드 카드로 전달.
- **로직 무중복**: 정렬·상태 판정은 `compute_forecast`에 위임하고 얇은 요약 헬퍼만 신설.
- **인덱스 정합**: 소비쿼리는 material_id 무조건 스캔 → 기존 (material_id, created_at) 미사용 →
  (reason, created_at)로 정확히 지원.
- **노이즈 제로**: 발주 권장 0건이면 카드 미노출(메모리 `feedback_alert_semantics` 정합).

## 4. QA (브라우저 스모크)

격리 임시 DB(`IRMS_DATA_DIR=tmp_qa_dashalert`, 데모 시드) + manager 세션(120206/함지안) +
Playwright. urgent 1(BYK-199) + soon 1(카본블랙) 소비 이력 주입 후 실검증.

| 항목 | 결과 |
|------|------|
| 로그인 → `/dashboard` 알림 카드 렌더 | PASS (2행, 긴급 먼저) |
| `GET /api/dashboard/forecast-alert` (세션) | 200, reorder 2 / urgent 1 / soon 1 |
| 계산 정확성 | BYK-199: 30g÷10 = **3일 urgent**, 발주 **270g**(=10×30−30), 소진 2026-06-03 ✓ / 카본블랙: 100g÷10 = **10일 soon**, 발주 **200g**, 소진 2026-06-10 ✓ |
| 요약 문구 | "발주 권장 2건 (긴급 1, 임박 1)" ✓ |
| 0건 케이스(이력 삭제 후) | reorder=0 → **cardHidden=true** ✓ (G3) |
| 대시보드 콘솔 오류 | 0건 |

## 5. 성과 지표

- 단위/라우트 6건 + 전체 회귀 **71/71** 통과(material-forecast 시점 65 → +6, 회귀 0).
- Match Rate **99%**.

## 6. 운영자 확인 필요

- 알림 노출 기준은 forecast의 리드타임/커버리지 파라미터를 그대로 따른다 →
  material-forecast의 운영자 초기 설정(리드타임/목표 커버리지)과 연동.
- 상위 표시 건수 기본 5 — 현장 자재 수 대비 적정성 검토.

## 7. 향후 과제 (Out of Scope → 차기 후보)

- 트레이/OS 토스트 푸시(별도 PDCA, material-forecast report §7 ①)
- 발주서 생성·ERP 연동(②)
- 계절성·요일 가중 모델(③)
- 알림 카드에서 자재별 상세 모달 인라인 연동(현재는 management 탭 링크)
