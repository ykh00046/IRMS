# 발주서 생성·ERP 연동 — Plan

> `material-forecast`가 산출한 권장 발주량·긴급도를 입력으로 받아, 발주서를 스냅샷으로 확정하고 Excel/인쇄용(PDF) 문서로 출력하며 ERP로 전송한다.

## 1. Overview

| 항목 | 값 |
|------|------|
| Feature | `order-sheet-erp` |
| Phase | Plan |
| 작성일 | 2026-06-02 |
| Priority | High (material-forecast 후속, 발주 사이클 완결) |
| Level | Dynamic |
| Base | `forecast_service.compute_forecast()` (권장 발주량·긴급도), `materials` |
| Goal | 발주 권장 자재를 **발주서로 확정 → 수량 검토/조정 → Excel/PDF 출력 → ERP 전송**까지 한 흐름으로 처리 |
| 선행 완료 | `material-forecast` (2026-06-01, 권장 발주량·긴급도 산출), `forecast-dashboard-alert` (2026-06-01) |

## 2. Problem Statement

`material-forecast`는 "무엇을 얼마나 발주해야 하는가"(권장 발주량·긴급도)를 산출하지만,
그 다음 단계인 **"실제 발주 행위"**는 여전히 수작업이다.

현재 책임자는 forecast의 CSV를 내려받아 → 외부 발주 시스템/ERP에 **수기로 다시 입력**한다.
이 과정에서 다음 문제가 발생한다.

### Pain Points

1. **이중 입력** — forecast 결과를 보고 ERP에 사람이 다시 타이핑. 오타·누락·수량 오기.
2. **발주 이력 부재** — "지난주에 무엇을 얼마나 발주했는지" 시스템에 남지 않음. forecast CSV는 휘발성.
3. **발주서 양식 없음** — 공급사/구매부서 전달용 정식 문서(발주번호·일자·품목·수량) 부재. CSV를 그대로 보냄.
4. **추천과 실제의 괴리** — 권장량을 그대로 발주하지 않고 책임자가 조정하는데, 그 조정 결과가 기록되지 않음.
5. **전송 상태 불투명** — ERP에 보냈는지/성공했는지 추적 불가.

## 3. Feature Items

### 3.1 발주서 생성 (forecast 스냅샷)

| Item | Detail |
|------|--------|
| 입력 | `compute_forecast()` 결과의 발주 권장(`urgent`/`soon`) 자재 |
| 스냅샷 | 생성 시점의 자재명·현재고·일평균·잔여일수·예상소진일·긴급도·권장량을 발주서 항목에 **고정 저장** (이후 재고 변동과 무관) |
| 발주번호 | `PO-YYYYMMDD-NNN` 형식 자동 채번 (일자별 일련번호) |
| 초기 상태 | `draft` (작성중) |

### 3.2 발주 수량 검토·조정

| Item | Detail |
|------|--------|
| 조정 | 항목별 발주 수량(`order_qty`)을 권장량에서 수정 가능 (draft 상태에서만) |
| 항목 제외 | 수량 0 입력 시 발주 대상에서 제외 |
| 비고 | 발주서 전체 비고 + 항목별 비고 입력 |
| 권한 | manager 이상 |

### 3.3 발주서 출력 (Excel / PDF)

| Item | Detail |
|------|--------|
| Excel | `openpyxl`로 `.xlsx` 생성 (헤더: 발주번호/일자/작성자, 본문: 품목/카테고리/단위/권장량/발주량/예상소진일/긴급도, 합계) |
| PDF | 인쇄 최적화 HTML 페이지(`/api/orders/{id}/print`) → 브라우저 인쇄(Ctrl+P)로 PDF 저장. **신규 의존성 없음** |
| 보안 | CSV/Excel 수식 인젝션 방어(위험 문자 접두 `'`) |

### 3.4 ERP 전송

| Item | Detail |
|------|--------|
| 방식 | 발주서 JSON을 설정된 ERP 엔드포인트로 HTTP POST(`httpx`, 기존 의존성) |
| 설정 | `IRMS_ERP_ENDPOINT`, `IRMS_ERP_API_KEY`(선택), `IRMS_ERP_TIMEOUT` 환경변수 |
| Mock 모드 | 엔드포인트 미설정 시 **모의 전송**(실제 호출 없이 성공 처리 + "mock" 표기). 현장 ERP 스펙 확정 전까지 안전하게 동작 |
| 상태 전이 | `draft` → (전송) → `sent`/`failed`. 전송 성공 시 응답 코드·본문 저장, 재전송은 `failed`/`draft`에서만 |
| 멱등 | 이미 `sent`인 발주서는 재전송 차단(중복 발주 방지) |

### 3.5 발주서 관리 화면 (책임자 전용)

| Item | Detail |
|------|--------|
| 위치 | `/management` 신규 탭 "발주서" |
| 목록 | 발주번호 / 일자 / 항목수 / 총수량 / 상태(작성중/전송됨/실패/취소) / 작성자 |
| 생성 | "발주 권장에서 생성" 버튼 → forecast 스냅샷으로 draft 발주서 생성 |
| 상세 | 항목 표 + 수량 인라인 편집 + Excel 다운로드 + 인쇄 + ERP 전송 + 취소 |

## 4. Scope

### In Scope
- 발주서/항목 테이블 신설(`purchase_orders`, `purchase_order_items`)
- forecast 결과 → 발주서 스냅샷 생성 서비스
- 발주 수량 조정·취소(상태 머신: draft/sent/failed/cancelled)
- Excel(.xlsx) 출력 + 인쇄용 HTML(→PDF)
- ERP 전송 어댑터(HTTP POST + Mock 모드)
- 발주서 관리 화면 신규 탭 + 인라인 편집
- 감사 로그(생성/수정/전송/취소)
- pytest 단위/통합 테스트

### Out of Scope
- 실제 특정 ERP 벤더 전용 프로토콜 구현(SAP/더존 등) — 범용 HTTP JSON 어댑터까지만
- 입고/검수/정산 등 발주 후속 워크플로
- 공급사 마스터 관리(자재의 category로 갈음, 별도 supplier 테이블 없음)
- 발주 승인 결재선(단일 책임자 확정)
- 트레이 푸시 알림

## 5. Success Criteria

1. forecast 발주 권장 자재로 발주서(draft)가 생성되고, 생성 시점 값이 스냅샷으로 고정된다.
2. draft 상태에서 항목별 발주 수량을 조정/제외하고 비고를 입력할 수 있다.
3. 발주서를 Excel(.xlsx)로 내려받고, 인쇄용 페이지로 PDF 저장할 수 있다.
4. ERP 전송 시 엔드포인트 설정이 있으면 HTTP POST, 없으면 Mock 처리되며 상태가 `sent`로 전이된다.
5. 이미 전송된 발주서는 재전송/수정이 차단된다(중복·정합성 보호).
6. 권한: operator 접근 불가, manager 이상만 가능.
7. 모든 생성/수정/전송/취소가 감사 로그에 남는다.
8. 기존 forecast/재고/계량 기능에 회귀가 없다(전체 pytest 통과).

## 6. Design Decisions (자율 판단)

| 결정 | 선택 | 근거 |
|------|------|------|
| PDF 생성 | 인쇄 최적화 HTML → 브라우저 PDF | `reportlab` 등 신규 의존성 회피. 프로젝트의 lean-deps 철학 + 현장 PC 인쇄 워크플로와 정합 |
| Excel | `openpyxl` | 이미 의존성에 존재(출입 Excel에서 사용 중) |
| ERP 어댑터 | 범용 HTTP JSON POST + Mock 폴백 | 실제 ERP API 스펙 미확정. Mock 모드로 안전하게 선출시, 스펙 확정 시 엔드포인트만 설정 |
| 전송 클라이언트 | `httpx`(동기) | 이미 의존성. 라우터는 def(스레드풀) 정책과 정합([[project_async_db_threadpool]]) |
| 스냅샷 vs 라이브 | 생성 시점 스냅샷 | 발주서는 "그때의 결정"을 보존해야 함. 재고 변동에 발주 내역이 흔들리면 안 됨 |
| 발주번호 | `PO-YYYYMMDD-NNN` | 사람이 읽고 공급사와 소통 가능. 일자별 일련번호로 충돌 방지 |
| 상태 머신 | draft→sent/failed→(취소) | 최소 상태로 중복 발주·수정 사고 방지 |
| UI 위치 | management 신규 탭 | forecast와 동일 권한·맥락. 기존 탭/JS 모듈 패턴 재사용 |

## 7. Open Questions (기본값으로 자율 결정)

1. 발주서 항목 기본 채움 → `urgent`+`soon` 자재만 자동 포함, `ok`는 제외(필요 시 수동 추가는 차기) — **기본값 확정**
2. 분석기간 → 발주서 생성 시 forecast와 동일 `window_days`(기본 30) 파라미터 사용 — **기본값 확정**
3. 빈 발주서(권장 자재 0건) → 생성 차단, "발주 권장 자재가 없습니다" 안내 — **기본값 확정**
4. ERP payload 스키마 → `{order_no, created_at, items:[{material_name, category, unit, order_qty}], note}` 범용 형태 — **기본값 확정**
5. 수량 단위 → forecast와 동일 `g` 고정(레시피 단위 규칙 계승) — **기본값 확정**
