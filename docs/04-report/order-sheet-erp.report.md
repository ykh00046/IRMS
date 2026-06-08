# 발주서 생성·ERP 연동 (order-sheet-erp) — 완료 보고서

| 항목 | 값 |
|------|------|
| Feature | `order-sheet-erp` |
| Level | Dynamic |
| 기간 | 2026-06-02 (Plan→Design→Do→Check→QA→Report 1사이클) |
| Match Rate | **99%** (gap-detector) |
| 테스트 | 단위/통합 13 + 라이브 QA 24 = **전부 통과**, 전체 스위트 **151 passed** |
| 상태 | ✅ 완료 |
| 선행 | `material-forecast`, `forecast-dashboard-alert` (둘 다 2026-06-01 완료) |

## 1. 개요

`material-forecast`가 산출하는 **권장 발주량·긴급도**를 입력으로 받아, 발주 사이클을
완결하는 기능. forecast 결과를 **발주서로 스냅샷 확정 → 수량 검토/조정 → Excel/인쇄(PDF)
출력 → ERP 전송**까지 한 흐름으로 처리한다. 기존의 이중 입력(forecast CSV를 보고 ERP에
수기 재입력)과 발주 이력 부재 문제를 해소한다.

## 2. 구현 결과

### 2.1 데이터 모델 (신규 2테이블)
- `purchase_orders` — 발주서 헤더(발주번호 `PO-YYYYMMDD-NNN`, 상태머신, ERP 전송 결과).
  `CHECK(status IN draft/sent/failed/cancelled)`로 DB 레벨 무결성.
- `purchase_order_items` — 생성 시점 **스냅샷**(자재명/현재고/일평균/잔여일수/예상소진일/
  긴급도/권장량/발주량). 이후 재고 변동과 무관하게 발주 결정 보존.

### 2.2 백엔드
- `services/order_service.py` — 채번·생성·조회·수정·취소·전송반영·Excel·payload 직렬화.
  스냅샷 생성은 `forecast_service.compute_forecast()`의 urgent/soon 항목만 취함.
- `services/erp_client.py` — 범용 HTTP JSON POST + **Mock 폴백**.
  `IRMS_ERP_ENDPOINT` 미설정 시 외부 호출 0으로 안전 동작(현장 ERP 스펙 확정 전 선출시 가능).
- `routers/order_routes.py` — manager 전용 8개 엔드포인트(생성/목록/상세/수정/전송/취소/
  Excel/인쇄). 상태 전이 위반 400, 미존재 404, 전송 실패 502, sent 멱등 차단.

### 2.3 UI
- `/management` 신규 탭 "발주서": 분석기간 선택 + "발주 권장에서 생성" + 목록 + 상세 모달
  (수량 인라인 편집/Excel/인쇄/ERP 전송/취소).
- `templates/order_print.html` — 인쇄 최적화 HTML(@media print) → 브라우저 PDF 저장.
  **신규 의존성 없음**(reportlab 회피, 프로젝트 lean-deps 철학 준수).
- `static/js/orders.js` — IIFE 구조, 쓰기 시 `x-csrftoken` 헤더 직접 부착([[project_management_csrf]] 준수).

## 3. 주요 설계 결정

| 결정 | 선택 | 근거 |
|------|------|------|
| PDF | 인쇄용 HTML → 브라우저 PDF | 신규 의존성 회피 + 현장 인쇄 워크플로 정합 |
| Excel | openpyxl | 기존 의존성, 수식 인젝션 방어(`_xlsx_safe`) |
| ERP | HTTP JSON + Mock 폴백 | 실제 스펙 미확정 → 안전 선출시 |
| 스냅샷 | 생성 시점 고정 | 발주 결정은 재고 변동에 흔들리면 안 됨 |
| 상태머신 | draft→sent/failed→(취소) | 중복 발주·수정 사고 방지(멱등) |

## 4. 검증

### 4.1 단위·통합 테스트 (`tests/test_order_sheet_erp.py`, 13개)
채번 일련번호 / 스냅샷 생성 / 0건 ValueError / draft 수정·0 제외 / sent 수정 거부 /
mock 전송 / cancel draft·sent거부 / Excel 바이트·인젝션 / payload 필터 / 권한 / ERP mock.

### 4.2 라이브 QA (실제 FastAPI 스택, 24개 체크)
임시 DATA_DIR + 데모 시드 + 긴급 자재 강제 주입 후 HTTP 전 구간 실행:
비인증 차단 → manager 로그인 → 생성 → 목록 → 수정 → **Excel(PK 시그니처)** →
**인쇄 템플릿 렌더** → **ERP Mock 전송** → 재전송/수정 멱등 차단 → sent 취소 거부 →
404 → **operator 403**. → **24/24 통과**.

### 4.3 회귀
전체 스위트 **151 passed** (기존 138 + 신규 13). forecast/재고/계량 로직 무변경(읽기만).

## 5. 운영 가이드

- **ERP 실연동 시**: `.env`에 `IRMS_ERP_ENDPOINT`(필수), `IRMS_ERP_API_KEY`(선택, Bearer),
  `IRMS_ERP_TIMEOUT`(기본 10초) 설정. 미설정 시 Mock 모드로 "전송됨" 처리되며 실제 호출 없음.
- **ERP payload**: `{order_no, created_at, created_by, note, item_count, total_qty,
  items:[{material_id, material_name, category, unit, order_qty, note}]}` (order_qty>0만).
- **권한**: manager 이상. 모든 생성/수정/전송/취소는 audit log 기록.

## 6. 후속 과제 (Out of Scope → 차기 PDCA 후보)

1. 특정 ERP 벤더 전용 프로토콜(SAP/더존 등) 어댑터 — 현재는 범용 HTTP JSON.
2. 입고/검수/정산 등 발주 후속 워크플로.
3. 공급사 마스터(현재 category로 갈음).
4. 발주 승인 결재선.
5. (문서 동기화) 설계 §2.2의 "FK 강제 안 함" 문구를 실제 구현(FK + ON DELETE CASCADE)에
   맞게 정정 — gap 분석 권장사항.

## 7. 변경 파일

신규: `services/order_service.py`, `services/erp_client.py`, `routers/order_routes.py`,
`templates/order_print.html`, `static/js/orders.js`, `tests/test_order_sheet_erp.py`,
`docs/{01-plan,02-design,03-analysis,04-report}/.../order-sheet-erp.*`
수정: `config.py`, `db/schema.py`, `db/migrations.py`, `routers/api.py`,
`routers/models.py`, `templates/management.html`
