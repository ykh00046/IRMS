# Cloudflare Tunnel 외부 접속 — Plan

> **Phase**: Plan (PDCA)
> **Date**: 2026-05-24
> **Owner**: Claude (자율 결정)
> **Related Memory**: `project_external_access`, `project_architecture`, `project_server_deploy`

---

## 1. 배경 (Why)

IRMS는 현재 사무실 LAN 내부 IP(`http://192.168.x.x:9000`)로만 접근 가능. 메모리 `project_external_access`에 사무실 외부(영업·재택·출장 등 최대 ~15명)에서 현황 조회·채팅 사용 필요가 명시되어 있음. 이전에 split-large-files Phase 1·2·3 부채 청산이 완료(2026-05-19)되어 외부 접속 기능 진입 차단이 해소됨.

대안 비교 (메모리 §"대안 비교 외부 접속"):

| 옵션 | 비용 | URL 고정 | HTTPS | 단점 |
|---|---|---|---|---|
| Ngrok | 무료 | ❌ 8시간 후 변경 | ✅ | URL 변경으로 사용자 혼란 |
| Tailscale | 무료 | ✅ | ✅ | 각 기기 클라이언트 설치 필요 |
| **Cloudflare Tunnel** | **무료** | **✅** | **✅** | 도메인 1개 필요 (~$1-10/년) |

→ **Cloudflare Tunnel 채택** (메모리에서 이미 의사 결정 완료, 본 PDCA에서 실행).

---

## 2. 목표 (What)

운영 PC가 켜져 있는 동안, 외부 인터넷에서 `https://irms.<도메인>.<tld>` 로 IRMS 서비스에 안정적으로 접속 가능하게 한다. **현장 보안 모델을 외부 노출에 견딜 수 있는 수준으로 강화**한다.

### 2.1 In-Scope

- **F1** cloudflared 설치·터널 생성·Windows 서비스 등록 절차를 문서화·자동화 (실행 스크립트 포함)
- **F2** `cloudflared/config.example.yml` 템플릿 제공 (gitignored 실제 config의 base)
- **F3** 외부 노출 대응 보안 헤더 미들웨어 신설 (HSTS, XFO, XCTO, Referrer-Policy, Permissions-Policy)
- **F4** `/health` 엔드포인트가 외부 모니터링/cloudflared health check에 적합한지 확인 (이미 존재)
- **F5** `InternalNetworkOnlyMiddleware`가 cloudflared 통한 외부 접속에서도 `/api/public/attendance-alerts`를 LAN 내부로만 제한하도록 동작 보존 검증
- **F6** `.env.example` 갱신: `IRMS_ENV=production`, `IRMS_TRUST_PROXY` 등 외부 접속용 변수 명시
- **F7** 운영자 가이드 문서 (도메인 구입 → 터널 생성 → 서비스 등록 → 검증 5단계)
- **F8** 보안 헤더·`/health` 동작 단위 테스트 추가
- **F9** `.gitignore`에 `cloudflared/config.yml`, `cloudflared/*.json` (credentials) 추가

### 2.2 Out-of-Scope (별도 PDCA로 분리)

- **NS1** Cloudflare Access (Zero Trust, 이메일 OTP 게이트) 도입 — 인증 모델 변화이므로 별 사이클
- **NS2** 도메인 자체 구입·결제 — 운영자 책임 영역 (가이드에는 절차 포함)
- **NS3** WAF 규칙·국가 차단 등 Cloudflare 대시보드 설정 — 운영 정책이므로 가이드만 명시
- **NS4** 외부 접속자에 대한 추가 admin 라우터 차단 (`/admin/*` 외부 거부) — 본 사이클은 헤더 강화·문서화만. 필요 시 후속
- **NS5** 출근 트레이 클라이언트의 외부 통신 — 트레이는 LAN 전용 유지 (메모리 `project_attendance_tray`)

---

## 3. 기능 요구사항 (FR)

| ID | 요구사항 | 검증 방법 |
|----|---------|-----------|
| FR-01 | 운영자가 `setup_tunnel.bat` 1회 실행으로 cloudflared 설치·로그인·터널 생성·DNS 라우팅·서비스 등록까지 완료 가능 | 가이드 실행 흐름 검토 |
| FR-02 | `run_tunnel.bat` 단발 실행으로 디버그 모드 터널 가동 가능 (서비스와 별도) | 스크립트 dry-run |
| FR-03 | 모든 HTTP 응답에 보안 헤더 6종 포함 (`Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy: <minimal>`, `Cross-Origin-Opener-Policy: same-origin`) | pytest TestClient |
| FR-04 | HSTS 헤더는 `IRMS_ENV=production`에서만 송신 (개발 환경에서 브라우저 캐시 오염 방지) | pytest 2개 케이스 |
| FR-05 | `/health`는 인증 없이 200 OK + `{"status":"ok"}` 반환, cloudflared health check가 통과 | pytest + curl 가이드 |
| FR-06 | `/api/public/attendance-alerts/*` 는 cloudflared 통한 외부 IP 요청 시 403 유지 (LAN 전용 보존) | pytest + 가짜 IP 헤더 |
| FR-07 | `.env.example`에 외부 접속 권장 설정 명시 (`IRMS_ENV=production`, 세션 비밀, 도메인 placeholder) | grep 검증 |
| FR-08 | `cloudflared/config.example.yml`이 9000 포트로 라우팅, health check 경로 `/health` 지정, ingress rule 단순화 | yaml lint |
| FR-09 | 기존 pytest 전부 통과 (회귀 없음) | pytest 풀스위트 |

---

## 4. 비기능 요구사항 (NFR)

| ID | 요구사항 | 측정 기준 |
|----|---------|-----------|
| NFR-01 | 외부 접속 추가 후 LAN 내부 접속 속도 영향 0 | 인스턴스 직접 통신 경로 변경 없음 |
| NFR-02 | cloudflared 다운 시 LAN 접속은 정상 동작 | 두 경로 독립, 백엔드는 동일 9000 |
| NFR-03 | 보안 헤더 추가로 LCP/TTFB 영향 < 1ms | 헤더 추가는 in-memory 작업 |
| NFR-04 | cloudflared credentials/config 절대 git에 포함 안 됨 | `.gitignore` + CI scan (수동) |
| NFR-05 | 새 파일 추가 시 모든 모듈 ≤ 250 LOC | wc -l 직접 확인 (split-refactor 패턴 동일) |
| NFR-06 | 운영자 가이드는 한국어 (사용자 비개발자) | 가이드 검토 |

---

## 5. 위험 (Risks)

| ID | 위험 | 확률 | 영향 | 완화 |
|----|------|:---:|:---:|------|
| R1 | `https_only=True` (production) 세션쿠키가 cloudflared HTTPS 종단과 백엔드 HTTP 사이에서 misroute | 중 | 고 | cloudflared가 X-Forwarded-Proto=https 부여 → starlette는 헤더 미신뢰 기본값. SessionMiddleware는 cookie set 시점에 Secure 플래그만 부착하고 브라우저↔cloudflared 구간이 HTTPS면 정상 동작. **검증**: production 모드로 로컬 cloudflared 임시 터널 → 로그인 흐름 수동 확인 (Do 단계) |
| R2 | HSTS 헤더가 운영 도메인을 영구 HTTPS-only로 가둠. 도메인 변경 시 기존 사용자 브라우저 캐시 문제 | 저 | 중 | `max-age=31536000` (1년) 기본, `includeSubDomains` 미포함 (서브도메인 자유). preload 미사용 |
| R3 | 외부 접속자 brute force 시도 증가 | 중 | 중 | `/api/auth/login` rate limiter 이미 적용 (커밋 7b29e72). 본 사이클은 비밀번호 정책 강화 별도 안 함 |
| R4 | InternalNetworkOnlyMiddleware가 `request.client.host`를 cloudflared edge IP로 인식해 우회 가능성 | 저 | 고 | 미들웨어 주석에 "X-Forwarded-For 무시" 명시. cloudflared가 client IP를 starlette에 전달하지 않으므로 외부 IP는 자동으로 비private → 403 거부 유지 (의도된 동작) |
| R5 | 운영자가 `.env`에 `IRMS_ENV=production` 미설정 시 SessionMiddleware가 `Secure=False` Cookie 발급 → 외부 HTTPS로 쿠키 전송 안 됨 | 중 | 고 | 가이드 §"운영 배포 체크리스트"에 명시. update_and_run.bat이 이미 `.env` 누락 시 경고 출력 중 |
| R6 | cloudflared Windows 서비스가 PC 재부팅 시 자동 시작 실패 | 저 | 중 | 가이드에 `sc query cloudflared` 검증 단계 포함 |
| R7 | 보안 헤더가 기존 페이지(JSpreadsheet iframe·외부 폰트 등) 차단 | 저 | 중 | XFO=DENY는 같은 origin iframe도 차단. 검증: 모든 페이지 헤더 적용 후 콘솔 에러 0 확인. `Permissions-Policy`는 화면 동작에 영향 없는 항목만 (geolocation, camera, microphone 등 disable) |

---

## 6. 인수 기준 (Acceptance Criteria)

본 기능은 다음 조건이 모두 충족될 때 완료로 본다:

1. **AC-1** Plan/Design/Do/Analysis/Report 4개 문서 생성 + design-validator 점수 ≥ 96 + gap-detector Match Rate ≥ 90%
2. **AC-2** 신규 보안 헤더 미들웨어 + `/health` + 단위 테스트 추가, pytest 전체 PASS (회귀 0)
3. **AC-3** `cloudflared/config.example.yml`, `setup_tunnel.bat`, `run_tunnel.bat`, `docs/external-access.md` 4개 파일 작성
4. **AC-4** `.env.example`, `.gitignore` 갱신 반영
5. **AC-5** 운영자 가이드 한 번 정독으로 외부 접속 활성화까지 절차 누락 없음 (5단계: 도메인 구입 → cloudflared 설치 → 로그인·터널 생성 → DNS 라우팅 → 서비스 등록·검증)
6. **AC-6** `project_external_access` 메모리 "외부 접속 계획" → "외부 접속 활성 (절차·구성 파일 완료)" 로 상태 갱신

---

## 7. 일정 (PDCA Phase)

| Phase | 산출물 | 도구 |
|-------|--------|------|
| Plan | 본 문서 | — |
| Design | `docs/02-design/features/cloudflare-tunnel.design.md` | design-validator |
| Do | 신규 파일 9개 + 수정 3개 | Read/Write/Edit + pytest |
| Check | `docs/03-analysis/features/cloudflare-tunnel.analysis.md` | gap-detector |
| (Act) | Match Rate < 90% 시 iterate, ≥ 90% 시 Report | pdca-iterator (조건부) |
| Report | `docs/04-report/features/cloudflare-tunnel.report.md` + 메모리 갱신 | report-generator |

---

## 8. 미해결 질문 (Open Questions)

설계 단계에서 결정:
- **Q1** Cloudflare가 부여한 client IP를 audit log에 기록할지? → Design §보안에서 결정 (X-Forwarded-For 신뢰 범위)
- **Q2** `/admin/*` 라우터를 LAN 전용으로 제한할지? → 본 사이클 out-of-scope, 다만 가이드에서 "Access(ZeroTrust)로 별도 인증층 추가 권장" 추천 명시

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-05-24 | 초기 Plan 작성 (Claude 자율 결정) |
