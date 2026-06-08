# db-session-dependency Planning Document

> **Summary**: R3 후속 추가기능. 라우트의 SQLite 커넥션을 FastAPI dependency로 주입해 요청 단위 수명과 테스트 override 지점을 만든다.
>
> **Project**: IRMS
> **Version**: 0.1.0
> **Author**: Codex
> **Date**: 2026-06-07
> **Status**: Final

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | `async-db-threadpool` 이후에도 라우트가 `get_connection()`을 직접 호출해 커넥션 수명과 테스트 대체 지점이 흩어져 있다. |
| **Solution** | `get_db()` FastAPI dependency를 추가하고, 대표 DB 라우트인 forecast 라우트를 request-scoped dependency로 전환한다. |
| **Function/UX Effect** | 기능 응답은 유지하면서 forecast API를 dependency override로 격리 테스트할 수 있고, 요청 종료 시 커넥션 close가 보장된다. |
| **Core Value** | R3 아키텍처 개선의 다음 단계로 DB 접근 표준을 작게 도입해 이후 라우트 전면 마이그레이션의 기준점을 만든다. |

---

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 라우트별 직접 DB 연결은 수명 관리와 테스트 격리를 어렵게 만든다. |
| **WHO** | IRMS 운영 사용자, 유지보수 개발자, 테스트 작성자. |
| **RISK** | 전면 치환 시 인증/관리/재고 쓰기 라우트에서 commit 범위가 흔들릴 수 있다. |
| **SUCCESS** | `get_db()` close 보장, forecast 라우트 dependency 전환, 관련 테스트와 전체 pytest 통과. |
| **SCOPE** | 기반 dependency 추가, forecast 라우트 적용, regression test 추가, 후속 전면 적용은 다음 cycle. |

---

## 1. Overview

### 1.1 Purpose

`async-db-threadpool`에서 동기 라우트를 threadpool로 옮긴 뒤 남은 DB 아키텍처 부채 중 가장 작고 안전한 단계를 진행한다. 목표는 모든 라우트의 동작을 바꾸는 것이 아니라, request-scoped DB dependency를 공식 진입점으로 만들고 대표 라우트에서 검증하는 것이다.

### 1.2 Background

기존 `with get_connection() as connection:` 패턴은 SQLite context manager가 commit/rollback만 처리하고 close는 보장하지 않는다. 또한 라우트 내부 직접 호출은 테스트에서 DB를 대체하려면 config reload나 파일 DB 조작에 의존하게 만든다.

### 1.3 Related Documents

- `docs/04-report/features/async-db-threadpool.report.md`
- `docs/01-plan/features/async-db-threadpool.plan.md`

---

## 2. Scope

### 2.1 In Scope

- [x] `src/db/connection.py`에 `get_db()` generator dependency 추가
- [x] `src/db/__init__.py`에서 `get_db` export
- [x] `src/routers/forecast_routes.py`의 forecast 조회/CSV/파라미터 수정 라우트 전환
- [x] dependency close 동작과 route override 테스트 추가
- [x] 관련 forecast regression 테스트 실행

### 2.2 Out of Scope

- 모든 라우트의 일괄 치환
- `src/auth.py`, `src/attendance_auth.py`, `src/db/schema.py` 등 request scope 밖 유틸 치환
- 트랜잭션 자동 commit/rollback 정책 도입

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | FastAPI dependency로 사용할 `get_db()`를 제공한다. | High | Complete |
| FR-02 | 요청 종료 시 dependency가 SQLite connection을 close한다. | High | Complete |
| FR-03 | forecast 라우트가 직접 `get_connection()`을 호출하지 않는다. | High | Complete |
| FR-04 | forecast 라우트가 `app.dependency_overrides[get_db]`로 테스트 DB를 받을 수 있다. | Medium | Complete |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| Compatibility | 기존 forecast API 응답 shape 유지 | 기존 `test_material_forecast.py` |
| Reliability | close 보장 | `test_get_db_closes_connection` |
| Testability | in-memory DB override 가능 | `test_forecast_route_uses_overridable_db_dependency` |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [x] `get_db()` 추가 및 export 완료
- [x] forecast 라우트가 dependency 기반으로 동작
- [x] 새 테스트 추가
- [x] 관련 regression 통과
- [x] 전체 pytest 통과

### 4.2 Quality Criteria

- [x] 기존 API path/status/response 유지
- [x] 커넥션 설정(`foreign_keys`, `busy_timeout`, `check_same_thread=False`) 재사용
- [x] 후속 라우트 마이그레이션을 위한 패턴 명확화

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| write 라우트 commit 범위 변경 | High | Medium | 이번 cycle은 forecast write 1개만 명시 commit 유지 |
| dependency override key 불일치 | Medium | Low | `src.db.get_db` export 객체로 테스트 검증 |
| in-memory SQLite thread 제한 | Medium | Medium | 테스트 DB도 운영 설정과 맞춰 `check_same_thread=False` 사용 |

---

## 6. Impact Analysis

### 6.1 Changed Resources

| Resource | Type | Change Description |
|----------|------|--------------------|
| `src/db/connection.py` | Infrastructure | `get_db()` request dependency 추가 |
| `src/db/__init__.py` | Public module API | `get_db` export |
| `src/routers/forecast_routes.py` | API route | 직접 연결에서 dependency 주입으로 변경 |

### 6.2 Current Consumers

| Resource | Operation | Code Path | Impact |
|----------|-----------|-----------|--------|
| `get_connection()` | READ/WRITE | auth, attendance_auth, schema, remaining routers | 없음, 기존 API 유지 |
| `get_db()` | READ/WRITE | forecast routes, tests | 신규 표준 경로 |

### 6.3 Verification

- [x] 변경 consumer 테스트 완료
- [x] 인증 권한 흐름 유지 확인
- [x] response shape regression 확인

---

## 7. Architecture Considerations

### 7.1 Project Level Selection

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| **Dynamic** | FastAPI + SQLite + 운영 업무 앱 | 현재 IRMS 구조 | yes |

### 7.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| DB dependency style | Direct call / FastAPI dependency / repository DI | FastAPI dependency | 현재 라우트 구조와 가장 잘 맞고 override 가능 |
| Migration scope | All routes / Representative route | Representative route | commit 범위 회귀 위험을 낮춤 |
| Transaction policy | Auto commit / Explicit commit | Explicit commit 유지 | 기존 쓰기 라우트 semantics 보존 |

---

## 8. Convention Prerequisites

| Category | Current State | Applied |
|----------|---------------|---------|
| Python style | Existing FastAPI router functions | 기존 스타일 유지 |
| Imports | stdlib, FastAPI, internal 순서 | 적용 |
| Tests | pytest + TestClient | 적용 |

---

## 9. Next Steps

1. [x] Design document 작성
2. [x] Do 구현
3. [x] Analysis/QA/Report 완료
4. [ ] 다음 cycle에서 write-heavy 라우트별 점진 전환

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-07 | Initial final plan | Codex |
