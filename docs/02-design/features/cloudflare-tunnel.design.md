# Cloudflare Tunnel 외부 접속 — Design

> **Phase**: Design (PDCA)
> **Date**: 2026-05-24
> **Plan**: `docs/01-plan/features/cloudflare-tunnel.plan.md`
> **Owner**: Claude (자율 결정)

---

## 1. 변경 요약

본 기능은 **(A) 외부 노출을 위한 보안 헤더 강화**(코드 변경)와 **(B) 운영 환경 설정 자산**(설정 파일·스크립트·가이드)을 함께 제공한다. 코드 변경 면적은 최소(미들웨어 1개 + main.py 1줄 + 테스트), 자산은 명료·자기완결형.

### 1.1 신규 파일 (9개)

| # | 경로 | 종류 | LOC |
|---|------|------|---:|
| 1 | `src/middleware/security_headers.py` | Python 미들웨어 | ~70 |
| 2 | `tests/test_security_headers.py` | pytest | ~80 |
| 3 | `tests/test_health.py` | pytest | ~30 |
| 4 | `cloudflared/config.example.yml` | YAML 템플릿 | ~25 |
| 5 | `cloudflared/README.md` | 디렉토리 README | ~20 |
| 6 | `setup_tunnel.bat` | Windows 배치 | ~60 |
| 7 | `run_tunnel.bat` | Windows 배치 | ~25 |
| 8 | `docs/external-access.md` | 운영자 가이드 | ~150 |
| 9 | `docs/03-analysis/features/cloudflare-tunnel.analysis.md` | gap-detector 산출물 (Check 단계) | — |

### 1.2 수정 파일 (3개)

| # | 경로 | 변경 |
|---|------|------|
| 1 | `src/main.py` | `app.add_middleware(SecurityHeadersMiddleware, ...)` 1줄 + import 1줄 |
| 2 | `.env.example` | 외부 접속 권장 변수 추가 (`IRMS_ENV`, `IRMS_TRUST_PROXY`, `IRMS_PUBLIC_HOST`) |
| 3 | `.gitignore` | `cloudflared/config.yml`, `cloudflared/*.json` 추가 |

---

## 2. 보안 헤더 미들웨어 명세

### 2.1 클래스 시그니처

```python
# src/middleware/security_headers.py

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security-related response headers for external (cloudflared) exposure.

    Headers applied to every response:
      - Strict-Transport-Security (production only)
      - X-Frame-Options: DENY
      - X-Content-Type-Options: nosniff
      - Referrer-Policy: same-origin
      - Permissions-Policy: minimal allowlist (no geolocation/camera/mic/payment)
      - Cross-Origin-Opener-Policy: same-origin

    HSTS is intentionally skipped in development to avoid polluting the
    browser cache with a long-lived production-only directive.
    """

    def __init__(
        self,
        app,
        *,
        is_production: bool,
        hsts_max_age: int = 31_536_000,  # 1 year
    ) -> None:
        super().__init__(app)
        self._is_production = is_production
        self._hsts_max_age = hsts_max_age

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), camera=(), microphone=(), payment=()",
        )
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if self._is_production:
            headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={self._hsts_max_age}",
            )
        return response
```

**설계 결정**:
- `setdefault` 사용 → 라우터에서 명시적으로 다른 값 설정한 경우 (예: 이미지 응답의 X-Frame) 유지
- `includeSubDomains` 미포함 → 서브도메인 자유, 도메인 변경 부담 ↓
- `preload` 미포함 → Cloudflare가 자체 preload 처리 가능, 백엔드에서는 안전 옵션 유지
- `Content-Security-Policy`는 본 사이클 out-of-scope (JSpreadsheet/Chart.js 인라인 스크립트 대응 별도 PDCA 필요)

### 2.2 미들웨어 스택 순서

Starlette `add_middleware`는 `user_middleware.insert(0, ...)` → 마지막 add가 가장 outer(요청 진입 첫 단계, 응답 마지막 단계).

| 추가 순서 | outer→inner 스택 | 요청 흐름 |
|---|---|---|
| 1. SessionMiddleware | [Session] | Session→app |
| 2. CSRFMiddleware | [CSRF, Session] | CSRF→Session→app |
| 3. InternalNetworkOnlyMiddleware | [Internal, CSRF, Session] | Internal→CSRF→Session→app |
| **4. SecurityHeadersMiddleware (신규)** | **[Security, Internal, CSRF, Session]** | **Security→Internal→CSRF→Session→app** |

응답 시 Security가 마지막에 헤더를 부착 → 모든 응답에 일괄 적용 (404·500·CSRF 차단 응답 포함).

### 2.3 main.py 변경

```python
# src/main.py (수정 후)
from .middleware.internal_only import InternalNetworkOnlyMiddleware
from .middleware.security_headers import SecurityHeadersMiddleware
# ... (기존 import 유지)

def create_app() -> FastAPI:
    # ... (기존 Session/CSRF/InternalOnly 코드 유지)
    app.add_middleware(
        InternalNetworkOnlyMiddleware,
        protected_prefixes=("/api/public/attendance-alerts",),
    )

    # 신규 1줄
    app.add_middleware(
        SecurityHeadersMiddleware,
        is_production=not IS_DEVELOPMENT,
    )

    # ... (templates/static/router/health 기존 코드 유지)
```

---

## 3. 단위 테스트 명세

### 3.1 `tests/test_security_headers.py`

| 케이스 | 검증 |
|---|---|
| T-S1 | 개발 환경(`IRMS_ENV` 미설정 또는 development): `Strict-Transport-Security` 헤더 **없음** |
| T-S2 | 운영 환경(`IRMS_ENV=production`): `Strict-Transport-Security: max-age=31536000` 존재 |
| T-S3 | 모든 환경: `X-Frame-Options=DENY`, `X-Content-Type-Options=nosniff`, `Referrer-Policy=same-origin`, `Permissions-Policy=geolocation=(), camera=(), microphone=(), payment=()`, `Cross-Origin-Opener-Policy=same-origin` 존재 |
| T-S4 | 4xx 응답에도 헤더 적용 (`/nonexistent` GET 404) |

구현 패턴: pytest fixture로 `monkeypatch.setenv("IRMS_ENV", "production")` → `importlib.reload(src.config); importlib.reload(src.main)` → `TestClient(app)`.

### 3.2 `tests/test_health.py`

| 케이스 | 검증 |
|---|---|
| T-H1 | `GET /health` → 200, `body["status"]=="ok"`, `body["time"]` ISO-8601 UTC 문자열 |
| T-H2 | `/health`는 CSRF exempt (POST가 아니지만 GET이라 무관) → 인증 없이 통과 |
| T-H3 | `/health` 응답에도 보안 헤더 적용 (모든 응답 적용 확인용 sanity test) |

---

## 4. cloudflared 설정 자산

### 4.1 `cloudflared/config.example.yml`

```yaml
# cloudflared/config.example.yml
# Copy to cloudflared/config.yml and replace placeholders.
# DO NOT commit the real config.yml (already in .gitignore).

tunnel: <TUNNEL_UUID>
credentials-file: C:\Users\<USER>\.cloudflared\<TUNNEL_UUID>.json

ingress:
  # Primary route: external HTTPS -> local IRMS
  - hostname: irms.<your-domain>.<tld>
    service: http://127.0.0.1:9000
    originRequest:
      connectTimeout: 30s
      noTLSVerify: true

  # Catch-all (required by cloudflared)
  - service: http_status:404

# Cloudflare edge health check hits this endpoint
originRequest:
  httpHostHeader: irms.<your-domain>.<tld>
```

**설계 결정**:
- `noTLSVerify: true` — 백엔드가 HTTP라 TLS 검증 무의미. cloudflared edge↔Cloudflare 구간은 mTLS로 자동 보호
- `originRequest.connectTimeout: 30s` — 콜드 스타트(uvicorn worker reload) 대비
- 단일 hostname → 단일 ingress rule. 다중 도메인은 가이드에서 추가 방법 설명

### 4.2 `cloudflared/README.md`

```markdown
# cloudflared/

This directory holds the local Cloudflare Tunnel configuration.

## Files

- `config.example.yml` — template, safe to commit
- `config.yml` — **gitignored**, real config with tunnel UUID
- `<UUID>.json` — **gitignored**, credentials file (auto-created by `cloudflared tunnel create`)

## Setup

See [docs/external-access.md](../docs/external-access.md) for the full
operator guide.
```

---

## 5. Windows 배치 스크립트

### 5.1 `setup_tunnel.bat` (최초 1회)

```batch
@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  IRMS Cloudflare Tunnel — Initial Setup
echo ============================================
echo.

:: 1. cloudflared 설치 확인
where cloudflared >nul 2>&1
if errorlevel 1 (
  echo [1/5] cloudflared not found. Installing via winget...
  winget install --id Cloudflare.cloudflared --silent
  if errorlevel 1 (
    echo [ERROR] winget install failed. Install manually from
    echo         https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
    pause
    exit /b 1
  )
) else (
  echo [1/5] cloudflared already installed.
)
echo.

:: 2. 인증 (브라우저 자동 열림)
echo [2/5] Browser will open for Cloudflare login.
echo        Pick your domain to authorize cloudflared.
cloudflared tunnel login
if errorlevel 1 ( pause & exit /b 1 )
echo.

:: 3. 터널 생성
set /p TUNNEL_NAME=Enter tunnel name (default: irms):
if "%TUNNEL_NAME%"=="" set "TUNNEL_NAME=irms"
echo [3/5] Creating tunnel "%TUNNEL_NAME%"...
cloudflared tunnel create %TUNNEL_NAME%
echo.

:: 4. DNS 라우팅
set /p HOSTNAME=Enter public hostname (e.g. irms.example.com):
echo [4/5] Routing DNS %HOSTNAME% -> tunnel %TUNNEL_NAME%...
cloudflared tunnel route dns %TUNNEL_NAME% %HOSTNAME%
echo.

:: 5. 안내
echo [5/5] Setup base complete.
echo.
echo NEXT STEPS:
echo  1. Copy cloudflared\config.example.yml to cloudflared\config.yml
echo  2. Replace ^<TUNNEL_UUID^> with the UUID shown above
echo  3. Replace ^<USER^> with %USERNAME% (or full path to .cloudflared dir)
echo  4. Replace hostname placeholders with %HOSTNAME%
echo  5. Run: cloudflared service install
echo  6. Verify: https://%HOSTNAME%/health
echo.
pause
```

### 5.2 `run_tunnel.bat` (디버그/임시 가동)

```batch
@echo off
setlocal
cd /d "%~dp0"

if not exist "cloudflared\config.yml" (
  echo [ERROR] cloudflared\config.yml not found.
  echo         Run setup_tunnel.bat first or copy from config.example.yml.
  pause
  exit /b 1
)

echo ============================================
echo  IRMS Cloudflare Tunnel — Debug Run
echo ============================================
echo  Press Ctrl+C to stop. For permanent install:
echo    cloudflared service install
echo ============================================
echo.

cloudflared tunnel --config cloudflared\config.yml run
pause
```

---

## 6. 운영자 가이드 `docs/external-access.md`

구조 (목차):

1. **개요** — Cloudflare Tunnel이 무엇이고 왜 쓰는지, 비용/한계
2. **사전 준비** — 도메인 구입 (`.xyz` $1~3 추천), Cloudflare 계정 무료 가입, 도메인 nameserver 변경
3. **자동 설정 (권장)** — `setup_tunnel.bat` 실행 → 5단계 자동 진행
4. **수동 설정** — `winget` 미동작 환경 대비 단계별
5. **Windows 서비스 등록** — `cloudflared service install` → 재부팅 후 자동 시작
6. **검증 체크리스트** — `https://<host>/health` 200, 로그인 흐름 동작, 보안 헤더 (브라우저 DevTools), `/api/public/attendance-alerts/*` 외부 403
7. **운영 배포 체크리스트** — `.env`에 `IRMS_ENV=production`, `IRMS_SESSION_SECRET` 32바이트 hex, `IRMS_REQUIRE_SESSION_SECRET=true`
8. **문제 해결** — 502 (백엔드 미기동), 502 (포트 9000 차단), DNS 미반영, HSTS 캐시 초기화
9. **추가 보안 권장** (Out-of-scope이지만 안내) — Cloudflare Access(Zero Trust) 이메일 OTP, WAF 규칙, 국가 차단
10. **롤백** — `cloudflared service uninstall` + DNS 레코드 제거

---

## 7. `.env.example` 변경

```ini
# (기존 변수 유지)

# === External Access (Cloudflare Tunnel) ===
# Set to "production" before exposing via cloudflared.
IRMS_ENV=development

# Require a fixed session secret in production (recommended).
IRMS_REQUIRE_SESSION_SECRET=false

# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
# IRMS_SESSION_SECRET=

# Public hostname behind cloudflared (informational; used in docs/logs).
# IRMS_PUBLIC_HOST=irms.example.com
```

**설계 결정**: `IRMS_PUBLIC_HOST`는 정보 변수로만 추가 (코드에서 직접 사용 안 함). 향후 emit-기반 absolute URL 생성 시 활용 가능 — 본 사이클에서는 가이드 검증용으로만 정의.

---

## 8. `.gitignore` 변경

```
# (기존 패턴 유지)

# === Cloudflare Tunnel local secrets ===
cloudflared/config.yml
cloudflared/*.json
```

---

## 9. 동작 보존 검증 (Risk R7 대응)

본 변경이 기존 페이지에 영향을 줄 수 있는 항목:

| 페이지 | 의존성 | 보안 헤더 영향 | 검증 |
|---|---|---|---|
| `/weighing` | 없음 (같은 origin) | XFO=DENY 무관 | pytest 200 통과 |
| `/management` | JSpreadsheet (`/static/vendor/jspreadsheet/`) | 같은 origin → 영향 없음 | 기존 pytest 32건 통과 |
| `/dashboard` | Chart.js (`/static/vendor/chartjs/`) | 같은 origin → 영향 없음 | 기존 통과 |
| `/admin/users` | 없음 | 영향 없음 | 기존 통과 |
| `/status` | SSE 폴링 | 응답 헤더 추가만, body·status 변경 없음 | 기존 통과 |

**결론**: 기존 페이지는 외부 iframe·외부 origin 리소스 미사용 → 보안 헤더 추가에 안전. **CSP는 본 사이클에서 추가하지 않음** (JSpreadsheet 인라인 스크립트 호환성 검증 부담).

---

## 10. 검증 기준 (gap-detector 입력)

Match Rate 100% 달성을 위한 점검 항목:

| 범주 | 가중치 | 검증 항목 |
|---|---:|---|
| 신규 파일 생성 (9개) | 30 | 9개 모두 존재, LOC 추정치 ±30% 이내 |
| 수정 파일 변경 (3개) | 15 | main.py에 SecurityHeadersMiddleware import + add, .env.example/.gitignore 패턴 |
| 보안 헤더 5종 | 20 | `headers.setdefault` 5종 + HSTS 분기 |
| 미들웨어 순서 | 10 | SecurityHeaders가 마지막 add (가장 outer) |
| 단위 테스트 통과 | 15 | 7개 케이스 (T-S1~S4, T-H1~H3) |
| 운영 가이드 10절 구성 | 5 | 10개 section 헤더 존재 |
| `cloudflared/config.example.yml` 필수 키 | 5 | `tunnel`, `credentials-file`, `ingress`(2개 rule), `originRequest` |

---

## 11. 비결정 사항 (Plan §8 응답)

- **Q1 (audit log에 외부 IP 기록)**: 본 사이클 **out-of-scope**. cloudflared가 `CF-Connecting-IP` 헤더를 부여하지만 InternalNetworkOnlyMiddleware는 의도적으로 모든 proxy 헤더 무신뢰 정책 유지. audit log 강화는 별도 PDCA.
- **Q2 (`/admin/*` LAN 전용)**: 본 사이클 **out-of-scope**. 가이드 §9에서 Cloudflare Access 사용 권장으로 우회.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-05-24 | 초기 Design 작성 (Plan §1-8 모두 매핑) |
