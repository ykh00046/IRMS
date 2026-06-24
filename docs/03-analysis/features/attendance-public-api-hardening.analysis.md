# attendance-public-api-hardening Analysis

> **Date**: 2026-06-18 · **Final Match Rate**: 100% · **Verdict**: PASS

## Context Anchor

| WHY | WHO | RISK | SUCCESS | SCOPE |
|-----|-----|------|---------|-------|
| 예측 가능한 근태 자격 증명과 공개 개인정보 API 보호 | 직원·관리자·트레이 운영자 | 설정 누락/절차 변경 | 계정 열거 제거, production 토큰 필수, 회귀 0 | 인증·설정·안내·테스트 |

## 1. Strategic Alignment

`CODE_REVIEW.md` P0 #1/#4와 `test-execution-baseline`의 차기 High 권고를 직접 해결했다. 기존 임시 비밀번호, 강도 검사, 감사 로그, constant-time 토큰 비교를 재사용해 설계 범위를 벗어난 구조 변경은 없다.

## 2. Success Criteria Evaluation

| SC | Status | Evidence |
|----|:------:|----------|
| SC-1 자동 약한 계정 생성 제거 | Met | `authenticate`는 `_create` 없이 401; 전용 테스트 통과 |
| SC-2 계정 열거 방지 | Met | 미등록/미프로비저닝 모두 `INVALID_CREDENTIALS`; 내부 audit reason만 구분 |
| SC-3 관리자 임시 비밀번호 프로비저닝 | Met | `reset_password_to_temporary` 난수 발급 테스트 |
| SC-4 production 토큰 fail-closed | Met | 기본값 `not IS_DEVELOPMENT`, 설정 누락 subprocess 실패 및 토큰 경계 테스트 |
| SC-5 전체 회귀 | Met | Python 210 passed + 10 subtests, JS 5 passed |

## 3. Design vs Implementation

| Axis | Match | Notes |
|------|:-----:|-------|
| Structural | 100% | 설계 파일 및 테스트 모두 존재 |
| Functional | 100% | FR-01~05 충족 |
| Contract | 100% | 401/403/startup failure 계약 검증 |
| Runtime | 100% | 대상 14개 및 전체 suite 통과 |
| **Overall** | **100%** | runtime 공식 적용 |

## 4. Gap List and Iterate

초기 Check에서 서버 구현 갭은 없었다. 다만 UI 4곳과 라우터 문서가 “초기 비밀번호=사번”을 계속 안내해 정책을 우회하도록 유도하는 Important 갭을 발견했다. Iterate-1에서 관리자 발급 임시 비밀번호 안내로 모두 수정했고 재검증했다.

## 5. Decision Verification

| Decision | Followed | Outcome |
|----------|:--------:|---------|
| Option A 최소 변경 | Yes | 기존 보안 구성 재사용, 스키마 변경 없음 |
| 공개 오류 통일 | Yes | 계정 열거 차단 |
| production fail-closed | Yes | 설정 누락 시 서버 시작 차단 |
| development LAN fallback | Yes | 개발 편의와 회귀 호환 유지 |

## 6. Remaining Issues

- Critical/Important: 없음.
- Info: 기존에 이미 생성된 약한 비밀번호 계정의 일괄 초기화는 별도 운영 마이그레이션 범위다.
- QA 사전 스캐너는 플러그인 Windows 경로 변환 오류로 실행되지 않았으며 애플리케이션 테스트와 무관하다.

