# Attendance Month Alert Completion Report

> **Status**: Complete (post-hoc PDCA, no Plan/Design 단계 — 운영 피드백 기반 enhancement)
>
> **Project**: IRMS (원료 계량 시스템)
> **Feature**: attendance-month-alert
> **Author**: PDCA Skill (코드 리뷰 직후 작성)
> **Completion Date**: 2026-04-27
> **Commit**: `e24475a` on main

---

## 1. Summary

### 1.1 Feature Overview

`attendance-alert` v1.1.0(2026-04-24 아카이브)의 후속 enhancement.
**핵심 변경 두 축**:
1. **알림 정책**: 당일(`/today`) 1시간 폴링 → **이번 달 미처리(`/month`)** 정해진 슬롯(09:00 / 13:00 / 16:00) 알림
2. **폴러 견고화**: 503 fall-through, 재시작 후 중복 팝업, 비활성 슬롯 소비 등 운영 중 발견된 결함 수정

| Item | Content |
|------|---------|
| Feature | attendance-month-alert |
| Base | attendance-alert v1.1.0 (archive 2026-04) |
| Tray Version | 1.1.0 → **1.1.6** |
| Commit | `e24475a` (10 files, +374 / -60) |
| Test Result | **39/39 PASS** (신규 5 + 기존 18 + 부수 16) |
| Duration | ~3일 (1.1.1 ~ 1.1.6 누적) |

### 1.2 Completion Rate

```
┌─────────────────────────────────────────────────┐
│  Overall Completion: 100%                        │
├─────────────────────────────────────────────────┤
│  ✅ /month 엔드포인트 + 감지 함수:        Complete │
│  ✅ 슬롯 스케줄러 (09/13/16):              Complete │
│  ✅ 503 sentinel 정리:                     Complete │
│  ✅ 재시작 grace period:                   Complete │
│  ✅ 비활성 슬롯 미소비:                    Complete │
│  ✅ 한국어 UI 문구 정리:                   Complete │
│  ✅ 테스트 커버리지 추가:                  Complete │
│  ✅ 버전 1.1.6 bump:                       Complete │
└─────────────────────────────────────────────────┘
```

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | (none — 운영 피드백 기반 직접 진행) | ⛔ Skipped |
| Design | (none — 기존 attendance-alert 설계 위에 누적 변경) | ⛔ Skipped |
| Analysis | (post-hoc 코드 리뷰로 대체) — `code-review-2026-04-27` | ✅ |
| Report | Current document | ✅ Complete |

> **Note**: 본 enhancement는 정식 PDCA Plan/Design 단계 없이 운영 중 발견된 결함과 정책 변경 요청을 묶어 진행됨. 이전 `attendance-alert.design.md`(아카이브)가 base spec.

---

## 3. Completed Items

### 3.1 Functional Changes

| ID | Change | Status | Evidence |
|----|--------|--------|----------|
| FC-01 | `/api/public/attendance-alerts/month` 엔드포인트 추가 | ✅ | `src/routers/public_attendance_alert_routes.py:45-63` |
| FC-02 | `detect_month_anomalies(year_month)` — 월간 미처리 감지 | ✅ | `src/services/attendance_excel.py:444-461` |
| FC-03 | `_merge_anomaly_record()` 헬퍼로 today/month 공통 병합 로직 통합 | ✅ | `attendance_excel.py:359-388` |
| FC-04 | 트레이 폴러: 시간당 → **09:00 / 13:00 / 16:00 슬롯** 스케줄 | ✅ | `tray_client/src/attendance_alerts.py:22, 91-129` |
| FC-05 | 응답에 `dates: [YYYY-MM-DD, ...]` 포함 (정렬) | ✅ | `attendance_excel.py:457-460` |

### 3.2 Robustness Fixes (코드 리뷰 결과 도출)

| ID | Issue | Fix | Evidence |
|----|-------|-----|----------|
| RB-01 | 503 응답이 fall-through로 `raise_for_status()` 트리거 → 중복 로그 | `_FileLockedRetry` sentinel 도입, info-level 단일 로그 | `attendance_alerts.py:30-31, 154-156, 184-201` |
| RB-02 | 트레이 재시작 시 이미 처리된 슬롯이 다시 팝업 | `_stale_slot_key_on_startup()` — 30분 grace 적용 | `attendance_alerts.py:91-94, 120-129` |
| RB-03 | 비활성 상태에서도 슬롯이 소비되어 재활성화 시 다음 슬롯까지 대기 | disabled 시 `_last_processed_slot` 미갱신, 60초 재체크 | `attendance_alerts.py:99-115` |

### 3.3 Korean UI/UX

| ID | Before | After | Evidence |
|----|--------|-------|----------|
| UI-01 | "오늘 근태 알림 끄기 / 켜기" (월간 알림에서 의미 모호) | "**근태 알림 오늘만 끄기 / 켜기**" | `tray_client/README.md:32` |
| UI-02 | "해당 월에서 이상이 해소되면 다음 슬롯부터 자동으로 빠짐" (추상적) | "**해당 직원이 출근 기록을 보충하거나 ERP에서 처리되면** 다음 슬롯부터 자동으로 빠짐" | `tray_client/README.md:49` |
| UI-03 | (미문서화) 재시작 후 동작 | "트레이를 도중에 재시작해도 이미 떴던 슬롯은 다시 띄우지 않음 (30분 그레이스)" 명시 | `tray_client/README.md:51` |
| UI-04 | 팝업 요약 "오늘 확인이 필요한 인원이 있습니다." | "**이번 달** 확인이 필요한 인원이 있습니다." | `tray_client/src/attendance_popup.py:90` |

### 3.4 Test Additions

| Test | Purpose | File:Line |
|------|---------|-----------|
| `test_detect_month_anomalies_returns_unresolved_rows_across_month` | `/month` 기본 감지 | `tests/test_attendance_excel_anomaly_resolution.py:51-80` |
| `test_detect_month_anomalies_merges_dates_and_dedupes_issues` | **(신규)** 다중 날짜 병합 + 이슈 dedupe | `:82-111` |
| `test_schedule_slot_keys_follow_9_13_16` | 슬롯 키 경계값 (08:59/09:00/14:30/16:05) | `tests/test_notice_tray_behaviour.py:195-214` |
| `test_duplicate_signature_is_suppressed_within_same_slot_only` | 같은 슬롯 내 중복 억제 / 다른 슬롯 재발생 | `:216-242` |
| `test_stale_slot_on_startup_marks_recent_slot_as_processed` | **(신규)** 30분 grace 검증 | `:244-263` |
| `test_disabled_state_does_not_consume_slot` | **(신규)** 비활성 → 슬롯 미소비 → 재활성화 시 발생 | `:265-296` |

---

## 4. Quality Metrics

### 4.1 Test Results

| Metric | Result |
|--------|--------|
| 전체 테스트 | **39 PASSED** |
| 신규 테스트 | **3** (병합 dedupe, grace, disabled) |
| 실행 시간 | 1.15s |
| 실패 건수 | 0 |

### 4.2 Code Review Findings & Resolutions

| Severity | Item | Resolution |
|----------|------|------------|
| 🔴 High | 503 fall-through 명시적 처리 | ✅ `_FileLockedRetry` sentinel 도입 |
| 🟡 Med | 트레이 재시작 시 슬롯 중복 방지 | ✅ 30분 grace period |
| 🟡 Med | 비활성 상태 슬롯 미소비 처리 | ✅ disabled non-consume |
| 🟡 Med | 다중 날짜 병합 테스트 추가 | ✅ 신규 테스트 1건 |
| 🟢 Low | README 한국어 문구 정리 | ✅ 4건 정리 |
| 🟢 Low (deferred) | `/month` 무인증 엔드포인트 PII 노출 검토 | ⏭ 정책 결정 사안 — 코드 변경 보류 |
| 🟢 Low (deferred) | Excel 캐싱 (성능) | ⏭ 현 규모(7대 × 3슬롯)에서 부하 미미 |

---

## 5. Key Design Decisions & Rationale

### 5.1 시간당 → 슬롯 기반 알림

**변경**: 60분 폴링 → 09:00 / 13:00 / 16:00 정해진 슬롯
**근거**:
- 하루 14회(09–22시 매시간) 알림은 과다. 현장은 출근(09)/오전 마감(13)/퇴근 직전(16) 시점에만 인지하면 충분
- ERP 갱신 시점과 자연스럽게 맞물림
- "월간 미처리"로 시야가 넓어진 만큼, 빈도는 낮춤으로써 정보 가치/소음 균형

**Trade-off**: 16:00 이후 발생한 이상은 다음 날 09:00에야 인지됨. 운영상 허용 가능.

### 5.2 503 sentinel 예외

**변경**: `_FileLockedRetry` 내부 예외 도입, debug + warning 중복 로그 제거
**근거**:
- 기존 코드: 503 분기에서 debug 로그 → fall-through → `raise_for_status()` → outer except에서 warning 로그(중복)
- 의도(retry)는 동일하나 가독성/로그 노이즈 모두 손해
- sentinel 예외로 "정상 retry 경로"와 "예기치 못한 네트워크 오류"를 코드 레벨에서 구분

**구현**: `attendance_alerts.py:30-31` (예외 정의), `:154-156` (외부 캐치), `:194-200` (내부 raise)

### 5.3 30분 startup grace

**변경**: 트레이 시작 시 현재 슬롯 시작 시각으로부터 30분 이상 경과 시 처리됨으로 마킹
**근거**:
- 이전 동작: 09:35 재부팅 → 09:00 슬롯이 이미 메모리 사라져서 다시 팝업 → 사용자가 본 동일 알림 재출현
- 영속화(disk persist) 대신 시간 기반 휴리스틱 채택 — 코드 단순성 + 합리적 기본값
- 30분: 현장에서 "방금 본 알림"의 reasonable cutoff

**Trade-off**: 정확한 "이미 봤다" 추적은 아니지만 한 번의 추가 팝업 방지가 주 목적이므로 휴리스틱으로 충분

**구현**: `attendance_alerts.py:91-94, 120-129`

### 5.4 비활성 상태 슬롯 미소비

**변경**: `is_enabled=False`일 때 `_last_processed_slot`을 갱신하지 않음
**근거**:
- 사용자가 08:55 알림 끄기 → 09:00 슬롯 무폴링 통과 → 09:30 다시 켜도 13:00까지 알림 없음 (이전 동작)
- 사용자 의도: "지금은 끄지만 나중에 다시 보고 싶음"
- 슬롯이 아직 진행 중이면 켜자마자 폴링되어야 자연스러움

**구현**: `attendance_alerts.py:99-115` — disabled면 `wait_seconds = SLOT_RETRY_SECONDS(60)`로 짧게 재체크, 슬롯 키는 그대로 유지

### 5.5 `_merge_anomaly_record()` 추출

**변경**: today/month 공통 병합 로직을 헬퍼 함수로 분리
**근거**:
- `detect_today_anomalies`와 `detect_month_anomalies`가 동일한 emp_id 병합 + 이슈 중복 제거 로직을 가짐
- `include_dates` 플래그로 month만 dates 리스트 누적
- 향후 슬롯별 알림 등 추가 시 한 곳에서 수정 가능

**구현**: `src/services/attendance_excel.py:359-388`

---

## 6. File Changes Summary

### 6.1 Server Side

| File | Change | Lines | Purpose |
|------|--------|-------|---------|
| `src/services/attendance_excel.py` | `_merge_anomaly_record` 추출 + `detect_month_anomalies` 추가 | +52 / -14 | 월간 미처리 감지 |
| `src/routers/public_attendance_alert_routes.py` | `/month` 엔드포인트 + 도크스트링 갱신 | +20 / -3 | 월간 API |

### 6.2 Tray Client Side

| File | Change | Lines | Purpose |
|------|--------|-------|---------|
| `tray_client/src/attendance_alerts.py` | 슬롯 스케줄러 + sentinel 예외 + grace + disabled non-consume | +99 / -15 | 폴러 견고화 |
| `tray_client/src/attendance_popup.py` | 요약 문구 월간 표현 | +1 / -1 | UI 문구 |
| `tray_client/README.md` | 메뉴 명칭, 동작 설명, 버전 | +14 / -14 | 문서화 |
| `tray_client/build/installer.iss` | MyAppVersion 1.1.6 | +2 / -2 | 빌드 |
| `tray_client/build/build.bat` | 출력 파일명 | +1 / -1 | 빌드 |

### 6.3 Tests

| File | Change | Lines |
|------|--------|-------|
| `tests/test_attendance_excel_anomaly_resolution.py` | month 감지 + dedupe 테스트 | +73 / -6 |
| `tests/test_notice_tray_behaviour.py` | 슬롯/grace/disabled/중복 억제 테스트 | +101 / -1 |

**Total**: 10 files, +374 / -60

---

## 7. API Changes

### 7.1 New Endpoint

**Route**: `GET /api/public/attendance-alerts/month`
**Authentication**: None (InternalNetworkOnlyMiddleware)
**Response 200**:
```json
{
  "month": "2026-04",
  "date": "2026-04-27",
  "total": 2,
  "items": [
    {
      "emp_id": "171013",
      "name": "김민호",
      "department": "생산1팀",
      "shift_time": "주간",
      "issues": ["출근 누락", "퇴근 누락"],
      "dates": ["2026-04-21", "2026-04-23"]
    }
  ]
}
```

**Errors**:
- 404 `MONTH_FILE_NOT_FOUND`
- 503 `FILE_LOCKED_RETRY`
- 500 `FILE_FORMAT_INVALID`

### 7.2 `/today` 엔드포인트 — 유지

기존 `/api/public/attendance-alerts/today`는 변경 없이 유지(다른 클라이언트 호환). 트레이만 `/month` 사용으로 전환.

### 7.3 Database Changes

**없음** (엑셀 기반 읽기만)

---

## 8. Known Limitations & Caveats

### 8.1 후속 검토 사안

| 항목 | 사유 | 권장 조치 |
|------|------|----------|
| `/month` 무인증 + 한 달치 PII | 내부망 트러스트 기반이지만 today보다 노출 범위 넓음 | 정책 결정: 그대로 유지 / Cloudflare Tunnel 전환 시 IP 화이트리스트 강화 |
| 매 슬롯마다 월 엑셀 전체 재파싱 | 7대 × 3슬롯 = 21회/일. 현 규모에서 부하 미미 | 향후 현장 PC 증설 시 in-process 캐시(5분 TTL) 도입 |
| `_last_processed_slot` 메모리 영속화 안됨 | grace period(30분)로 휴리스틱 보완 | 향후 disk persist 검토 (필요 시) |
| 16:00 이후 발생 이상은 다음 날 09:00 인지 | 슬롯 빈도 트레이드오프 | 추가 슬롯 필요 시 `SCHEDULED_ALERT_HOURS` 상수 조정 |

### 8.2 검증 한계

| 항목 | 이유 | 대체 방안 |
|------|------|----------|
| 7대 동시 슬롯 정각 폴링 | 실배포 필요 | 설계상 thundering herd는 미미하나 향후 monitoring |
| 자정 경과 자동 복귀 | 타이밍 재현 불가 | 기존 attendance-alert에서 검증된 로직 재사용 |
| Windows 토스트 실제 팝업 렌더링 | 본 환경에서 tray 실행 불가 | 단위 테스트 + 정적 검증 |

---

## 9. Deployment Procedure

### 9.1 Server Deployment

```bash
cd C:\X\IRMS
git pull origin main
# 커밋 e24475a 포함 확인
# update_and_run.bat 실행
```

**스모크 테스트**:
```bash
# 내부망에서:
curl http://localhost:9000/api/public/attendance-alerts/month
# 200 OK with month/date/total/items
```

### 9.2 Tray Client Deployment

1. EXE 재빌드:
   ```bash
   cd tray_client/build
   build.bat
   # → Output\IRMS-Notice-Setup-1.1.6.exe
   ```
2. 7대 현장 PC에 `IRMS-Notice-Setup-1.1.6.exe` 덮어쓰기 설치 (AppId 동일)
3. 트레이 재시작 후 메뉴 확인:
   - "**근태 알림 오늘만 끄기 / 켜기**" 표시 (이전 "오늘 근태 알림" 텍스트가 아니어야 함)
4. 다음 슬롯(09:00 / 13:00 / 16:00) 도래 시 팝업 확인

---

## 10. Status

### 10.1 PDCA Cycle

| Phase | Status | Note |
|-------|--------|------|
| Plan | ⛔ Skipped | 운영 피드백 기반 enhancement |
| Design | ⛔ Skipped | 기존 attendance-alert.design.md base 활용 |
| Do | ✅ Complete | 커밋 `e24475a` |
| Check | ✅ Complete | 코드 리뷰(7건 발견 → 5건 resolved, 2건 deferred) + 테스트 39 PASS |
| Act | ✅ Complete | 본 보고서 |

### 10.2 Match Assessment

설계 문서가 없으므로 정량적 Match Rate 산출은 불가. 대신 **요구사항 충족도** 기준으로:

| Requirement | Status |
|-------------|:------:|
| 월간 미처리만 알림 (당일 단발 → 누적 미처리) | ✅ |
| 09:00 / 13:00 / 16:00 정해진 슬롯 | ✅ |
| 같은 슬롯 내 중복 팝업 억제 | ✅ |
| 다른 슬롯에서는 다시 표시 (해소 미확인) | ✅ |
| 재시작 후 동일 슬롯 재발생 방지 | ✅ |
| 비활성화 시 의도 보존 (재활성 시 즉시 작동) | ✅ |
| 503/404 견고한 처리 | ✅ |
| 한국어 UI 일관성 | ✅ |

**Effective Match Rate**: 100% (8/8 요구사항 충족)

---

## 11. Lessons Learned

### 11.1 Keep

- **운영 중 결함을 묶어 단일 사이클로 처리**: 개별 hotfix 5건을 1 commit으로 묶어 변경 리스크와 배포 횟수 절감
- **코드 리뷰 → 우선순위 → 테스트 추가** 흐름이 효과적: 발견 → 수정 → 검증을 한 세션에 완료
- **Sentinel 예외로 retry 의도 명시**: 단순 status code 분기보다 호출자 측 가독성 우수
- **기존 헬퍼 추출(`_merge_anomaly_record`)로 today/month 일관성 보장**: 향후 슬롯 추가 시 한 곳만 수정

### 11.2 Problem

- **Plan/Design 문서 부재**: 정식 PDCA 단계를 건너뛰고 직행. 본 enhancement는 작아서 가능했지만 변경 의도 추적성이 약해짐 (이 보고서가 부분 보완)
- **재시작 grace는 휴리스틱**: 30분 기준은 합의된 값이 아닌 디폴트. 운영 후 fine-tune 필요할 수 있음
- **무인증 `/month` 엔드포인트 노출 범위 확장**에 대한 정책 합의 미선행: 코드 변경 보류, 후속 결정 필요

### 11.3 Try

- **enhancement용 lightweight Plan**: 정식 Plan/Design은 부담스럽지만 commit message + 본 report 사이를 잇는 짧은 ENHANCEMENT.md 같은 산출물 검토
- **`_last_processed_slot` 영속화**: PC 잦은 재시작 환경에서 휴리스틱 한계 발견 시 디스크 저장 추가
- **운영 모니터링 카드**: 관리자 웹에 "오늘 슬롯 알림 발송 통계" 카드 추가하면 7대 PC 동작 가시성 향상
- **PII 노출 정책 합의 후 조치**: Cloudflare Tunnel 전환과 묶어 "내부망 + 인증" 모델 전환 검토

---

## 12. Next Steps

### 12.1 Immediate

- [ ] `IRMS-Notice-Setup-1.1.6.exe` 재빌드
- [ ] 7대 현장 PC 배포 (덮어쓰기 설치)
- [ ] 다음 슬롯(09:00 / 13:00 / 16:00) 도래 시 정상 팝업 확인
- [ ] 트레이 재시작 시나리오: 슬롯 직후 재시작해도 중복 팝업 없는지 확인

### 12.2 Follow-up

| Item | Priority | Note |
|------|----------|------|
| `/month` 무인증 정책 재검토 | Medium | Cloudflare Tunnel 전환과 함께 |
| 운영 모니터링 카드 (관리자 웹) | Low | 선택 사항 |
| `_last_processed_slot` 영속화 | Low | 운영 데이터 수집 후 결정 |
| Excel 캐싱 (5분 TTL) | Low | PC 증설 시 |

### 12.3 Active Features Status

| Feature | Status |
|---------|--------|
| status-operator-view | active |
| cloudflare-tunnel-access | plan |
| **attendance-month-alert** | **completed (this report)** |

---

## 13. Changelog

### v1.1.6 (2026-04-27)

**Added**:
- 서버: `GET /api/public/attendance-alerts/month` 엔드포인트
- 서버: `detect_month_anomalies(year_month)` — 월간 미처리 감지
- 서버: `_merge_anomaly_record()` 헬퍼로 today/month 공통 로직
- 트레이: 슬롯 스케줄러 (09:00 / 13:00 / 16:00)
- 트레이: `_FileLockedRetry` sentinel 예외
- 트레이: 시작 시 30분 grace period
- 테스트: 다중 날짜 병합, grace, disabled 동작 검증

**Changed**:
- 트레이 폴링: `/today` 시간당 → `/month` 슬롯 기반
- 비활성 상태가 슬롯을 소비하지 않도록 변경
- README: "오늘 근태 알림 끄기" → "근태 알림 오늘만 끄기"
- 팝업 요약: "오늘" → "이번 달"
- 버전: 1.1.0 → 1.1.6

**Fixed**:
- 503 응답에서 debug + warning 중복 로깅
- 트레이 재시작 시 처리된 슬롯 재팝업
- 비활성 → 재활성화 시 다음 슬롯까지 알림 누락

---

## Sign-Off

**Feature**: attendance-month-alert
**Commit**: `e24475a`
**Tests**: 39 PASSED / 0 FAILED
**Status**: ✅ COMPLETE
**Next Action**: 1.1.6 EXE 재빌드 → 7대 현장 PC 덮어쓰기 설치 → 슬롯 동작 모니터링

**Note**: 정식 PDCA Plan/Design 단계는 생략(운영 피드백 기반). 향후 유사 enhancement에는 lightweight 계획 문서 도입 검토. `/month` 엔드포인트의 무인증 PII 노출 범위 확장은 정책 결정 사안으로 별도 검토 보류.
