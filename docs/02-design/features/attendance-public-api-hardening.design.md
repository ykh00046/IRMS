# attendance-public-api-hardening Design Document

> **Project**: IRMS · **Version**: 0.1.0 · **Date**: 2026-06-18 · **Status**: Final
> **Planning Doc**: [attendance-public-api-hardening.plan.md](../../01-plan/features/attendance-public-api-hardening.plan.md)

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | 근태 자격 증명과 개인정보 공개 API를 안전한 기본값으로 전환한다. |
| **WHO** | 근태 직원, 관리자, 트레이 운영자 |
| **RISK** | production 토큰 미설정 및 신규 사용자 절차 변경 |
| **SUCCESS** | 계정 열거/자동 약한 계정 생성 제거, production 토큰 필수, 회귀 0 |
| **SCOPE** | 인증 정책, 환경 정책, 기존 토큰 경계, 테스트·문서 |

## 1. Overview

기존 컴포넌트를 재사용해 보안 정책의 빈틈만 닫는다. 공개 응답은 최소화하고 내부 감사 로그에는 실제 실패 원인을 보존한다.

## 2. Architecture Options

| Criteria | A: Minimal | B: Auth Service 재구성 | C: Credential 상태 확장 |
|----------|:----------:|:----------------------:|:-----------------------:|
| 변경 파일 | 4~6 | 10+ | 8+ 및 migration |
| 유지보수성 | High | High | High |
| 배포 위험 | Low | Medium | Medium |
| 목표 충족 | Full | Full | Full+ |

**Selected: Option A.** 임시 비밀번호·강도검사·감사로그·토큰 미들웨어가 이미 구현되어 있어 정책 분기와 검증만 보강하는 것이 가장 작은 완전 해법이다.

## 3. Data Model

스키마 변경 없음. `attendance_users`는 관리자 초기화 시에만 신규 INSERT된다.

## 4. API Specification

| Method | Path | 변경 |
|--------|------|------|
| POST | `/api/attendance/login` | 미프로비저닝/오류 비밀번호 모두 401 `INVALID_CREDENTIALS` |
| POST | `/api/attendance/admin/reset-password` | 기존 임시 비밀번호 발급 경로 유지 |
| GET | `/api/public/attendance-alerts/*` | production 기본 토큰 필수 |

내부 감사 사유는 `employee_not_in_excel`, `account_not_provisioned`, `invalid_credentials`로 구분하되 HTTP 응답으로 노출하지 않는다.

## 5. UI/UX Design

화면 변경 없음. 관리자는 기존 초기화 동작으로 임시 비밀번호를 전달한다. 설정 예시에 production 필수 토큰 생성/배포 지침을 추가한다.

## 6. Error Handling

| Condition | Public response | Internal evidence |
|-----------|-----------------|-------------------|
| 직원 없음 | 401 `INVALID_CREDENTIALS` | audit reason `employee_not_in_excel` |
| 계정 미발급 | 401 `INVALID_CREDENTIALS` | audit reason `account_not_provisioned` |
| 비밀번호 오류 | 401 `INVALID_CREDENTIALS` | 실패 횟수/감사 로그 |
| production 토큰 누락 | startup RuntimeError | 환경 변수 이름 포함 |
| 요청 토큰 누락/오류 | 403 `TRAY_TOKEN_REQUIRED` | 응답 코드 |

## 7. Security Design

- 인증 실패는 사용자 열거가 불가능한 동일 계약을 사용한다.
- 신규 계정은 암호학적 난수 임시 비밀번호로만 생성한다.
- 토큰 비교는 기존 `hmac.compare_digest`를 유지한다.
- production은 `REQUIRE_TRAY_API_TOKEN` 기본 true, development는 false다.
- 운영자가 위험을 명시적으로 수용할 때만 `IRMS_REQUIRE_TRAY_API_TOKEN=false` override가 가능하다.

## 8. Test Plan

| Level | Scenario | Expected |
|-------|----------|----------|
| L1 | 미프로비저닝 로그인 | 401, INSERT 없음, 감사 로그 |
| L1 | 직원 미존재 로그인 | 같은 401 계약 |
| L1 | 관리자 임시 비밀번호 생성 후 인증 | 성공, reset required |
| L1 | production 기본 토큰 누락 | config reload 실패 |
| L1 | production 토큰 유효/무효 | 404 downstream / 403 middleware |
| Regression | 전체 Python/JS | 모두 통과 |

## 9. Implementation Files

| File | Change |
|------|--------|
| `src/attendance_auth.py` | 자동 생성 제거, 오류 통일 |
| `src/config.py` | production 토큰 기본 필수 |
| `.env.example` | 안전한 운영 기본값/생성 안내 |
| `tests/test_attendance_auth_hardening.py` | 인증 정책 테스트 |
| `tests/test_security_headers.py` | production 기본값 계약 갱신 |

## 10. Traceability

| Plan | Design/Code |
|------|-------------|
| FR-01~03 | attendance auth + 전용 테스트 |
| FR-04~05 | config/middleware + security tests |

## 11. Implementation Guide

1. 인증 테스트를 먼저 추가한다.
2. `ensure_account`와 `authenticate`의 자동 생성 경로를 fail-closed로 바꾼다.
3. production 토큰 기본값과 환경 예시를 갱신한다.
4. 대상 테스트, 전체 회귀를 실행한다.

### 11.3 Session Guide

| Module | Scope | Files |
|--------|-------|-------|
| module-1 | Attendance credential policy | `attendance_auth.py`, auth tests |
| module-2 | Public API token policy | `config.py`, `.env.example`, middleware tests |
| module-3 | Verification/docs | full tests, Analysis/QA/Report |

## 12. Decision Record

| Decision | Rationale |
|----------|-----------|
| Option A | 기존 보안 기반을 재사용해 회귀 면적 최소화 |
| Generic 401 | 계정 존재 여부 유출 방지 |
| Production fail-closed | 외부 노출 환경의 설정 누락을 조용히 허용하지 않음 |

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-18 | 자율 승인된 Design | Codex |
