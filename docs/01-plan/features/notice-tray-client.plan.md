# Notice Tray Client Plan

> 공지방 메시지를 현장 PC에서 자동 TTS 재생하는 트레이 클라이언트 배포 계획서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | notice-tray-client |
| Priority | Medium |
| Base | IRMS 안정화 완료 시점(2026-04-23) |
| Goal | IRMS 공지방 메시지를 7대 현장 PC에 자동 음성 공지로 전달 |
| Deliverable | Windows 설치 파일(.exe) + 시스템 트레이 상주 클라이언트 |

## 2. Problem Statement

현재 IRMS 채팅 기능에는 공지방(`chat_rooms.scope='notice'`)과 브라우저 TTS(`speechSynthesis`)가 이미 구현되어 있으나, **브라우저 채팅 탭이 열려 있고 활성화된 상태에서만** TTS가 동작한다. 현장 작업자는 계량·관리 화면을 주로 보고 있어 공지가 도착해도 바로 인지하지 못한다.

### 현재 Pain Points

1. **공지 도달률 저하** — 브라우저가 닫혀있거나 다른 탭이면 공지 TTS가 재생되지 않음
2. **일일이 확인 필요** — 작업자가 채팅방을 주기적으로 확인해야 함
3. **멀티 PC 동시 공지 불가** — 관리자가 7대 PC에 동시 전달할 수단 없음

## 3. Feature Items

### 3.1 무인증 공지 Polling API

| Item | Detail |
|------|--------|
| 목표 | 트레이 클라이언트가 로그인 없이 공지방 신규 메시지를 주기적으로 가져갈 수 있게 함 |
| 위치 | 서버 측 신규 엔드포인트 `GET /api/public/notice/poll` |
| 주요 기능 | `room_key='notice'` 고정, `after_id` 기반 증분 조회, 내부망 IP만 허용 |
| 관련 파일 | `src/routers/chat_routes.py`(또는 신규 `public_routes.py`), `src/main.py` |
| DB 변경 | 없음 (기존 `chat_messages` 조회만) |
| 보안 | 내부망(`192.168.x.x`, `10.x.x.x`, `127.0.0.1`) IP 화이트리스트, 쓰기 불가 읽기 전용 |

### 3.2 Windows 트레이 클라이언트 (Python)

| Item | Detail |
|------|--------|
| 목표 | 시스템 트레이 상주, 10초마다 공지 polling, 새 메시지 도착 시 효과음 + TTS 재생 |
| 위치 | 신규 디렉터리 `tray_client/` (서버 코드와 분리) |
| 주요 기능 | 트레이 아이콘, 우클릭 메뉴(음소거/종료/서버 주소), TTS 재생, 로그 파일 |
| 관련 파일 | `tray_client/main.py`, `tray_client/poller.py`, `tray_client/tts.py`, `tray_client/config.py` |
| 의존성 | `pystray`, `Pillow`, `requests`, `pyttsx3`, `pywin32` |
| 설정 저장 | `%APPDATA%/IRMS-Notice/config.json` (서버 URL, 음소거 상태, 마지막 after_id) |

### 3.3 설치 프로그램 빌드

| Item | Detail |
|------|--------|
| 목표 | 관리자가 7대 PC에 쉽게 배포하도록 단일 설치 파일 제공 |
| 위치 | 신규 디렉터리 `tray_client/build/` |
| 주요 기능 | PyInstaller로 단일 `.exe` 빌드, Inno Setup으로 설치 마법사, 시작프로그램 자동 등록 |
| 관련 파일 | `tray_client/build/irms_notice.spec`, `tray_client/build/installer.iss` |
| 산출물 | `IRMS-Notice-Setup-v1.0.exe` (약 30MB 예상) |
| 동작 | 설치 → 시작 시 자동 실행 → 트레이 아이콘 표시 → 공지 수신 대기 |

## 4. Scope

### In Scope
- 무인증 공지 polling REST API (`GET /api/public/notice/poll?after_id=X`)
- 내부망 IP 화이트리스트 미들웨어
- Python 트레이 클라이언트 (pystray + pyttsx3 + requests)
- 트레이 메뉴: 음소거 토글, 서버 상태 표시, 종료
- PyInstaller 빌드 스펙
- Inno Setup 설치 스크립트 (시작프로그램 등록 포함)
- 서버 URL 기본값: `http://192.168.11.147:9000` (config.json에서 변경 가능)
- 배포 가이드 문서

### Out of Scope
- 메시지 전송(쓰기) 기능 — 수신 전용
- 로그인/인증 — 내부망 전용이므로 불필요
- 양방향 채팅 UI — 단순 TTS 알림만
- 관리자 원격 제어(종료/재시작) — 향후 검토
- macOS/Linux 지원 — Windows 전용
- 자동 업데이트 — v1에서는 수동 재설치
- 설치 시 서버 URL 입력 UI — 고정 IP라 하드코딩, 필요 시 config.json 수동 편집

## 5. Dependencies

| Dependency | Status |
|------------|--------|
| `chat_rooms` 테이블 (`notice` 시드) | ✅ 존재 |
| `chat_messages` 테이블 | ✅ 존재 |
| 기존 `serialize_chat_message` | ✅ 존재 |
| 서버 고정 IP (`192.168.11.147:9000`) | ✅ 확정 |
| Python 3.10+ 빌드 환경 | ⚠️ 빌드 PC에 필요 |
| Inno Setup 6 | ⚠️ 빌드 PC에 필요 (무료) |
| Windows 10+ 타겟 PC | ✅ 현장 환경 |

## 6. Success Criteria

1. 관리자가 공지방에 메시지를 등록하면 **10초 이내** 7대 PC 모두에서 TTS 재생
2. 브라우저가 닫혀 있어도 정상 동작
3. PC 재부팅 후 트레이 앱 자동 시작
4. 트레이 우클릭 → "음소거" 선택 시 해당 PC만 음소거(서버에는 영향 없음)
5. 설치 파일 더블클릭 → Next 2~3번 클릭 → 자동 실행 (비개발자도 설치 가능)
6. 네트워크 장애 시 앱이 죽지 않고 재연결 시도

## 7. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| 서버 다운 시 계속 재시도로 CPU 낭비 | 지수 백오프(5s → 10s → 30s → 60s 상한) |
| TTS 재생 중 다음 메시지 도착 | 큐잉 후 순차 재생 (중요 공지 누락 방지) |
| Windows SmartScreen 차단 | 코드 서명 생략(내부 배포), 사용자에게 "추가 정보 → 실행" 안내 |
| 폴링 부하(7대 × 10초) | 가벼운 SELECT 1건/요청, 부하 미미 (초당 0.7 req) |
| 공지방 과거 메시지 대량 재생 | 설치 후 최초 실행 시 현재 `latest_id`부터 시작 |
| pyttsx3 한국어 발음 품질 | Windows SAPI 기본 음성(기본: Heami) 사용, 실용 수준 확인 |

## 8. Implementation Order

1. **[서버]** 공지 polling 엔드포인트 + 내부망 IP 화이트리스트 (파일 1~2개)
2. **[클라이언트]** Python 트레이 앱 기본 골격 (pystray 아이콘, polling 루프)
3. **[클라이언트]** TTS 재생 + 효과음 + 큐잉
4. **[클라이언트]** 설정 파일 + 로그 파일
5. **[빌드]** PyInstaller 스펙 작성 및 단일 `.exe` 빌드 검증
6. **[빌드]** Inno Setup 설치 스크립트 (시작프로그램 등록)
7. **[테스트]** 로컬 1대 설치 → 공지 전송 → TTS 확인
8. **[배포]** 현장 7대 PC 설치 및 동시 수신 테스트
9. **[문서]** 설치 가이드 및 운영 매뉴얼

## 9. Open Questions

- [ ] 공지 TTS 포맷: "관리자님: {내용}" vs 그냥 "{내용}"? (기존 브라우저는 `님: ` 포함)
- [ ] 음소거 상태 기본값: 켜짐(ON) vs 꺼짐(OFF)?
- [ ] 효과음 파일: Windows 기본 알림음 vs 커스텀 `.wav` 포함?
- [ ] 트레이 아이콘: IRMS 로고 재사용 vs 확성기/벨 아이콘 신규?

## 10. Next Step

계획 승인 시 `/pdca design notice-tray-client`로 상세 설계(데이터 흐름, API 스펙, 패키징 구조) 문서 작성.
