# Gap 분석 — lot-expiry-tracking (PDCA Check)

> 분석일 2026-06-02 · gap-detector 에이전트 + 통합 QA 기반 · Match Rate **99%**

## 1. 종합 점수

| 영역 | 점수 | 판정 |
|------|:----:|:----:|
| 설계 일치(§2~§8) | 99% | OK |
| Plan 성공기준(§5, 8항목) | 100% | OK |
| 규약 준수(CLAUDE.md/메모리) | 100% | OK |
| **종합** | **~99%** | OK (≥90%) |

## 2. 설계 섹션별 대조

| 설계 | 구현 | 결과 |
|------|------|------|
| §2 데이터 모델 `material_lots` (11컬럼, CHECK, FK, 인덱스 2종, IF NOT EXISTS) | `migrations.py` 동일 + `_ALLOWED_TABLES` 갱신 | ✅ 100% |
| §3.1 순수 판정 `expiry_state`/`days_until` (경계 포함) | 시그니처·경계 동일 | ✅ 100% |
| §3.2 쓰기 `register/consume/discard` (검증·자동 depleted·폐기 사유) | 전 검증 구현, caller-commit | ✅ 100% |
| §3.3 조회/집계 `list_lots`/`expiry_alert` (정렬·필터·payload) | 동형 | ✅ 100% |
| §4 API 6 엔드포인트 (operator/manager 튜플) + audit + CSV 방어 | 전부 구현 | ✅ 100% |
| §4.1 Pydantic 3모델 | 필드 일치 | ✅ 100% |
| §5 프런트(탭/모달/대시카드/CSRF) | 구현 | ✅ 98%→100%(반복 후) |
| §6 권한·보안 | operator/manager dependency 강제 | ✅ 100% |
| §7 테스트 9 시나리오(L1~L9) | 13개 테스트 전부 통과 | ✅ 100% |
| §8 회귀 방지(append-only) | 기존 무변경, include만 | ✅ 100% |

## 3. 발견된 편차 및 조치

| 편차 | 내용 | 조치 |
|------|------|------|
| `no_expiry` 배지 색상 | 설계=중립, 구현=`stock-ok`(녹색) | **Act 반복으로 코드 수정** — `lot.js` STATE_CLASS `no_expiry:""`(중립)로 일치 |

- gap-detector가 테스트를 11개로 집계했으나 실제 13개(pytest 출력 기준). 집계 오류로 코드 영향 없음.

## 4. Plan §5 성공기준 (8/8 충족)

1. ✅ LOT 등록(manager), 잔여=입고수량
2. ✅ LOT 목록(operator) + 유통기한 상태
3. ✅ 소진/폐기, 잔여 0 → 자동 depleted
4. ✅ 대시 만료 알림(manager), 만료→임박 상위 N, 0건 미노출
5. ✅ CSV export + 수식 인젝션 방어
6. ✅ operator/비인증 쓰기·대시 차단(L8)
7. ✅ material_lots 테이블+인덱스 마이그레이션(L9)
8. ✅ 회귀 0 — 164 passed

## 5. QA 결과 (통합)

- **서비스 생명주기**: 등록→목록 정렬(만료/임박/정상/무기한)→부분소진(active)→전량소진(depleted)→폐기(discarded)→대시 알림 제외 검증 ✅
- **라우터 인증 통합**: 매니저(120206) 로그인 → CSRF 등록 200 → operator 목록 → 대시 알림(임박 1건) → CSV(text/csv) → 감사 로그 1건 기록 ✅

## 6. 판정

**Match Rate 99% ≥ 90% → 완료(report) 진행.** 추가 Act 불필요(편차 1건은 반복에서 이미 해소).
