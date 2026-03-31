# IRMS Improvements 완료 보고서

> **상태**: 완료
>
> **프로젝트**: IRMS (Ink Recipe Management System)
> **버전**: v0.2.0
> **작성자**: PDCA Report Generator
> **완료일**: 2026-03-08
> **PDCA 사이클**: #2

---

## 1. 요약

### 1.1 프로젝트 개요

| 항목 | 내용 |
|------|------|
| 피처명 | irms-improvements |
| 시작일 | 2026-03-07 |
| 완료일 | 2026-03-08 |
| 소요시간 | 1일 |
| 기반 | IRMS PoC v0.1.0 (매치율 92%) |

### 1.2 결과 요약

```
┌─────────────────────────────────────────────┐
│  완성도: 97.6%                              │
├─────────────────────────────────────────────┤
│  ✅ 완료됨:     9 / 9 개 항목                 │
│  ⏳ 진행 중:     0 / 9 개 항목                 │
│  ⏸️ 이월:       0 / 9 개 항목                 │
│  ❌ 취소됨:     0 / 9 개 항목                 │
└─────────────────────────────────────────────┘
```

---

## 2. 관련 문서

| 단계 | 문서 | 상태 |
|------|------|------|
| Plan | [irms-improvements.plan.md](../01-plan/features/irms-improvements.plan.md) | ✅ 최종화 |
| Design | [irms-improvements.design.md](../02-design/features/irms-improvements.design.md) | ✅ 최종화 |
| Check | [irms-improvements.analysis.md](../03-analysis/irms-improvements.analysis.md) | ✅ 완료 |
| Act | 현재 문서 | 🔄 작성 완료 |

---

## 3. PDCA 사이클 요약

### 3.1 Plan 단계: 개선 항목 정의

**목표**: IRMS PoC 코드 리뷰 기반 품질 개선

**식별된 문제**:
- P0 (즉시): 단위 변환 중복, XSS 취약점
- P1 (고우선): 통계 집계 오류, CDN 로컬화, debounce 부재
- P2 (중우선): DB 스키마 갭, 상태 전이 정합성, deprecated API
- P3 (저우선): SQL injection 패턴 개선

### 3.2 Design 단계: 상세 설계

**설계 기반**:
- 총 9개 개선 항목 (A-1 ~ D-1)
- 수정 파일: 6개 (database.py, api.py, common.js, work.js, management.js, insight.js)
- 신규 파일: 2개 (jspreadsheet, jsuites 로컬 벤더 파일)
- 의존성 순서: A(P0) → B(P1) → C(P2) → D(P3)

### 3.3 Do 단계: 구현

**구현 현황**: 9/9 항목 완료

| 단계 | 항목 | 파일 수정 | 상태 |
|------|------|---------|------|
| **A (P0)** | 데이터 무결성 & 보안 | 2 | ✅ |
| **B (P1)** | 핵심 기능 | 3 | ✅ |
| **C (P2)** | 스키마 & 로직 정합성 | 3 | ✅ |
| **D (P3)** | 보안 강화 | 1 | ✅ |

### 3.4 Check 단계: 검증

**분석 결과**: 매치율 **97.6%** (41/42 체크 통과)

| 항목 | 점수 | 상태 |
|------|------|------|
| 아키텍처 준수 | 100% | PASS |
| 컨벤션 준수 | 100% | PASS |
| 설계 구현 | 97.6% | PASS |
| **전체** | **97.6%** | **PASS** |

---

## 4. 개선 항목별 완료 현황

### Phase A: 긴급 수정 (P0)

#### A-1: 단위 변환 중복 실행 방지

| 항목 | 내용 |
|------|------|
| 파일 | `src/database.py` |
| 문제 | `standardize_recipe_units_to_grams()` 매 서버 시작마다 실행 → 데이터 손상 |
| 해결 | `schema_migrations` 테이블 도입, 마이그레이션 플래그 기록 |
| 구현 | `has_migration()`, `record_migration()` 함수 추가 |
| 검증 | T-1 PASS - 서버 2회 재시작 후 value_weight 불변 |
| 상태 | ✅ 완료 |

**주요 변경**:
- `schema_migrations` 메타 테이블 생성
- `has_migration()` 함수로 마이그레이션 여부 확인
- `record_migration()` 함수로 완료 기록
- `standardize_recipe_units_to_grams()` 체크 로직 추가

#### A-2: XSS 방어

| 항목 | 내용 |
|------|------|
| 파일 | `common.js`, `work.js`, `management.js`, `insight.js` |
| 문제 | innerHTML에 서버 데이터 직접 삽입 → 스크립트 실행 가능 |
| 해결 | `escapeHtml()` 유틸 함수 추가, 모든 사용자 데이터 이스케이프 |
| 구현 범위 | 11개 위치에서 productName, inkName 등 17개 필드 이스케이프 |
| 검증 | T-2 PASS - XSS 스크립트 삽입 차단 |
| 상태 | ✅ 완료 |

**주요 변경**:
- `IRMS.escapeHtml()` 함수: `&<>"'` 문자 이스케이프
- work.js: buildRows(107-133), renderLog(154-164), renderWeighingPanel(277-312) 이스케이프 적용
- management.js: renderHistory(252-265), renderPreview(217-234) 이스케이프 적용
- insight.js: renderWeightTable(58-68), renderCountTable(81-91), renderBars(113-114) 이스케이프 적용

**추가 개선** (설계 초과):
- insight.js 필터 옵션도 이스케이프 처리 (category escape)
- management.js 에러/경고 메시지도 이스케이프 (renderIssues escape)
- work.js weighing panel에서 textContent 사용 (더 안전한 DOM API 레벨 방어)

### Phase B: 핵심 수정 (P1)

#### B-1: 통계 count 집계 수정

| 항목 | 내용 |
|------|------|
| 파일 | `src/routers/api.py` |
| 문제 | count 타입 원재료는 `value_text`에 저장되지만 `value_weight` 사용 → Insight 화면 카운트 0 표시 |
| 해결 | SQL 쿼리 변경: `SUM(CASE WHEN m.unit_type = 'count' AND ri.value_text IS NOT NULL THEN 1 ELSE 0 END)` |
| 검증 | T-3 PASS - count 타입 정상 집계 |
| 상태 | ✅ 완료 |

**변경 내용**:
- 기존: `SUM(CASE WHEN m.unit_type = 'count' THEN COALESCE(ri.value_weight, 0) ELSE 0 END) AS total_count`
- 신규: `SUM(CASE WHEN m.unit_type = 'count' AND ri.value_text IS NOT NULL THEN 1 ELSE 0 END) AS total_count`

#### B-2: CDN 라이브러리 로컬화

| 항목 | 내용 |
|------|------|
| 파일 | `templates/management.html`, `static/vendor/` (신규) |
| 문제 | JSpreadsheet/jSuites를 CDN으로 로드 → 폐쇄망 환경 미지원 |
| 해결 | 로컬 벤더 디렉토리에 라이브러리 저장 |
| 구현 | 4개 파일 추가 (jspreadsheet.min.css/js, jsuites.min.css/js) |
| 검증 | T-4 PASS - 오프라인 환경에서 스프레드시트 정상 렌더링 |
| 상태 | ✅ 완료 |

**디렉토리 구조**:
```
static/vendor/
  jspreadsheet/
    jspreadsheet.min.css
    jspreadsheet.min.js
  jsuites/
    jsuites.min.css
    jsuites.min.js
```

**HTML 변경**:
- 기존: `https://cdn.jsdelivr.net/npm/...`
- 신규: `/static/vendor/jspreadsheet/...`, `/static/vendor/jsuites/...`

#### B-3: debounce 추가

| 항목 | 내용 |
|------|------|
| 파일 | `common.js`, `work.js`, `management.js` |
| 문제 | input 이벤트에 직접 render/API 호출 → 타이핑마다 요청 발생 |
| 해결 | `debounce()` 유틸 함수 추가, 검색 입력에 적용 (300ms 지연) |
| 구현 | 2개 위치에 debounce 바인딩 |
| 검증 | T-5 PASS - 빠른 타이핑(10자)에 API 요청 1-2회만 발생 |
| 상태 | ✅ 완료 |

**주요 변경**:
- `IRMS.debounce(fn, delay)` 함수: 타이머 기반 호출 지연
- work.js: `searchInput.addEventListener("input", IRMS.debounce(render, 300))`
- management.js: `historySearch.addEventListener("input", IRMS.debounce(renderHistory, 300))`

### Phase C: 스키마 & 로직 정합성 (P2)

#### C-1: DB 스키마 갭 보완

| 항목 | 내용 |
|------|------|
| 파일 | `src/database.py`, `src/routers/api.py` |
| 컬럼 추가 | 7개 (note, cancel_reason, started_by, started_at, raw_input_hash, raw_input_text, revision_of) |
| 구현 | `apply_schema_migrations()` 함수에 ensure_column() 호출 추가 |
| 검증 | T-6, T-8 PASS - cancel_reason, raw_input 정상 저장 |
| 상태 | ✅ 완료 (1개 설계 갭: FK 제약조건 미적용) |

**추가된 컬럼**:

| 컬럼명 | 타입 | 목적 |
|--------|------|------|
| note | TEXT | 비고 |
| cancel_reason | TEXT | 취소 사유 |
| started_by | TEXT | 작업 시작자 |
| started_at | TEXT | 작업 시작 시각 |
| raw_input_hash | TEXT | 붙여넣기 원문 SHA256 |
| raw_input_text | TEXT | 붙여넣기 원문 |
| revision_of | INTEGER | 개정 원본 레시피 ID |

**API 통합**:
- cancel 액션: `cancel_reason` 필드 저장
- import_recipes: `raw_input_hash`, `raw_input_text` SHA256 저장
- update_recipe_status: started_by/at 기록 로직 추가

**설계 대비 갭**:
- 설계: `INTEGER REFERENCES recipes(id)` (FK 제약)
- 구현: `INTEGER` (FK 제약 없음)
- 원인: SQLite `ALTER TABLE ADD COLUMN`은 FK 제약 미지원
- 영향: 낮음 - 기능적 차이 없음 (DB 무결성 관리는 애플리케이션 레벨)

#### C-2: 상태 전이 정합성

| 항목 | 내용 |
|------|------|
| 파일 | `src/routers/api.py` |
| 문제 | complete 액션이 `pending -> completed` 직접 전이 허용 → 작업 시작 시각 미기록 |
| 해결 | pending -> complete 시 started_by/at 자동 기록 (원스텝 완료 지원 + 추적성 보전) |
| 검증 | T-7 PASS - pending->complete 직접 전이 시 started_at 자동 기록 |
| 상태 | ✅ 완료 |

**구현 로직**:
```python
if body.action == "complete" and current_status == "pending":
    # started_by/at 자동 기록 (미기록 시에만)
    connection.execute(
        "UPDATE recipes SET started_by = ?, started_at = ? WHERE id = ? AND started_at IS NULL",
        ("auto", utc_now_text(), recipe_id),
    )
    # 완료 상태로 전환
```

**추가 개선** (설계 초과):
- in_progress 상태에서도 cancel 가능 (기획서 명시 항목)
- start 액션에서도 started_by/at 기록

#### C-3: deprecated API 교체

| 항목 | 내용 |
|------|------|
| 파일 | `src/database.py` |
| 문제 | `datetime.utcnow()` 사용 (Python 3.12+ deprecated) |
| 해결 | `datetime.now(timezone.utc)` 로 교체 |
| 포맷 호환 | `.replace("+00:00", "")` 로 기존 형식 유지 |
| 검증 | 기존 DB 쿼리와 호환성 문제 없음 |
| 상태 | ✅ 완료 |

**변경 내용**:
```python
# Before
from datetime import datetime
return datetime.utcnow().replace(microsecond=0).isoformat()

# After
from datetime import datetime, timezone
return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "")
```

### Phase D: 보안 강화 (P3)

#### D-1: SQL 패턴 개선

| 항목 | 내용 |
|------|------|
| 파일 | `src/database.py` |
| 문제 | `ensure_column()`에서 `table_name` f-string 삽입 → SQL injection 패턴 위험 |
| 해결 | 허용 테이블명 화이트리스트 검증 추가 |
| 구현 | `_ALLOWED_TABLES` frozenset 정의, 호출 시 검증 |
| 검증 | 통과 |
| 상태 | ✅ 완료 |

**구현**:
```python
_ALLOWED_TABLES = frozenset({
    "materials", "material_aliases", "recipes",
    "recipe_items", "schema_migrations"
})

def ensure_column(connection, table_name, column_name, column_def):
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"Unknown table: {table_name}")
    # ... 기존 로직 ...
```

---

## 5. 품질 지표

### 5.1 최종 분석 결과

| 지표 | 목표 | 달성 | 변화 |
|------|------|------|------|
| 설계 매치율 | 95% | 97.6% | +2.6% |
| 아키텍처 준수 | 100% | 100% | - |
| 컨벤션 준수 | 100% | 100% | - |
| 테스트 패스율 | 100% | 100% (8/8) | - |

### 5.2 해결된 이슈

| 우선순위 | 이슈 | 해결 방법 | 결과 |
|----------|------|----------|------|
| P0 | 데이터 파괴 (단위 중복 변환) | 마이그레이션 플래그 | ✅ 해결 |
| P0 | XSS 취약점 | escapeHtml 유틸 + 일괄 이스케이프 | ✅ 해결 |
| P1 | 통계 오류 (카운트 0) | SQL 쿼리 수정 | ✅ 해결 |
| P1 | 폐쇄망 미지원 | 로컬 벤더 파일 | ✅ 해결 |
| P1 | 서버 과부하 | debounce 추가 | ✅ 해결 |
| P2 | 스키마 갭 | 누락 컬럼 추가 | ✅ 해결 |
| P2 | 상태 전이 오류 | 자동 기록 로직 | ✅ 해결 |
| P2 | deprecated API | datetime.now(timezone.utc) | ✅ 해결 |
| P3 | SQL injection 패턴 | 테이블명 화이트리스트 | ✅ 해결 |

### 5.3 구현 통계

| 항목 | 수량 |
|------|------|
| 수정된 파일 | 6개 |
| 신규 파일 | 4개 (벤더) |
| 추가된 함수 | 4개 (has_migration, record_migration, escapeHtml, debounce) |
| 추가된 컬럼 | 7개 |
| 이스케이프 적용 위치 | 11개 |
| debounce 적용 위치 | 2개 |

---

## 6. 학습 & 회고

### 6.1 잘한 점 (Keep)

1. **상세한 설계 문서**: Plan과 Design 단계의 명확한 문서화로 구현 시 혼동 최소화
   - 개선 항목별 우선순위 명확
   - 각 항목의 수정 파일 및 변경 범위 명시
   - 검증 방법 사전 정의

2. **점진적 우선순위 관리**: P0 → P1 → P2 → P3 순서로 진행하여 위험도 높은 항목 먼저 해결
   - 데이터 파괴 위험(A-1) 즉시 해결
   - 보안 취약점(A-2) 조기 차단
   - 덜 중요한 개선(D-1)은 나중에 처리

3. **설계 대비 구현 초과**: 단순 설계 준수 초과로 품질 향상
   - cancel from in_progress 상태 전이 추가 지원
   - start 액션에서도 started_by/at 기록
   - 이스케이프 처리 범위 확대 (필터 옵션, 에러 메시지)
   - textContent 사용으로 더 안전한 DOM 방어

4. **즉각적 검증**: 각 항목별 Test Checklist로 완료 기준 명확화
   - 8개 항목 테스트 모두 PASS
   - 구현 후 즉시 검증으로 오류 조기 발견

### 6.2 개선 필요 (Problem)

1. **설계 단계의 환경 고려 부족**: SQLite의 ALTER TABLE 제약 미리 파악 실패
   - C-1 revision_of FK 제약 미적용 (SQLite 제한)
   - 사전 환경 조사로 방지 가능했음

2. **테스트 자동화 부재**: 수동 테스트만 진행
   - 단위 테스트 코드 없음
   - 회귀 테스트 자동화 미흡

3. **문서화 순서**: 설계 완료 후 구현 진행하는 과정에서 새로운 아이디어 반영
   - 설계 검토 단계 강화 필요
   - 변경사항 추적 및 설계 업데이트 미흡

### 6.3 다음에 시도할 것 (Try)

1. **사전 환경 체크리스트**: 새로운 기술/라이브러리 도입 시 환경별 제약 사항 미리 문서화
   - Python 버전별 API 호환성 확인
   - DB 플랫폼별 SQL 문법 제약 확인

2. **자동화된 검증**: Gap analysis를 자동으로 수행하는 tooling
   - 코드 분석 자동화
   - 테스트 케이스 자동 생성

3. **더 작은 PDCA 사이클**: 개별 항목별로 더 세분화된 사이클 운영
   - P0 항목만 단독 사이클
   - P1-P3 항목을 별도 사이클로 분리

4. **팀 리뷰 강화**: 설계 단계에서 플랫폼 전문가 리뷰 추가
   - DBA 리뷰 (DB 설계)
   - 보안 리뷰 (XSS, SQL injection)

---

## 7. 프로세스 개선 제안

### 7.1 PDCA 프로세스

| 단계 | 현재 상황 | 개선 제안 |
|------|---------|---------|
| Plan | 우선순위별 문제 식별 | 우선순위별 리스크 평가 추가 |
| Design | 파일별 상세 설계 | 환경 제약사항 사전 검토 |
| Do | 순차적 구현 | 병렬 구현 및 PR 단위 검증 |
| Check | 수동 gap 분석 | 자동화 tooling 도입 (linter, analyzer) |

### 7.2 도구/환경

| 영역 | 개선 제안 | 기대 효과 |
|------|---------|---------|
| 테스팅 | pytest 도입 및 자동 테스트 | 회귀 오류 방지 |
| CI/CD | 자동 lint 및 테스트 파이프라인 | 품질 유지 비용 감소 |
| 문서화 | 설계 변경 자동 추적 | 최신성 확보 |

---

## 8. 다음 단계

### 8.1 즉시 조치

- [ ] v0.2.0 버전 태깅
- [ ] 프로덕션 배포 (폐쇄망 환경 포함)
- [ ] 사용자 가이드 업데이트
- [ ] 변경사항 안내 공지

### 8.2 다음 PDCA 사이클

| 항목 | 우선순위 | 예상 시작 |
|------|---------|---------|
| 사용자 인증 (Auth) | 높음 | 2026-03-10 |
| 감사 로그 (Audit) | 중간 | 2026-03-17 |
| SSE 실시간 동기화 | 중간 | 2026-03-24 |
| Docker 배포 설정 | 낮음 | 2026-04-01 |

---

## 9. 변경로그

### v0.2.0 (2026-03-08)

**추가**:
- `schema_migrations` 메타 테이블 및 마이그레이션 추적 기능
- `escapeHtml()` 유틸 함수로 XSS 방어
- `debounce()` 유틸 함수로 검색 성능 최적화
- 7개 누락 컬럼 추가 (note, cancel_reason, started_by, started_at, raw_input_hash, raw_input_text, revision_of)
- 로컬 벤더 파일 (JSpreadsheet, jSuites)

**변경**:
- 통계 count 집계 SQL 쿼리 수정
- deprecated `datetime.utcnow()` → `datetime.now(timezone.utc)` 교체
- 상태 전이 로직: pending->complete 시 자동으로 started_by/at 기록
- 11개 위치에서 사용자 데이터 HTML 이스케이프 처리
- 2개 위치에 검색 입력 debounce 적용

**수정**:
- 단위 변환 중복 실행으로 인한 데이터 파괴 문제
- XSS 취약점 (innerHTML 직접 삽입)
- Insight 화면 count 타입 원재료 카운트 0 표시
- Management 화면 폐쇄망 환경 미지원
- SQLite 동시 쿼리 충돌 우려

**보안**:
- `ensure_column()` 테이블명 화이트리스트 검증 추가
- XSS 방어 유틸 함수화로 보안 일관성 확보

---

## 10. 버전 히스토리

| 버전 | 일시 | 변경사항 | 작성자 |
|------|------|---------|--------|
| 1.0 | 2026-03-08 | 완료 보고서 작성 | PDCA Report Generator |

---

## 11. 부록: 상세 구현 체크리스트

### 11.1 구현 완료 검증

| 항목 | 파일 | 라인 | 상태 |
|------|------|------|------|
| A-1 마이그레이션 테이블 | database.py | 신규 | ✅ |
| A-1 has_migration() | database.py | 신규 | ✅ |
| A-1 record_migration() | database.py | 신규 | ✅ |
| A-2 escapeHtml() | common.js | 신규 | ✅ |
| A-2 work.js XSS | work.js | 107-312 | ✅ |
| A-2 management.js XSS | management.js | 217-265 | ✅ |
| A-2 insight.js XSS | insight.js | 38-114 | ✅ |
| B-1 count 쿼리 | api.py | 496 | ✅ |
| B-2 로컬 vendor | templates/html | 14-15, 157-158 | ✅ |
| B-2 jspreadsheet 파일 | vendor/jspreadsheet/ | 신규 2개 | ✅ |
| B-2 jsuites 파일 | vendor/jsuites/ | 신규 2개 | ✅ |
| B-3 debounce() | common.js | 신규 | ✅ |
| B-3 work.js debounce | work.js | 448 | ✅ |
| B-3 management.js debounce | management.js | 345 | ✅ |
| C-1 note 컬럼 | database.py | apply_schema_migrations | ✅ |
| C-1 cancel_reason 컬럼 | database.py | apply_schema_migrations | ✅ |
| C-1 started_by/at 컬럼 | database.py | apply_schema_migrations | ✅ |
| C-1 raw_input 컬럼 | database.py | apply_schema_migrations | ✅ |
| C-1 revision_of 컬럼 | database.py | apply_schema_migrations | ✅ |
| C-1 raw_input 저장 | api.py | import_recipes | ✅ |
| C-1 cancel_reason 저장 | api.py | update_recipe_status | ✅ |
| C-2 상태 전이 로직 | api.py | 149-196 | ✅ |
| C-3 datetime.now() | database.py | 9 | ✅ |
| D-1 테이블 화이트리스트 | database.py | 25 | ✅ |

### 11.2 테스트 결과

| 테스트 | 예상 결과 | 실제 결과 | 상태 |
|--------|---------|---------|------|
| T-1: 서버 재시작 데이터 불변 | value_weight 변화 없음 | value_weight 변화 없음 | PASS |
| T-2: XSS 차단 | 스크립트 미실행 | 스크립트 미실행 | PASS |
| T-3: count 집계 | 정상 카운트 | 정상 카운트 | PASS |
| T-4: 오프라인 동작 | 스프레드시트 로드 | 정상 로드 | PASS |
| T-5: debounce 효과 | API 1-2회 요청 | API 1-2회 요청 | PASS |
| T-6: cancel_reason 저장 | DB 기록 확인 | DB 기록 확인 | PASS |
| T-7: started_at 자동 기록 | DB 시각 기록 | DB 시각 기록 | PASS |
| T-8: raw_input 저장 | DB 원문+해시 기록 | DB 원문+해시 기록 | PASS |

---

## 12. 결론

**IRMS Improvements PoC 품질 개선 사이클 완료**

- **최종 매치율**: 97.6% (목표 95% 달성)
- **완료 항목**: 9/9 (100%)
- **테스트 통과**: 8/8 (100%)

IRMS PoC는 초기 92% 매치율에서 출발하여, 9개의 체계적인 개선 항목을 통해 97.6%의 높은 품질 수준을 달성했습니다. 특히 **데이터 무결성(A-1)**, **보안(A-2, D-1)**, **API 정합성(B-1, C-2)**, **환경 호환성(B-2)** 측면에서 운영 환경 투입을 위한 핵심 이슈를 모두 해결했습니다.

유일한 설계 갭(C-1 FK 제약)은 SQLite의 기술적 제약에 의한 것으로, 기능적 영향은 없으며 애플리케이션 레벨의 데이터 무결성 관리로 충분합니다.

다음 단계로는 **사용자 인증(Auth)** 기능을 별도 PDCA 사이클로 추진하여 완전한 시스템 기능을 확보할 것을 권고합니다.

---

**Report Generated**: 2026-03-08
**PDCA Cycle Status**: Complete
**Next Action**: Production Deployment
