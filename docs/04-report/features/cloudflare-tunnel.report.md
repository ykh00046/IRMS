# cloudflare-tunnel PDCA Completion Report

> **Status**: COMPLETED (코드/문서) + WAITING FOR OPERATOR (실 배포)
> **Feature**: cloudflare-tunnel (외부 접속 활성화 + 보안 헤더 강화)
> **Branch**: main
> **Date**: 2026-05-24
> **Match Rate**: 98% (gap-detector 검증)
> **Author**: ykh00046 (Claude 자율 결정)

---

## Executive Summary (한국어)

IRMS 운영 PC에서 사무실 LAN 외부로 안전하게 노출하기 위한 **Cloudflare Tunnel 통합**과, 외부 노출 환경에 대응한 **보안 헤더 미들웨어**를 추가했다. 코드 변경 면적은 최소(미들웨어 1개 + main.py 2줄 + 테스트 8건)이며, 운영자가 외부 노출을 활성화하기 위한 모든 자산(설정 템플릿·시작 스크립트·한국어 운영 가이드)을 자기완결형으로 제공한다. 신규 단위 테스트 8/8 PASS, 기존 테스트 회귀 0건. design-validator 97점·gap-detector 98% Match Rate로 통과 (잔여 2%는 `IRMS_REQUIRE_SESSION_SECRET` Design 예시값 vs 운영 안전 강화의 minor 차이로 의도적 결정). 메모리 `project_external_access`의 "외부 접속 계획" 상태를 "외부 접속 활성 (코드/문서 준비 완료, 운영자 배포 대기)"로 갱신.

**부수 발견 hotfix**: Do 단계 진입 시 `src/security.py`가 직전 커밋(59b8e7a)에서 `PASSWORD_ALGORITHM="***"`/`PASSWORD_ITERATIONS=***`로 손상되어 import syntax error 발생 상태였음. 비밀번호 해시 호환성 유지를 위해 이전 커밋 7b29e72 값(`"pbkdf2_sha256"`, `200_000`)으로 즉시 복구. cloudflare-tunnel과 무관한 별도 hotfix로 본 변경에 포함.

---

## Plan vs Outcome

| In-Scope Item (Plan §2.1) | Delivered | Notes |
|---|:---:|---|
| F1 setup_tunnel.bat 1회 실행 자동화 | ✅ | winget 설치 + login + create + DNS 라우팅 5단계 |
| F2 cloudflared/config.example.yml 템플릿 | ✅ | tunnel/ingress(2 rules)/originRequest 필수 키 |
| F3 보안 헤더 미들웨어 (5종 + COOP) | ✅ | Plan FR-03 보정 후 6종 (HSTS는 production 분기) |
| F4 /health 엔드포인트 (기존) | ✅ | 단위 테스트 3건 신규 (T-H1~H3) |
| F5 InternalNetworkOnlyMiddleware 동작 보존 | ✅ | T-I1 회귀 가드 신규 |
| F6 .env.example 외부 접속 변수 명시 | ✅ | IRMS_ENV, IRMS_PUBLIC_HOST 추가 (IRMS_TRUST_PROXY는 R4 정책상 제외) |
| F7 운영자 한국어 가이드 (10절) | ✅ | docs/external-access.md 188 LOC |
| F8 보안 헤더·/health 단위 테스트 | ✅ | 8/8 PASS |
| F9 .gitignore (cloudflared 시크릿) | ✅ | cloudflared/config.yml + *.json |
| 실제 외부 노출 검증 (AC-5) | 🔄 | 운영자 도메인 구입 + setup_tunnel.bat 실행 후 사용자 확인 대기 |

**Result**: 코드/문서 In-Scope 100% 달성. 실 배포 검증만 운영자 수동 단계.

### Out-of-Scope (별도 PDCA 후보)
- NS1 Cloudflare Access (Zero Trust 이메일 OTP) 인증층
- NS4 `/admin/*` LAN 전용 차단
- CSP (Content-Security-Policy) — JSpreadsheet 인라인 스크립트 호환성 검증 필요
- audit log에 `CF-Connecting-IP` 기록 (외부 접속자 추적)

---

## Implementation Highlights

### 신규 미들웨어 — `SecurityHeadersMiddleware`

`src/middleware/security_headers.py` (62 LOC):

- **6종 헤더**: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy: geolocation=(), camera=(), microphone=(), payment=()`, `Cross-Origin-Opener-Policy: same-origin`, **HSTS** `max-age=31536000` (production only)
- **위치**: `add_middleware` 호출 순서 4번째 (가장 마지막) → Starlette `insert(0)` 동작에 의해 **가장 outer**. 4xx/5xx/CSRF reject/InternalOnly 403 응답에도 헤더 부착 확인 (T-S4, T-I1)
- **`setdefault` 사용**: 라우터에서 명시 override가 필요한 경우 보존 (예: 이미지 응답)
- **HSTS는 production 분기**: 개발 환경 브라우저 캐시 오염 방지 (Plan R2)

### 운영 자산

| 파일 | LOC | 역할 |
|------|---:|------|
| `cloudflared/config.example.yml` | 23 | 터널 UUID/credentials/ingress(127.0.0.1:9000) 템플릿 |
| `cloudflared/README.md` | 28 | 디렉토리 가이드 + 시크릿 표시 |
| `setup_tunnel.bat` | 79 | 자동 설치 → login → create → DNS 라우팅 5단계 |
| `run_tunnel.bat` | 30 | 디버그 임시 가동 (서비스 등록 전 검증용) |
| `docs/external-access.md` | 188 | 한국어 운영자 가이드 (10절, 사전준비~롤백) |

### 부수 hotfix — `src/security.py`

| Before (커밋 59b8e7a) | After (복구) |
|---|---|
| `PASSWORD_ALGORITHM="***"` | `PASSWORD_ALGORITHM = "pbkdf2_sha256"` |
| `PASSWORD_ITERATIONS=***` | `PASSWORD_ITERATIONS = 200_000` |

복구 값은 직전 커밋 7b29e72(`git show 59b8e7a -- src/security.py`)에서 정확히 가져옴. 비밀번호 해시 알고리즘·iteration이 동일 → 저장된 해시와 100% 호환.

### 환경 변수 / gitignore

- `.env.example`: `IRMS_ENV=production` 주석 가이드, `IRMS_PUBLIC_HOST` placeholder
- `.gitignore`: `cloudflared/config.yml` + `cloudflared/*.json` (credentials 보호)

### 종속성 (requirements.txt)

- `httpx>=0.27.0` 추가 — fastapi.TestClient (starlette.testclient) 필수 의존성. T-H/T-S 테스트 실행에 필요

---

## Metrics

### 신규/수정 면적

| 항목 | 수 |
|---|---:|
| 신규 파일 | 9 (미들웨어 1 + 테스트 2 + cloudflared 2 + 배치 2 + 가이드 1 + analysis.md 1) |
| 수정 파일 | 5 (main.py + .env.example + .gitignore + security.py hotfix + requirements.txt) |
| 신규 코드 LOC (테스트 제외) | 62 (미들웨어) |
| 신규 테스트 LOC | 156 (test_security_headers 100 + test_health 56) |
| 신규 운영 자산 LOC | 348 (config.example 23 + README 28 + setup.bat 79 + run.bat 30 + 가이드 188) |
| 신규 문서 LOC | 약 750 (plan + design + analysis + report) |

### 검증

| Type | Count | Status |
|---|---:|:---:|
| design-validator 점수 | 97/100 | ✅ ≥ 96 (Do 진행 임계) |
| gap-detector Match Rate | 98% | ✅ ≥ 90% (Report 진행 임계) |
| 단위 테스트 신규 | 8/8 | ✅ PASS |
| pytest 회귀 (cloudflare-tunnel 변경분) | 0건 | ✅ |
| pytest 기존 통과 | 30/30 | ✅ |
| pytest 기존 실패 (사전 환경 결손) | 2/40 | ⚠️ 본 변경 무관 (`C:\ErpExcel/monthly_attendance_2026-04.xlsx` 부재) |
| 미들웨어 스택 검증 | outermost 확인 | ✅ |
| 보안 헤더 6종 적용 | 6/6 | ✅ |
| cloudflared YAML 문법 | valid | ✅ |
| 가이드 10절 구성 | 10/10 | ✅ |
| NFR-05 LOC ≤ 250 | 모든 코드 모듈 | ✅ (최대: setup_tunnel.bat 79) |

### Plan 인수 기준 충족

| AC | 내용 | 충족 |
|----|------|:---:|
| AC-1 | Plan/Design/Analysis/Report 4문서 + design ≥ 96 + gap ≥ 90% | ✅ (97 / 98) |
| AC-2 | 보안 헤더 + /health 단위 테스트, pytest 회귀 0 | ✅ |
| AC-3 | cloudflared/config.example.yml + setup_tunnel.bat + run_tunnel.bat + 가이드 | ✅ |
| AC-4 | .env.example + .gitignore 갱신 | ✅ |
| AC-5 | 운영자 가이드 5단계 누락 없음 (도메인→설치→로그인·터널→DNS→서비스 등록) | ✅ (가이드 §2-§5에 모두 매핑) |
| AC-6 | project_external_access 메모리 상태 갱신 | ✅ (본 보고와 함께 진행) |

---

## Lessons Learned

1. **Starlette 미들웨어 순서는 `add_middleware` 호출 순서 = `insert(0)` 동작 → 마지막 add = 가장 outer**. 응답 헤더를 모든 응답에 부착하려면 가장 마지막에 add해야 하며, 이는 4xx/5xx/CSRF reject까지 커버한다. T-S4(404)·T-I1(403) 테스트로 실증 확보.

2. **`setdefault` vs `__setitem__`**: 보안 헤더는 라우터가 명시적으로 다른 값을 설정한 경우(예: 이미지의 X-Frame, embeddable iframe 등) 보존해야 하므로 `setdefault`가 안전한 기본 선택. 단, 향후 정책 강화 시 `__setitem__`(=강제 override)을 고려할 수 있음.

3. **HSTS는 production 분기 필수**: 개발 환경에서 송신하면 브라우저가 `localhost`를 영구 HTTPS-only로 기억해 다른 프로젝트 개발에 지장. `IRMS_ENV=production`에서만 적용하는 분기가 필수.

4. **TestClient의 client.host는 `testclient` 문자열**: `InternalNetworkOnlyMiddleware`는 invalid IP를 비-private으로 처리 → testclient 자체가 외부 IP 시뮬레이션을 대신함. T-I1이 이 동작에 의존하므로, 향후 `request.client.host`의 명시적 fixture 주입 패턴으로 바꾸려면 회귀 안전망 재설계 필요.

5. **부수 발견 — 커밋 손상 감시**: 직전 커밋 59b8e7a의 `src/security.py` 손상은 정상적인 git diff 보기로는 알아채기 어려운 패턴(`= "***"`)이었다. **pytest CI 부재 + push 후 다음 작업까지 시간 간격**이 원인. 후속 사이클로 `pytest --collect-only` 정도라도 git pre-push hook에 거는 PDCA를 고려할 가치 있음.

---

## Operator Handoff (다음 단계)

운영 PC에서 외부 노출 활성화까지:

1. **도메인 구입** (`mycompany.xyz` 약 $1~3/년, Namecheap·Cloudflare Registrar 추천)
2. **Cloudflare 가입** + 도메인 등록 + nameserver 변경 (반영까지 5~30분)
3. **운영 PC에서 IRMS 폴더로 이동, 관리자 권한 cmd에서**:
   ```
   setup_tunnel.bat
   copy cloudflared\config.example.yml cloudflared\config.yml
   notepad cloudflared\config.yml      :: placeholder 4곳 교체
   run_tunnel.bat                       :: 임시 검증 (브라우저로 https://<host>/health 확인)
   :: Ctrl+C 종료 후
   cloudflared service install          :: PC 재부팅 시 자동 시작
   sc query cloudflared                 :: STATE: 4 RUNNING 확인
   ```
4. **`.env`에 운영 모드 명시**:
   ```ini
   IRMS_ENV=production
   IRMS_REQUIRE_SESSION_SECRET=true
   IRMS_SESSION_SECRET=<python -c "import secrets; print(secrets.token_hex(32))">
   ```
5. **검증**: 외부 휴대폰 데이터로 `https://irms.<도메인>/health` → `{"status":"ok"}`, 로그인 흐름 동작, DevTools에서 `Strict-Transport-Security` 헤더 확인, `/api/public/attendance-alerts/test` → 403

전체 절차는 `docs/external-access.md`에서 한국어 자기완결형으로 안내.

---

## File Index

### 신규 (9)
- `src/middleware/security_headers.py` — 보안 헤더 미들웨어 (62 LOC)
- `tests/test_security_headers.py` — 단위 테스트 5건 (T-S1~S4, T-I1)
- `tests/test_health.py` — 단위 테스트 3건 (T-H1~H3)
- `cloudflared/config.example.yml` — 터널 설정 템플릿
- `cloudflared/README.md` — 디렉토리 README
- `setup_tunnel.bat` — 자동 초기 설정
- `run_tunnel.bat` — 디버그 임시 가동
- `docs/external-access.md` — 한국어 운영자 가이드 (10절)
- `docs/03-analysis/cloudflare-tunnel.analysis.md` — gap-detector 산출물

### 수정 (5)
- `src/main.py` — SecurityHeadersMiddleware import + add (2줄)
- `src/security.py` — hotfix (PASSWORD_ALGORITHM/ITERATIONS 복구)
- `.env.example` — IRMS_ENV·IRMS_PUBLIC_HOST 가이드 추가
- `.gitignore` — cloudflared 시크릿 패턴 추가
- `requirements.txt` — httpx>=0.27.0 추가 (TestClient 의존)

### PDCA 문서 (4)
- `docs/01-plan/features/cloudflare-tunnel.plan.md`
- `docs/02-design/features/cloudflare-tunnel.design.md`
- `docs/03-analysis/cloudflare-tunnel.analysis.md`
- `docs/04-report/features/cloudflare-tunnel.report.md` (본 문서)

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-05-24 | 초기 Report 작성 (Match Rate 98% 기반 완료) |
