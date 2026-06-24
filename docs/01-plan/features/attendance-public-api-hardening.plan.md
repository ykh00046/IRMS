# attendance-public-api-hardening Planning Document

> **Summary**: 근태 계정의 예측 가능한 초기 비밀번호와 공개 알림 API의 운영환경 무토큰 접근을 제거한다.
>
> **Project**: IRMS
> **Version**: 0.1.0
> **Author**: Codex
> **Date**: 2026-06-18
> **Status**: Final

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | 신규 근태 계정이 사번을 초기 비밀번호로 자동 생성할 수 있고, 운영환경 공개 알림 API가 설정 누락 시 LAN 신뢰만으로 개인정보를 반환한다. |
| **Solution** | 계정은 관리자 임시 비밀번호 발급으로만 프로비저닝하고, 계정 존재 여부를 숨기며, 운영환경 트레이 API 토큰을 기본 필수로 전환한다. |
| **Function/UX Effect** | 신규 사용자는 관리자에게 임시 비밀번호를 받아 최초 로그인 후 변경하고, 트레이 클라이언트는 공유 토큰이 일치할 때만 근태 이상 데이터를 조회한다. |
| **Core Value** | 개인정보 API의 기본값을 fail-closed로 바꾸고 예측 가능한 자격 증명을 제거한다. |

## Context Anchor

| Key | Value |
|-----|-------|
| **WHY** | bkit 코드리뷰 P0와 test-execution-baseline의 차기 High 권고인 근태 인증·공개 API 보호를 이행한다. |
| **WHO** | 근태 조회 직원, 관리자, 현장 트레이 클라이언트 운영자 |
| **RISK** | 운영 전환 시 토큰 미설정으로 서버가 시작되지 않거나 기존 신규 사용자 자동 가입 흐름이 중단될 수 있다. |
| **SUCCESS** | 미프로비저닝 계정 로그인 거부, 계정 열거 방지, production 토큰 필수, 전체 회귀 테스트 통과 |
| **SCOPE** | 인증 프로비저닝·오류 계약, 트레이 토큰 기본 정책, 환경 예시, 단위/통합 테스트 |

## 1. Overview

### 1.1 Purpose

근태 데이터의 인증 경계를 안전한 기본값으로 강화한다.

### 1.2 Background

`CODE_REVIEW.md`는 근태 인증과 공개 근태 알림 API 보호를 P0로 분류했고, 완료된 `test-execution-baseline` 보고서도 이를 다음 High 우선순위로 지정했다. 일부 기반(임시 비밀번호, 강도 검사, 실패 감사, 토큰 미들웨어)은 이미 있으나 자동 사번 비밀번호 생성과 production의 선택적 토큰 정책이 남아 있다.

### 1.3 Related Documents

- `CODE_REVIEW.md` P0 개선 계획
- `docs/04-report/features/test-execution-baseline.report.md`

## 2. Scope

### 2.1 In Scope

- [x] 미프로비저닝 근태 계정 자동 생성 제거
- [x] 존재/부재 계정 로그인 실패 응답 통일
- [x] production에서 트레이 토큰 기본 필수화
- [x] 환경 설정과 트레이 배포 절차 명확화
- [x] 인증·미들웨어 회귀 테스트

### 2.2 Out of Scope

- 기존 약한 비밀번호 계정의 일괄 강제 초기화
- MFA, SSO, 사용자 셀프서비스 비밀번호 복구
- 토큰 자동 배포/회전 인프라

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | 계정이 없으면 로그인 과정에서 사번 비밀번호 계정을 만들지 않는다. | High | Approved |
| FR-02 | 미등록 사번과 잘못된 비밀번호는 동일한 공개 오류를 반환한다. | High | Approved |
| FR-03 | 관리자의 비밀번호 초기화가 유일한 신규 계정 프로비저닝 경로다. | High | Approved |
| FR-04 | production은 별도 override가 없으면 트레이 토큰을 요구한다. | High | Approved |
| FR-05 | 유효 토큰은 constant-time 비교하며 트레이 요청 헤더로 전달한다. | High | Existing/Verify |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| Security | fail-closed production 설정, 계정 열거 방지 | pytest 계약 테스트 |
| Compatibility | development LAN 폴백 유지 | 미들웨어 테스트 |
| Quality | 기존 Python/JS 테스트 회귀 없음 | 전체 테스트 실행 |

## 4. Success Criteria

- SC-1: 존재하지 않는 계정 로그인 시 DB 레코드가 생성되지 않고 401 `INVALID_CREDENTIALS`를 반환한다.
- SC-2: 존재하지 않는 직원과 잘못된 비밀번호의 공개 오류 계약이 동일하다.
- SC-3: 관리자 초기화로 생성된 임시 비밀번호는 인증되고 변경 필요 상태다.
- SC-4: production 기본 설정은 토큰 누락 시 시작을 거부하고, 토큰 설정 시 무토큰 요청을 거부한다.
- SC-5: 전체 Python 및 JavaScript 테스트가 통과한다.

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 운영 토큰 누락 | High | Medium | 시작 시 명확한 오류, `.env.example` 생성 명령 제공 |
| 신규 사용자 로그인 절차 변경 | Medium | High | 기존 관리자 초기화 API 재사용, 별도 UI 변경 불필요 |
| 기존 배포 호환성 | Medium | Medium | development 기본 LAN 폴백 유지, 명시적 false override 허용 |

## 6. Impact Analysis

| Resource | Current Consumers | Impact |
|----------|-------------------|--------|
| `authenticate` | `/api/attendance/login` | 신규 계정 자동 생성 중단, 공개 오류 통일 |
| `reset_password_to_temporary` | 관리자 초기화 API | 신규 계정 프로비저닝 경로로 유지 |
| `REQUIRE_TRAY_API_TOKEN` | `create_app` 미들웨어 | production 기본값 변경 |
| `X-IRMS-Tray-Token` | 트레이 폴러 | 기존 구현 재사용 |

## 7. Architecture Considerations

IRMS는 FastAPI 기반 Dynamic 프로젝트다. 인증 도메인 함수와 기존 미들웨어 경계를 유지하며 DB 스키마 변경 없이 정책만 강화한다.

## 8. Convention Prerequisites

- FastAPI 예외는 안정된 `detail` 코드 사용
- 비밀값은 환경 변수로만 주입
- 보안 비교는 `hmac.compare_digest` 유지
- 테스트는 `pytest tests/` 기준

## 9. Next Steps

1. Design 확정
2. 정책 및 테스트 구현
3. Gap 분석, Iterate, QA, Report

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-06-18 | 자율 승인된 Plan | Codex |

