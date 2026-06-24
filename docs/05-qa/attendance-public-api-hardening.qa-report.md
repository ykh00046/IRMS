# attendance-public-api-hardening QA Report

> **Date**: 2026-06-18 · **Result**: QA_PASS

## 1. Scope and Environment

Plan/Design/Analysis의 보안 계약을 대상으로 L1 단위, L2 TestClient API 통합, 전체 회귀를 실행했다. 실제 UI 동작 변경은 안내 문구뿐이므로 별도 브라우저 E2E는 적용하지 않았다.

## 2. Pre-Release Scan

| Scanner | Result |
|---------|--------|
| bkit pre-release scanner | SKIP — Windows에서 플러그인 `/c/.../lib/qa` 경로를 Node가 해석하지 못함 |
| 대체 검증 | 전체 pytest, Node test, git diff 수동 범위 검토 |

## 3. Test Results

| Level | Test | Result |
|-------|------|:------:|
| L1 | 신규 계정 자동 생성 금지 | PASS |
| L1 | 미등록/미프로비저닝 공개 오류 통일 | PASS |
| L1 | 관리자 난수 임시 비밀번호 프로비저닝 | PASS |
| L2 | production 기본 토큰 누락 startup failure | PASS |
| L2 | loopback 무토큰 403 / 유효 토큰 통과 | PASS |
| L2 | development LAN-only 회귀 | PASS |
| Regression | `python -m pytest -q` | PASS — 210 passed, 10 subtests, 1 deprecation warning |
| Regression | `node --test tests/js/*.test.js` | PASS — 5/5 |

## 4. Security Assertions

- 로그인 과정에서 사번 기반 계정을 INSERT하지 않는다.
- 계정 존재 여부는 HTTP 응답으로 구분되지 않는다.
- 실제 실패 사유는 감사 로그에 보존된다.
- production은 토큰이 없으면 시작하지 않는다.
- 토큰은 기존 `hmac.compare_digest`로 비교된다.

## 5. Verdict

**QA_PASS.** Critical/Important 결함 0건. Starlette TestClient 쿠키 API deprecation warning 1건은 기존 테스트 코드의 비차단 경고다.

