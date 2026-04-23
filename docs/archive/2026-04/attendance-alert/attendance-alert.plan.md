# Attendance Alert Plan

> 당일 근태 이상을 트레이 팝업으로 지속 고지하는 기능 계획서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | attendance-alert |
| Priority | Medium |
| Base | attendance-view (archived 2026-04, Match Rate 96%) + notice-tray-client |
| Goal | 평일 당일 근태 이상이 발견되면 전 현장 PC에 이름과 함께 팝업을 반복 표시 |

## 2. Problem Statement

근태 조회는 작업자가 직접 페이지에 접속해야 확인 가능하다. 관리자·현장 모두 오늘 누군가의 출근 누락/지각/조퇴/퇴근 누락을 실시간으로 인지할 수 있는 채널이 없다. 엑셀은 매일 18:00에 갱신되지만, 이후 누락된 기록이 있어도 당사자나 관리자가 바로 알아차릴 방법이 없다.

### Pain Points

1. 근태 이상을 늦게 발견 → 교정 기회 상실
2. 관리자가 매일 직접 조회해야만 현황 파악 가능
3. 작업자 본인도 모르고 지나가는 경우가 생길 수 있음

## 3. Feature Items

### 3.1 서버 측 이상 감지

| Item | Detail |
|------|--------|
| 목표 | 오늘 날짜 평일 근무자 중 근태 이상을 가진 사람을 집계해 반환 |
| 위치 | `src/services/attendance_excel.py` 의 신규 헬퍼 + 공개 API |
| 감지 규칙 | 출근/퇴근 누락, 지각(>0), 조퇴(>0). 외출은 제외 (사전 승인된 업무 외출 포함 가능) |
| 제외 조건 | `day_type != '평일'` (토/일/휴일은 제외) |
| API | `GET /api/public/attendance-alerts/today` (무인증, 내부망 전용) |

### 3.2 트레이 클라이언트 알림

| Item | Detail |
|------|--------|
| 목표 | 30분마다 서버를 폴링, 이상이 있으면 Windows 토스트 알림 |
| 구현 | 신규 `tray_client/src/attendance_alerts.py` 모듈 (별도 polling 스레드) |
| 알림 수단 | `pystray.Icon.notify(message, title)` — 추가 라이브러리 불필요 |
| 주기 | 30분 (설정값으로 조절 가능) |
| 종료 조건 | 이상이 해소되면 자동 중단. 출근/퇴근 누락 → 엑셀에 기록되면 사라짐. 지각·조퇴 → 당일 자정 이후 자동 소거 (다음 날 "오늘" 기준 변경) |
| 수동 종료 | 트레이 메뉴에 **"오늘 근태 알림 끄기"** 토글 — 자정에 자동 재활성화 |

### 3.3 메시지 형식

| Item | Detail |
|------|--------|
| 제목 | `근태 이상 {N}건` |
| 본문 | `{이름1} · {이름2} · {이름3}` (3명 초과 시 `외 {N-3}명` 접미) |
| 클릭 동작 | v1 없음 (`pystray` balloon tip은 click action 제한적). 향후 가능하면 브라우저 `/attendance` 열기 |

## 4. Scope

### In Scope
- `detect_today_anomalies(year_month, target_date)` 함수 (서버)
- `GET /api/public/attendance-alerts/today` 엔드포인트 + InternalNetworkOnly 적용
- 트레이 앱 신규 알림 폴러 (30분 주기)
- 토스트 알림 (이름 나열)
- 트레이 메뉴 "오늘 근태 알림 끄기" 토글
- 버전 bump (1.0.0 → 1.1.0) + 재빌드

### Out of Scope (v1)
- 개인별 알림 (각 PC가 자기 사번만 보는 것)
- 알림 클릭 시 브라우저 열기
- 상세 사유 표시 (지각 시간, 조퇴 시간 등)
- 관리자 대시보드 내 오늘 이상 카드
- 이상 해소 시점 자동 기록/감사 로그
- 슬랙/카카오톡 등 외부 채널

## 5. Dependencies

| Dependency | Status |
|------------|--------|
| attendance-view의 엑셀 파서 | ✅ 완료 (archive 2026-04) |
| notice-tray-client 빌드 인프라 | ✅ 완료 (PyInstaller + Inno Setup) |
| InternalNetworkOnlyMiddleware | ✅ 완료 (notice 구축 시 추가) |
| pystray.Icon.notify (toast) | ✅ 기본 기능, 추가 설치 불필요 |

## 6. Success Criteria

1. 오늘 평일 근무자 중 누군가 출근 누락이면 → 30분 이내 전 PC에 팝업 표시
2. 그 사람이 이후 출근 기록을 남기면 → 다음 폴링부터 팝업 중단
3. 지각·조퇴 기록은 당일 자정까지 30분마다 반복 → 다음 날 자동 소거
4. 이상이 없으면 팝업 안 뜸
5. 휴일·주말에는 알림 없음
6. 트레이 우클릭 "알림 끄기" → 그날은 더 안 뜸, 다음 날 자동 복귀

## 7. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| ERP가 18:00보다 늦게 엑셀 업데이트 | 폴링 주기가 짧아서 자연히 보충됨 |
| 엑셀 파일 락 | 기존 `openpyxl read_only=True` 재사용, 503 응답 시 알림 스킵 |
| 팝업 피로도 (30분마다) | "오늘 알림 끄기" 버튼 제공 |
| 이름 노출 개인정보 | 현장 공장 환경에서 이름은 이미 공개정보 수준 — 사용자 결정 |
| 네트워크 장애 | 조용히 실패, 다음 주기에 재시도 |
| 버전 업그레이드 배포 | Inno Setup AppId 동일하므로 덮어쓰기 설치로 기존 설정 유지 |

## 8. Implementation Order

1. 서버: `detect_today_anomalies` 헬퍼 + 공개 엔드포인트
2. 트레이: `attendance_alerts.py` 폴러 + main.py 통합 + 메뉴 항목
3. 버전 1.1.0 으로 bump, `build.bat` 재실행
4. 로컬 스모크 테스트 (서버 mock + 실제 엑셀)
5. 커밋·푸시 → 서버 pull + 재시작 → 트레이 재설치 배포

## 9. Next Step

계획 승인 시 바로 `/pdca design attendance-alert` 로 상세 설계 작성.
