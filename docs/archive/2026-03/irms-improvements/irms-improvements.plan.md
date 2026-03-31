# IRMS Improvements Plan

> IRMS PoC 코드 리뷰 기반 품질 개선 계획서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | irms-improvements |
| Priority | Critical / High / Medium |
| Base | IRMS PoC v0.1.0 (2026-03-06 archived, matchRate 92%) |
| Goal | 데이터 무결성, 보안, API 정합성, UX 안정성 확보 |

## 2. Problem Statement

IRMS PoC 1차 구현(92% 매치율)은 3화면 기능 프로토타입에 성공했으나,
코드 리뷰에서 **데이터 파괴 위험**, **보안 취약점**, **API 로직 오류** 등
운영 투입 전 반드시 해결해야 할 이슈가 발견되었다.

## 3. Improvement Items

### 3.1 [CRITICAL] 데이터 무결성 - unit 변환 중복 실행

| Item | Detail |
|------|--------|
| 파일 | `src/database.py:41-72` |
| 문제 | `standardize_recipe_units_to_grams()`가 매 서버 시작마다 실행되어, 이미 g로 변환된 value_weight를 다시 x1000 함 |
| 영향 | 서버 재시작할 때마다 투입량 데이터가 1000배씩 증가 (데이터 파괴) |
| 해결 | 마이그레이션 완료 플래그(메타 테이블 또는 컬럼 체크) 도입, 1회만 실행 보장 |
| 우선순위 | P0 - 즉시 수정 |

### 3.2 [CRITICAL] XSS 취약점

| Item | Detail |
|------|--------|
| 파일 | `static/js/work.js:107-133`, `static/js/management.js:252-265`, `static/js/insight.js:58-68` |
| 문제 | `innerHTML`에 서버 데이터(productName, inkName 등)를 직접 삽입. 악의적 입력 시 스크립트 실행 가능 |
| 영향 | 사용자 세션 탈취, 데이터 조작 가능 |
| 해결 | HTML 이스케이프 유틸 함수 도입, innerHTML 대신 textContent 사용 또는 sanitize 처리 |
| 우선순위 | P0 - 즉시 수정 |

### 3.3 [HIGH] 통계 count 집계 오류

| Item | Detail |
|------|--------|
| 파일 | `src/routers/api.py:496` |
| 문제 | `total_count` 계산 시 `ri.value_weight`을 사용하지만, count 타입 데이터는 `value_text`에 저장됨 |
| 영향 | Insight 화면의 Component Count가 항상 0으로 표시 |
| 해결 | count 타입은 `COUNT(ri.value_text)` 또는 `SUM(1)` 방식으로 집계 변경 |
| 우선순위 | P1 |

### 3.4 [HIGH] CDN 라이브러리 로컬화

| Item | Detail |
|------|--------|
| 파일 | `templates/management.html:14-15, 157-158` |
| 문제 | JSpreadsheet/jSuites를 CDN으로 로드. 사내 폐쇄망에서 동작 불가 |
| 영향 | Management 화면 핵심 기능(스프레드시트 UI) 사용 불가, textarea fallback으로 UX 저하 |
| 해결 | CDN 라이브러리를 `static/vendor/`에 로컬 번들로 포함 |
| 우선순위 | P1 |

### 3.5 [HIGH] 검색 입력 debounce 부재

| Item | Detail |
|------|--------|
| 파일 | `static/js/work.js:448`, `static/js/management.js:345` |
| 문제 | `input` 이벤트에 직접 render/API 호출 바인딩. 타이핑마다 API 요청 발생 |
| 영향 | 서버 과부하, SQLite 동시 쿼리 충돌 가능 |
| 해결 | `common.js`에 debounce 유틸 추가 (300ms), 검색 입력에 적용 |
| 우선순위 | P1 |

### 3.6 [MEDIUM] DB 스키마 갭 보완

| Item | Detail |
|------|--------|
| 파일 | `src/database.py` |
| 문제 | 기획서 대비 누락 컬럼: `recipes.note`, `recipes.cancel_reason`, `recipes.started_by`, `recipes.started_at`, `recipes.raw_input_hash`, `recipes.raw_input_text`, `recipes.revision_of` |
| 영향 | 감사 추적성 약화, 취소 사유 미기록, 개정 이력 불가 |
| 해결 | `apply_schema_migrations()`에 누락 컬럼 추가, import 시 raw_input 저장 로직 추가 |
| 우선순위 | P2 |

### 3.7 [MEDIUM] 상태 전이 정합성

| Item | Detail |
|------|--------|
| 파일 | `src/routers/api.py:149-196` |
| 문제 | `complete` 액션이 `pending -> completed` 직접 전이를 허용. 기획서는 `pending -> in_progress -> completed` 순서 요구 |
| 영향 | 작업 시작 시각/작업자 미기록으로 추적성 저하 |
| 해결 | 계량 모드 외 직접 완료 시에도 `started_by/at` 자동 기록, 또는 엄격 전이 적용 후 UI 조정 |
| 우선순위 | P2 |

### 3.8 [MEDIUM] deprecated API 교체

| Item | Detail |
|------|--------|
| 파일 | `src/database.py:9` |
| 문제 | `datetime.utcnow()` 사용 (Python 3.12+ deprecated) |
| 해결 | `datetime.now(timezone.utc)` 로 교체 |
| 우선순위 | P2 |

### 3.9 [LOW] SQL injection 패턴 개선

| Item | Detail |
|------|--------|
| 파일 | `src/database.py:25` |
| 문제 | `ensure_column()`에서 `table_name`을 f-string으로 SQL에 삽입. 현재 내부 호출만 하므로 실질 위험 낮음 |
| 해결 | 허용 테이블명 화이트리스트 검증 추가 |
| 우선순위 | P3 |

## 4. Implementation Priority & Phases

### Phase A: Emergency Fixes (P0) - 즉시

| # | Item | Est. |
|---|------|------|
| A-1 | unit 변환 중복 실행 방지 (3.1) | 30min |
| A-2 | XSS 방어 (3.2) | 1hr |

### Phase B: Core Fixes (P1) - 1일 내

| # | Item | Est. |
|---|------|------|
| B-1 | stats count 집계 수정 (3.3) | 30min |
| B-2 | CDN 로컬화 (3.4) | 1hr |
| B-3 | debounce 추가 (3.5) | 30min |

### Phase C: Schema & Logic Alignment (P2) - 2~3일 내

| # | Item | Est. |
|---|------|------|
| C-1 | DB 스키마 갭 보완 (3.6) | 1hr |
| C-2 | 상태 전이 정합성 (3.7) | 1hr |
| C-3 | deprecated API 교체 (3.8) | 15min |

### Phase D: Hardening (P3) - 여유 시

| # | Item | Est. |
|---|------|------|
| D-1 | SQL 패턴 개선 (3.9) | 15min |

## 5. Out of Scope (이번 사이클 제외)

| Item | Reason |
|------|--------|
| 인증/세션 구현 | 별도 PDCA 사이클로 분리 권장 (users 테이블, 로그인 UI, 세션 관리 전체 설계 필요) |
| audit_logs 테이블 | 인증 구현 후 연계 설계 필요 |
| SSE 실시간 동기화 | Phase 2 기능, 현재 PoC 범위 초과 |
| Docker 배포 설정 | 인프라 별도 사이클 |

## 6. Success Criteria

| Metric | Target |
|--------|--------|
| P0 이슈 해결 | 100% (데이터 파괴 방지, XSS 차단) |
| P1 이슈 해결 | 100% (집계 정확도, 폐쇄망 호환, 성능) |
| P2 이슈 해결 | 100% (스키마 정합성, 상태 전이) |
| 기존 기능 회귀 | 0건 (Work/Management/Insight 정상 동작) |
| Gap Analysis Match Rate | >= 95% |

## 7. Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| unit 변환 수정 시 기존 데이터 이중 변환 | 기존 DB 값 오염 | 수정 전 DB 백업 필수, 현재 단위 상태 확인 로직 선행 |
| innerHTML 일괄 교체 시 레이아웃 깨짐 | UI 회귀 | 이스케이프 함수 도입 방식 우선, 점진적 교체 |
| JSpreadsheet 로컬 번들 버전 불일치 | 기능 오작동 | CDN 버전과 동일 버전 다운로드 확인 |
