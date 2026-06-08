# db-session-dependency Design Document

> **Summary**: FastAPI request-scoped SQLite dependency를 추가하고 forecast 라우트에 우선 적용한다.
>
> **Project**: IRMS
> **Version**: 0.1.0
> **Author**: Codex
> **Date**: 2026-06-07
> **Status**: Final
> **Planning Doc**: [db-session-dependency.plan.md](../../01-plan/features/db-session-dependency.plan.md)

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

### 1.1 Design Goals

- DB connection 생성 설정은 기존 `get_connection()` 하나로 유지한다.
- FastAPI dependency override가 가능한 request DB 진입점을 만든다.
- 전면 치환 위험을 피하고 대표 forecast 라우트에서 패턴을 검증한다.

### 1.2 Design Principles

- 최소 동작 변경
- 명시적 commit 유지
- 테스트 가능성 우선

---

## 2. Architecture Options

### 2.0 Architecture Comparison

| Criteria | Option A: Minimal | Option B: Clean | Option C: Pragmatic |
|----------|:-:|:-:|:-:|
| **Approach** | `get_db()`만 추가 | 모든 DB 접근 repository DI화 | `get_db()` 추가 + forecast 우선 적용 |
| **New Files** | 0 | 다수 | 1 test |
| **Modified Files** | 2 | 20+ | 3 |
| **Complexity** | Low | High | Medium |
| **Maintainability** | Medium | High | High |
| **Effort** | Low | High | Low-Medium |
| **Risk** | Low but unproven | High | Low |
| **Recommendation** | 기반만 필요할 때 | 장기 대개편 | **Selected** |

**Selected**: Option C. 근거는 R3 후속으로 아키텍처 방향을 실제 라우트와 테스트로 증명하면서도 쓰기 라우트 전면 변경 리스크를 피할 수 있기 때문이다.

### 2.1 Component Diagram

```text
FastAPI Router
  -> Depends(get_db)
      -> get_connection()
      -> yield sqlite3.Connection
      -> close on request end
  -> forecast_service.compute_forecast(connection)
```

### 2.2 Data Flow

```text
HTTP request -> auth dependency -> get_db dependency -> forecast route -> service -> response -> get_db finalizer close
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|------------|---------|
| `get_db` | `get_connection` | 기존 connection 설정 재사용 |
| forecast routes | `Depends(get_db)` | request-scoped connection 주입 |
| tests | `app.dependency_overrides[get_db]` | in-memory DB 격리 |

---

## 3. Data Model

DB schema 변경 없음.

---

## 4. API Specification

기존 forecast API contract 유지.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/forecast/materials` | forecast material list | manager |
| GET | `/api/forecast/export` | forecast CSV export | manager |
| PATCH | `/api/materials/{material_id}/forecast-params` | forecast parameter update | manager |

---

## 5. UI/UX Design

UI 변경 없음.

---

## 6. Error Handling

| Case | Handling |
|------|----------|
| forecast param validation error | 기존처럼 `400` HTTPException |
| auth failure | 기존 router dependency 유지 |
| DB connection finalization | request 종료 후 `connection.close()` |

---

## 7. Security Considerations

- 인증/권한 dependency 변경 없음.
- DB query parameterization 변경 없음.
- 테스트 override는 앱 내부 테스트 전용 FastAPI 기능이다.

---

## 8. Test Plan

### 8.1 Test Scope

| Type | Target | Tool | Phase |
|------|--------|------|-------|
| L1 | dependency finalizer | pytest | Do |
| L1 | forecast route override | TestClient | Do |
| Regression | forecast service/routes | pytest | QA |
| Regression | full suite | pytest | QA |

### 8.2 L1 Test Scenarios

| # | Target | Test Description | Expected |
|---|--------|------------------|----------|
| 1 | `get_db` | generator 종료 시 connection close | closed flag true |
| 2 | `/api/forecast/materials` | override DB의 material 반환 | injected row 반환 |

### 8.3 L2/L3

UI 변경 없음. 기존 API route regression으로 대체.

---

## 9. Clean Architecture

### 9.1 Layer Assignment

| Component | Layer | Location |
|-----------|-------|----------|
| `get_db` | Infrastructure | `src/db/connection.py` |
| forecast route dependency usage | Presentation/API | `src/routers/forecast_routes.py` |
| forecast computation | Application service | `src/services/forecast_service.py` |

### 9.2 Dependency Rules

라우트는 infrastructure dependency를 FastAPI `Depends`로 주입받고, 서비스는 connection을 인자로 받는 기존 순수 구조를 유지한다.

---

## 10. Coding Convention Reference

| Item | Convention Applied |
|------|--------------------|
| Function naming | 기존 Python snake_case |
| Imports | stdlib -> third-party -> internal |
| Tests | pytest function style |

---

## 11. Implementation Guide

### 11.1 File Structure

```text
src/db/connection.py
src/db/__init__.py
src/routers/forecast_routes.py
tests/test_db_dependency.py
```

### 11.2 Implementation Order

1. [x] `get_db()` 추가
2. [x] `get_db` export
3. [x] forecast route에 `Depends(get_db)` 적용
4. [x] close/override 테스트 추가
5. [x] regression 실행

### 11.3 Session Guide

| Module | Scope Key | Description | Estimated Turns |
|--------|-----------|-------------|:---------------:|
| DB dependency foundation | `module-1` | `get_db` 추가와 export | 1 |
| Forecast route migration | `module-2` | 대표 라우트 적용 | 1 |
| Verification docs | `module-3` | 테스트와 PDCA 문서 | 2 |

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-07 | Final design | Codex |
