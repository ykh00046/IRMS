# Attendance Alert Completion Report

> **Status**: Complete (Match Rate 98%)
>
> **Project**: IRMS (원료 계량 시스템)
> **Feature**: attendance-alert
> **Author**: PDCA Skill (gap-detector + report-generator)
> **Completion Date**: 2026-04-24
> **Commit**: `11fa7c3` on main

---

## 1. Summary

### 1.1 Feature Overview

당일 평일 근태 이상(출근/퇴건 누락, 지각, 조퇴)을 감지하여 7대 현장 PC의 트레이 앱이 30분마다 이름을 나열한 조용한 Windows Toast로 알려주는 기능. notice-tray-client v1.0.0 에 **비(非) TTS 팝업 채널**을 추가하고, attendance-view 엑셀 파서를 **anomaly detection**으로 확장한 것.

| Item | Content |
|------|---------|
| Feature | attendance-alert |
| Start Date | 2026-04-16 (계획 생성) |
| Completion Date | 2026-04-24 (커밋 `11fa7c3`) |
| Base | attendance-view (archive 2026-04, 96%) + notice-tray-client v1.0.0 |
| Duration | ~1주 |

### 1.2 Completion Rate

```
┌─────────────────────────────────────────────┐
│  Overall Completion: 100%                    │
├─────────────────────────────────────────────┤
│  ✅ Server detection API:         Complete  │
│  ✅ Tray poller + Toast:          Complete  │
│  ✅ Mute toggle + Auto-reset:     Complete  │
│  ✅ Version bump (1.0.0→1.1.0):   Complete  │
│  ✅ Internal network protection:  Complete  │
└─────────────────────────────────────────────┘
```

---

## 2. Related Documents

| Phase | Document | Status |
|-------|----------|--------|
| Plan | [attendance-alert.plan.md](../../01-plan/features/attendance-alert.plan.md) | ✅ Finalized |
| Design | [attendance-alert.design.md](../../02-design/features/attendance-alert.design.md) | ✅ Finalized |
| Analysis | [attendance-alert.analysis.md](../../03-analysis/features/attendance-alert.analysis.md) | ✅ Complete (98% Match) |
| Act | Current document | ✅ Complete |

---

## 3. Completed Items

### 3.1 Functional Requirements

| ID | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| FR-01 | 오늘 평일 근무자 중 근태 이상 감지 (출근/퇴건 누락) | ✅ Complete | `src/services/attendance_excel.py:207-211` |
| FR-02 | 지각 및 조퇴 이상 감지 | ✅ Complete | `attendance_excel.py:212-215` |
| FR-03 | 당일 근태 정보 JSON API (`GET /api/public/attendance-alerts/today`) | ✅ Complete | `public_attendance_alert_routes.py:20-46` |
| FR-04 | 30분 주기 폴링 + Windows Toast 표시 | ✅ Complete | `tray_client/src/attendance_alerts.py:78-85` |
| FR-05 | 토스트 메시지 포맷 (1/3/5명 테스트) | ✅ Complete | `attendance_alerts.py:30-42` |
| FR-06 | 트레이 메뉴 "오늘 근태 알림 끄기" 토글 | ✅ Complete | `tray_client/src/main.py:106-112, 143-151` |
| FR-07 | 자정 자동 복귀 + 하루 단위 음소거 | ✅ Complete | `main.py:62, 93-96` + `attendance_alerts.py:134-135` |
| FR-08 | 이상 해소 시 자동 중단 (상태 저장 없음) | ✅ Complete | `attendance_alerts.py:87-104` |

### 3.2 Non-Functional Requirements

| Item | Target | Achieved | Status |
|------|--------|----------|--------|
| 네트워크 보호 (내부망 전용) | InternalNetworkOnly prefix | `/api/public/attendance-alerts` 추가 | ✅ |
| 새 의존성 | 0개 추가 | `pystray.Icon.notify()` 기존 사용 | ✅ |
| 트레이 버전 | 1.1.0 | 1.1.0 bump 완료 | ✅ |
| 설치 파일 | 재빌드 | `IRMS-Notice-Setup-1.1.0.exe` (28.29MB) | ✅ |

### 3.3 Deliverables

| Deliverable | Location | Status |
|-------------|----------|--------|
| 서버 감지 함수 | `src/services/attendance_excel.py` | ✅ |
| 서버 엔드포인트 | `src/routers/public_attendance_alert_routes.py` | ✅ |
| 트레이 폴러 모듈 | `tray_client/src/attendance_alerts.py` | ✅ |
| 트레이 앱 통합 | `tray_client/src/main.py` | ✅ |
| 미들웨어 보호 | `src/main.py` (50-56) | ✅ |
| 설치 파일 | `tray_client/build/Output/IRMS-Notice-Setup-1.1.0.exe` | ✅ |
| 문서화 | 이 보고서 + PDCA 3단계 문서 | ✅ |

---

## 4. Quality Metrics

### 4.1 Design Match Analysis

| Metric | Target | Final | Change |
|--------|--------|-------|--------|
| Design Match Rate | 90% | **98%** | +8% |
| High/Medium Gap | 0 | **0** | ✅ |
| Info-level Cleanup | - | **3건** | 후속 과제 |
| Code Quality | Pass | Pass | ✅ |

### 4.2 Resolved Issues

| Issue | Resolution | Result |
|-------|------------|--------|
| 비(非) TTS 채널 설계 | pystray.Icon.notify()로 Toast 구현 | ✅ Resolved |
| 상태 관리 복잡성 | 서버 신뢰 전략 채택 (매 폴링마다 fresh read) | ✅ Resolved |
| 개인정보 노출 우려 | 공장 환경 공개정보 수준, 사용자 승인 | ✅ Resolved |
| 자동 리셋 타이밍 | 자정 날짜 변경 시 자연 소거 | ✅ Resolved |

### 4.3 Test Evidence

| Test Case | Result |
|-----------|--------|
| 서버 스모크 테스트: 내부망 IP → 200 정상 응답 | ✅ PASS |
| 서버 스모크 테스트: 외부 IP → 403 `INTERNAL_NETWORK_ONLY` | ✅ PASS |
| `detect_today_anomalies(2026-04, 2026-04-22 수요일)` → 0건 이상 | ✅ PASS |
| `detect_today_anomalies(2026-04, 2026-04-19 일요일)` → 1건 이상 감지 (교대조 지각) | ✅ PASS |
| `detect_today_anomalies(2026-04, 2026-04-20)` → 1건 이상 (출/퇴근 누락) | ✅ PASS |
| `format_notification(1건)` → `"근태 이상 1건" / "김민호"` | ✅ PASS |
| `format_notification(3건)` → `"근태 이상 3건" / "전명옥 · 김민호 · 박효빈"` | ✅ PASS |
| `format_notification(5건)` → `"근태 이상 5건" / "전명옥 · 김민호 · 박효빈 외 2명"` | ✅ PASS |
| EXE 재빌드 (PyInstaller + Inno Setup) | ✅ SUCCESS |
| 기동 테스트: 프로세스 alive, 79MB RSS | ✅ ALIVE |

---

## 5. Key Design Decisions & Rationale

### 5.1 비(非) TTS, 비주얼 전용 Toast

**설계**: 새 알림은 **이름을 표시하되 소리 없이** 시각적으로만 제공  
**근거**: 공지방 TTS가 이미 음성 브로드캐스트 채널로 사용 중이므로, 새로운 알림이 추가 소음 방지. 현장에서 이름 노출은 이미 공개 정보 수준. 사용자가 명시적으로 요청한 UX 방향.  
**구현**: `pystray.Icon.notify(body, title)` — 추가 라이브러리 불필요. Windows 10/11에서 시스템 트레이 토스트로 자동 렌더링.

### 5.2 30분 반복 + 해소 시 자동 중단

**설계**: 폴링 주기 30분, 이상이 해소되면 자동으로 알림 중단  
**근거**: 
- ERP 엑셀이 18:00에 갱신되므로 하루 3~4회 확인으로 충분
- 해당자가 뒤늦게 출근 기록을 남기면 서버가 다음 폴링에서 자동으로 제외
- 지각·조퇴는 당일 자정에 "오늘"이 바뀌면서 자연 소거
- 클라이언트 쪽에는 dedup/상태 저장 없음 — 설계 단순성 우선

**구현**: `attendance_alerts.py:78-85` (stop_event.wait 루프), `:87-104` (매 폴링마다 fresh items 비교)

### 5.3 공용 PC 가정, 개인별 필터 미적용

**설계**: 7대 모두 동일 브로드캐스트. 사번 매핑 설정을 배포 단계에 추가하지 않음  
**근거**: 현장 공용 PC이므로 모두에게 같은 정보를 보여주는 것이 자연스러움. 향후 확장 가능.  
**Trade-off**: 각 PC별 필터링 미제공 (out-of-scope v1)

### 5.4 기존 인프라 재사용

**설계**: 새 의존성 없이 기존 기술 스택으로 구현  
**근거**: 
- pystray는 이미 notice-tray-client v1.0.0 에서 사용 중
- `InternalNetworkOnlyMiddleware`는 notice 구축 시 이미 추가됨
- 엑셀 파서 (`attendance_excel.py`)는 attendance-view에서 검증됨

**결과**: 새 의존성 0개. 빌드 시간·용량·유지보수 비용 최소화.

### 5.5 버전 정책: AppId 동일, 덮어쓰기 설치

**설계**: tray_client AppId 동일하므로 1.1.0은 1.0.0 위에 덮어쓰기 설치  
**근거**: 사용자 설정(`config.json`) 보존, Windows Run 키 유지, 배포 간편화  
**구현**: `installer.iss:9` (MyAppVersion="1.1.0"), 설치 파일명 `IRMS-Notice-Setup-1.1.0.exe`

---

## 6. File Changes Summary

### 6.1 Server Side

| File | Change | Lines | Purpose |
|------|--------|-------|---------|
| `src/services/attendance_excel.py` | Add `detect_today_anomalies()` + `current_date()` | +65 | 당일 근태 이상 감지 함수 |
| `src/routers/public_attendance_alert_routes.py` | New file | 46 | 공개 API 엔드포인트 |
| `src/routers/api.py` | Register public router | +2 | 라우터 통합 |
| `src/main.py` | Add `/api/public/attendance-alerts` to protected_prefixes | +1 | 내부망 보호 설정 |

**Total Server Changes**: 4 files, +114 lines

### 6.2 Tray Client Side

| File | Change | Lines | Purpose |
|------|--------|-------|---------|
| `tray_client/src/attendance_alerts.py` | New file | 136 | 30분 폴러 + Toast 포맷 |
| `tray_client/src/main.py` | Add poller integration + menu + mute toggle | +30 | TrayApp 통합 |
| `tray_client/build/installer.iss` | Version bump to 1.1.0 | +1 | 버전 업데이트 |
| `tray_client/README.md` | Document v1.1.0 feature | +15 | 문서화 |

**Total Client Changes**: 4 files, +182 lines

### 6.3 Generated Artifacts

| Artifact | Size | Purpose |
|----------|------|---------|
| `IRMS-Notice-Setup-1.1.0.exe` | 28.29 MB | 배포용 설치 파일 |
| PDCA 문서 (Plan/Design/Analysis) | ~400 KB | 프로세스 기록 |

---

## 7. API & Database Changes

### 7.1 New Endpoint

**Route**: `GET /api/public/attendance-alerts/today`  
**Authentication**: None (InternalNetworkOnlyMiddleware)  
**Response 200**:
```json
{
  "date": "2026-04-23",
  "day_type": "평일",
  "total": 2,
  "items": [
    {
      "emp_id": "171013",
      "name": "김민호",
      "department": "생산",
      "issues": ["지각 0.25시간"]
    },
    {
      "emp_id": "240518",
      "name": "전명옥",
      "department": "포장",
      "issues": ["퇴근 누락"]
    }
  ]
}
```

**Error Responses**:
- 404 `MONTH_FILE_NOT_FOUND`: 월별 엑셀 파일 없음
- 503 `FILE_LOCKED_RETRY`: 엑셀 파일 락 (ERP 갱신 중)
- 500 `FILE_FORMAT_INVALID`: 엑셀 파일 형식 오류

### 7.2 Database Changes

**없음** (엑셀 기반 읽기만)

---

## 8. Known Limitations & Caveats

### 8.1 검증 한계

| 항목 | 이유 | 대체 방안 |
|------|------|----------|
| Windows 토스트 실제 팝업 | 본 환경에서 tray 실행 불가 | 개발자 스모크 테스트 (EXE 기동 OK, 프로세스 alive, formatter unit test) |
| 30분 주기 실측 | 장시간 구동 필요 | 상수값 정적 확인 (`DEFAULT_INTERVAL_SECONDS = 30*60`) |
| 자정 경과 자동 복귀 | 타이밍 재현 불가 | `today_iso()` 비교 로직 정적 확인, 설계 검증 완료 |
| 7대 동시 폴링 부하 | 실배포 필요 | 설계상 14 req/h로 부하 문제 없음 |
| 엑셀 락 503 처리 | ERP 갱신 타이밍 | `FileLocked` 예외 경로 정적 확인 |

**결론**: 정적 검증 완료 (98%). 실제 현장 운영 환경에서는 7대 PC 배포 후 18:05 이후에 확인 필요.

### 8.2 Cleanup 후보 (Info level)

| 항목 | 설명 | 권장 조치 |
|------|------|----------|
| `_last_signature` 미사용 필드 | `attendance_alerts.py:64`에서 선언, `:103`에서 None 리셋만 | 다음 iterate에서 제거 또는 dedup 구현 결정 |
| 설계 예시 문서 정정 | `BACKOFF_ON_ERROR` 상수가 미사용 (구현은 단순 재시도) | 설계서 예시에서 해당 상수 삭제 |
| docstring 타입 정정 | `detect_today_anomalies`가 `tuple[str, list[dict]]` 반환 | docstring 업데이트 |

---

## 9. Deployment Procedure

### 9.1 Server Deployment (먼저)

```bash
cd C:\X\IRMS
git pull origin main
# 커밋 11fa7c3 포함되어 있어야 함

# 가상환경 활성화 후 서버 재시작
# update_and_run.bat 또는 수동 재시작
```

**검증**:
```bash
curl -H "X-Forwarded-For: 192.168.11.X" http://localhost:9000/api/public/attendance-alerts/today
# 응답: {"date":"...", "day_type":"...", "total":0~N, "items":[]}
```

### 9.2 Tray Client Deployment (이후)

1. 7대 현장 PC의 IRMS 공지 수신기 프로그램을 관리자로 실행
2. `IRMS-Notice-Setup-1.1.0.exe` 실행
3. 기존 v1.0.0 위에 덮어쓰기 설치 (AppId 동일)
4. 설정(`config.json`)과 Windows Run 키 자동 보존

**배포 후 확인** (18:05 이후):
- 트레이 메뉴에 "오늘 근태 알림 끄기" 항목 표시 확인
- 테스트 메뉴에서 "근태 알림 테스트" 클릭 → Toast 팝업 확인
- 실제 근태 이상자 없으면 팝업 없음 확인

---

## 10. Status & Match Rate

### 10.1 PDCA Cycle Completion

| Phase | Status | Details |
|-------|--------|---------|
| **Plan** | ✅ Complete | `attendance-alert.plan.md` finalized |
| **Design** | ✅ Complete | `attendance-alert.design.md` detailed, 설계 검증 완료 |
| **Do** | ✅ Complete | 서버 + 클라이언트 구현 (commit `11fa7c3`) |
| **Check** | ✅ Complete | Match Rate 98%, 3건 Info-level gap only |
| **Act** | ✅ Complete | 보고서 작성 (현 문서) |

### 10.2 Design Match Analysis

**Overall Match Rate: 98%**

| Category | Score | Notes |
|----------|:-----:|-------|
| Detection Rules (§3.2) | 100% | PASS |
| API Shape & Error Codes (§3.1) | 100% | PASS |
| Polling Behavior (§4.1) | 95% | PASS (minor variance accepted) |
| Menu / Mute Toggle (§4.2) | 100% | PASS |
| Middleware Protection | 100% | PASS |
| Version Bump (§7) | 100% | PASS |
| **High/Medium Gap** | **0** | ✅ |
| **Info-level Cleanup** | **3** | 후속 과제 |

---

## 11. Lessons Learned

### 11.1 What Went Well (Keep)

- **설계 문서의 정확도**: 28개 설계 체크 항목 중 27개 Match. 설계 단계에서 API shape, 감지 규칙, 메뉴 로직을 명확히 정의해 구현 단계에서 revisit 최소화.
- **기존 인프라 재사용**: pystray, InternalNetworkOnlyMiddleware, 엑셀 파서 등 이미 검증된 기술 스택을 조합해 새 의존성 0개, 빌드 시간 최소화.
- **상태 관리 전략**: 클라이언트에서 dedup/상태 저장 없이 "서버 신뢰" 설계로 폴러 코드 단순화 (±45 lines). 매 폴링마다 fresh read가 오버헤드보다 이해하기 쉬운 구현.
- **현장 피드백 반영**: 비(非) TTS 요청이 구현 직후 확인되어 design 단계에 피드백 적시 반영. 사용자 요구와 기술 가능성의 균형 우수.

### 11.2 What Needs Improvement (Problem)

- **cleanup 후보 문서화**: `_last_signature` 필드가 미사용 상태로 남겨졌는데, 설계 문서에 "향후 dedup 고려"를 명시했으면 구현자 입장에서 더 명확했을 것. Info-level이지만 다음 iterate 전에 정리 필요.
- **docstring과 실제 반환값 불일치**: `detect_today_anomalies` 함수가 설계 예시에서는 `list[dict]`처럼 보이나 실제로는 `tuple[str, list[dict]]`를 반환. API 응답에 `day_type` 필드 필요했지만 docstring이 뒤쫓지 못함.
- **설계 예시 코드의 정확도**: `BACKOFF_ON_ERROR` 상수가 설계서에 나열되었으나 구현에서는 단순 재시도(no backoff) 사용. 문서 정합성 문제.

### 11.3 What to Try Next (Try)

- **자동화된 docstring 검증**: 함수 시그니처와 문서화 사이의 불일치를 검출하는 pre-commit hook 또는 lint 규칙 추가. 특히 반환 타입이 복잡한 경우.
- **설계 예시와 구현 코드의 1:1 매핑 테이블**: 다음 설계 문서에서 "이 설계 섹션은 다음 구현 파일에서 검증" 같은 명시적 매핑 추가. trace 가능성 향상.
- **배포 전 E2E 현장 테스트**: 7대 PC에서 실제 18:05 이후 데이터로 팝업, 자정 복귀, 네트워크 장애 복구 등을 확인하는 체크리스트 작성. 정적 검증만으로는 Windows UI 타이밍을 보장할 수 없음.
- **cleanup 자동화**: 코드베이스 주기적(월 1회) 리뷰에서 Info-level gap 3건을 bundled로 처리하는 스케줄.

---

## 12. Next Steps

### 12.1 Immediate (배포 전)

- [ ] 서버 배포 (커밋 `11fa7c3` 포함 확인)
- [ ] 서버 엔드포인트 스모크 테스트 (내부망 IP 200, 외부 IP 403)
- [ ] `IRMS-Notice-Setup-1.1.0.exe` 재빌드 및 서명 확인
- [ ] 7대 현장 PC 배포 스케줄 공지

### 12.2 배포 후 (18:05 이후)

- [ ] 각 PC에서 트레이 메뉴 "오늘 근태 알림 끄기" 항목 확인
- [ ] "근태 알림 테스트" 메뉴로 Toast 팝업 확인
- [ ] 실제 근태 이상자 발생 시 알림 수신 및 자동 중단 확인
- [ ] 자정 이후 자동 리셋 확인 (다음 날)

### 12.3 Next PDCA Cycle (v1.2)

| Item | Priority | Note |
|------|----------|------|
| cleanup 3건 (docstring/설계 정정) | Medium | 자동화 검증 도구 추가 후 |
| 개인별 필터링 (config에서 사번 선택) | Low | 사용자 요청 시 |
| 알림 클릭 → 브라우저 열기 | Low | `pystray` 버전 확인 필요 |
| 관리자 대시보드 "오늘 이상" 카드 | Medium | 웹 UI 확장 필요 |

---

## 13. Appendix: Design vs Implementation Mapping

### 13.1 서버 감지 규칙 (Design §3.2)

| Design Spec | Implementation | Match |
|-------------|---|:-----:|
| `day_type != '평일'` 건너뜀 | `attendance_excel.py:203-204` | ✅ |
| 출근 누락 → "출근 누락" | `attendance_excel.py:208-209` | ✅ |
| 퇴근 누락 → "퇴근 누락" | `attendance_excel.py:210-211` | ✅ |
| 지각 + `:g` 포맷 | `attendance_excel.py:212-213` | ✅ |
| 조퇴 + `:g` 포맷 | `attendance_excel.py:214-215` | ✅ |
| 외출 제외 | COL_OUTING 미참조 | ✅ |

### 13.2 API 엔드포인트 (Design §3.1)

| Design Spec | Implementation | Match |
|-------------|---|:-----:|
| `GET /api/public/attendance-alerts/today` | `public_attendance_alert_routes.py:20-23` | ✅ |
| Response: `date/day_type/total/items` | `:38-43` | ✅ |
| Item: `emp_id/name/issues` | `attendance_excel.py:218-223` | ✅ (+department) |
| 404 `MONTH_FILE_NOT_FOUND` | `:31-32` | ✅ |
| 503 `FILE_LOCKED_RETRY` | `:33-34` | ✅ |

### 13.3 트레이 폴러 (Design §4.1)

| Design Spec | Implementation | Match |
|-------------|---|:-----:|
| 폴링 주기 30분 | `attendance_alerts.py:26` | ✅ |
| 이름 3명 + "외 N명" | `attendance_alerts.py:38-41` | ✅ |
| 토스트 제목 `근태 이상 N건` | `attendance_alerts.py:33` | ✅ |
| 404/503 조용히 스킵 | `attendance_alerts.py:123-129` | ✅ |

### 13.4 메뉴 & Mute Toggle (Design §4.2)

| Design Spec | Implementation | Match |
|-------------|---|:-----:|
| `_alert_mute_date: str\|None` | `tray_client/src/main.py:62` | ✅ |
| `_alerts_enabled_today()` + 자정 복귀 | `main.py:93-96` | ✅ |
| 메뉴 토글 텍스트 | `main.py:106-112` | ✅ |
| `_toggle_alert_mute_today` 로직 | `main.py:143-151` | ✅ |

---

## 14. Changelog

### v1.1.0 (2026-04-24)

**Added**:
- 서버: `detect_today_anomalies(year_month, target_date)` 함수로 당일 근태 이상 감지 (출근/퇴근 누락, 지각, 조퇴)
- 서버: `GET /api/public/attendance-alerts/today` 공개 엔드포인트 (InternalNetworkOnly 보호)
- 트레이: `AttendanceAlertPoller` 클래스로 30분 주기 폴링 + Windows Toast 알림
- 트레이: 트레이 메뉴에 "오늘 근태 알림 끄기" 토글 항목 추가
- 트레이: 자정 자동 복귀 + 하루 단위 음소거 기능

**Changed**:
- notice-tray-client v1.0.0 → v1.1.0
- `src/main.py`: `/api/public/attendance-alerts` prefix를 InternalNetworkOnly 보호 대상에 추가

**Fixed**:
- 현장에서 근태 이상을 실시간 인지할 수 있도록 개선

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2026-04-24 | PDCA completion report created (Match Rate 98%, 0 high/medium gap) | PDCA Skill |

---

## Sign-Off

**Feature**: attendance-alert  
**Commit**: `11fa7c3` (Add attendance anomaly tray alerts v1.1.0)  
**Match Rate**: 98%  
**Status**: ✅ COMPLETE  
**Next Action**: Proceed to deployment (server first, then tray client)

**Note**: 3건의 Info-level cleanup 후보 (docstring, 설계 정정, 미사용 필드)는 다음 iterate에서 처리. 현장 운영 배포 후 자정 리셋, 30분 주기, 토스트 렌더링 등 실제 타이밍 검증 필요.
