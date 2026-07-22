# 인증·권한·세션 아키텍처 (BRM/IRMS)

> 대상: 운영자 + 개발자 겸용. BRM(내부코드 IRMS)의 로그인·권한·세션·타임아웃을
> **시나리오 중심**으로 정리한다. 모든 서술은 코드에 대해 `파일:함수` 로 근거를 단다.
> 근거 코드 기준: `main` 브랜치 커밋 `6c551f1` 기준 리뷰(읽기 전용).
> 이 문서는 **설명 문서**다. 코드를 바꾸지 않으며, 마지막 §8 에 점검에서 나온
> 개선 후보(BUG/GAP/POLISH)를 분류해 둔다.

---

## 0. 한눈에 보기 — 세 개의 독립 인증 체계

BRM 에는 서로 **다른 자격증명 공간**을 쓰는 세 인증 체계가 한 쿠키 세션 위에 공존한다.
셋은 같은 서명 쿠키(`irms_session`)를 공유하지만 **세션 키가 겹치지 않아** 서로를
끊지 않는다.

| 체계 | 누가 | 자격증명 | 세션 키 | 저장 위치 | 유휴 만료 |
|------|------|----------|---------|-----------|-----------|
| **책임자/관리자** | 레시피·사용자·시스템 관리자 | 이름+비밀번호(지정 책임자) 또는 `admin/admin`(레거시) | `mgr_worker_id`+`mgr_token` / `user_id`+`session_token` | `workers`/`users` 테이블의 `session_token` | 15분 (`MANAGER_IDLE_TIMEOUT_SECONDS`) |
| **배합 작업자** | 현장 계량자 | **이름만**(비밀번호 없음) | `blend_worker` (dict) | 세션 쿠키 자체(서버 미저장) | 8시간(서버) + 저장 후 5분(JS) |
| **근태** | 사원(사번 소유자) | 사번+비밀번호(관리자 발급) | `att_user` (dict) | `attendance_users` 테이블 | 5분(서버) + 3분/30초(JS) |

핵심 설계 원칙(`src/auth.py` 상단 주석, `_AUTH_SESSION_KEYS`):
로그인/로그아웃은 `session.clear()` 를 **절대 부르지 않고** 자기 네임스페이스 키만
지운다(`_clear_auth_session`). 그래서 책임자가 로그인/로그아웃해도 같은 PC 의
배합 작업자·근태 세션은 살아있다.

---

## 1. 세 인증 체계 상세

### 1-A. 책임자/관리자 세션 (management session)

관리 권한(레시피 수정, 사용자 관리, 감사로그, 시스템 설정)을 여는 세션. 두 종류의
주체가 한 엔드포인트로 로그인한다.

- **로그인**: `POST /api/auth/management-login`
  (`src/routers/auth_routes.py:auth_management_login`)
  1. `authenticate_manager_worker(name, pw)` — 이용자 명단(`workers`)에서
     `is_manager=1` 로 지정된 사람을 이름+비밀번호로 검증(`src/auth.py:authenticate_manager_worker`).
     성공 시 `login_manager_worker` 가 `workers.session_token` 을 새로 회전시키고
     세션에 `mgr_worker_id`+`mgr_token` 을 심는다(`src/auth.py:login_manager_worker`).
  2. 실패하면 레거시 `users` 계정으로 폴백(`authenticate_user`) 하되
     **책임자 등급일 때만** 수락(`has_access_level(legacy,"manager")`). 이때는
     `login_user` 가 `users.session_token` 을 회전(`user_id`+`session_token`).
  3. 둘 다 실패 → 감사로그 `login_failed` + 401 `INVALID_CREDENTIALS`.
- **세션 검증**: 매 요청 `get_current_user`(`src/auth.py:get_current_user`)
  가 (0) 유휴 만료 → (1) `mgr_worker_id` 경로 → (2) `user_id` 경로 순으로 확인.
  쿠키의 토큰과 **DB 의 `session_token` 이 일치**해야 통과 → 다른 기기에서 재로그인하면
  이전 세션 토큰이 무효화되는 **단일 세션** 보장.
- **로그아웃**: `POST /api/auth/logout`(`auth_routes.py:auth_logout`) →
  `logout_user`(`src/auth.py:logout_user`) 가 해당 주체의 `session_token=NULL`
  로 만들고 auth 키만 제거.
- **비밀번호 변경(본인)**: `POST /api/auth/change-password`
  (`auth_routes.py:auth_change_password`) — 현재 비밀번호 확인 후 해시 갱신 +
  세션 토큰 회전(변경 즉시 다른 세션 무효).
- **만료**: 15분 유휴(§3) 또는 쿠키 수명 8시간 도달.

### 1-B. 배합 작업자 세션 (blend session, 이름 기반)

현장 계량 편의를 위한 **보안 경계가 아닌** 귀속용 세션. 비밀번호가 없다.

- **로그인**: `POST /api/blend/session/login`
  (`src/routers/blend_session_routes.py:login`) — 이름이 `workers` 명단에 있는지만
  확인(`worker_service.exists`) 후 `login_worker_session` 이 세션 dict `blend_worker`
  에 이름·시각 저장(`src/blend_session.py:login_worker_session`). 서버 DB 에 세션
  토큰을 남기지 않는다(쿠키 자체가 상태).
- **검증**: `current_blend_worker`(`src/blend_session.py`) — `last_activity` 로부터
  8시간(`IDLE_TIMEOUT_SECONDS`) 초과면 세션 폐기. 배합 쓰기 엔드포인트는
  `require_blend_worker`(`blend_routes.py:63`)로 이 세션을 강제.
- **하트비트**: `GET /api/blend/session/me` 가 `touch_worker_session` 으로 활동
  시각 갱신. **주의**: 이 하트비트는 `get_current_user` 를 타지 않으므로 책임자
  15분 유휴에는 영향이 없다(설계 의도, `src/auth.py:_idle_expired` 주석).
- **로그아웃/만료**: 홈(`/`) 재진입 시 강제 로그아웃(`pages.py:entry_page` →
  `logout_worker_session`), 저장 후 5분 JS 자동 로그아웃(§3), 8시간 서버 유휴.
- **등록 개방**: `POST /api/workers` 는 무로그인(현장에서 새 이름 입력 시 등록,
  `worker_routes.py:register_worker`).

### 1-C. 근태 세션 (attendance, 사번+비밀번호)

메인 로그인과 완전히 분리된 자격증명 공간(`src/attendance_auth.py` 모듈 docstring).

- **로그인**: `POST /api/attendance/login`(`attendance_routes.py:login`) →
  `attendance_auth.authenticate`(사번+관리자 발급 비밀번호). 계정은 **자동 생성 금지**
  — 관리자가 임시 비밀번호로 프로비저닝해야 한다(`ensure_account`,
  `reset_password_to_temporary`).
- **검증**: `current_attendance_emp_id` — `last_activity` 5분 초과면 세션 폐기.
  `require_view_context`(`attendance_auth.py`)가 근태 화면 게이트. **관리자 우회**:
  메인 로그인으로 책임자면 사번 로그인 없이 `admin_mode` 로 아무 사원이나 조회 가능
  (`is_admin_mode` → `has_access_level(user,"manager")`).
- **비밀번호 강도**: 8자 이상 + 사번과 동일 금지 + 반복/연속 숫자 금지
  (`validate_password_strength`).
- **관리자 전용**: `POST /api/attendance/admin/reset-password`,
  `GET /api/attendance/admin/*` 는 `require_irms_manager` 로 책임자 강제.
- **로그아웃/만료**: 홈 재진입 강제 로그아웃(`pages.py:entry_page`), 5분 서버 유휴,
  JS 3분/탭숨김 30초(§3).

---

## 2. 권한 2단계 (담당자 / 책임자)

`src/auth.py` 상단: **담당자(operator) < 책임자(manager)** 2단계. 구 3단계의
'관리자(admin)' 는 책임자로 흡수됐다.

- **등급 랭크**: `ACCESS_LEVEL_RANK = {operator:1, manager:2, admin:2}`
  — 레거시 `admin` 값은 manager 와 동급(`src/auth.py:14`).
- **정규화**: `to_public_user` 가 잔존 `access_level='admin'` 을 `manager` 로 승격
  하고, `access_level` 이 비어있으면 `role=='admin'` → manager 로 유도(`src/auth.py:26`).
- **게이트**: `require_access_level("manager")` 의존성이 관리 쓰기 엔드포인트 전체를
  보호(`src/auth.py:require_access_level` → `has_access_level`).
- **두 종류의 책임자**:
  - 지정 책임자(worker-manager): `workers.is_manager=1`, `_worker_to_public` 이
    `access_level="manager"`, `is_worker_manager=True` 로 승격(`src/auth.py:56`).
  - 레거시 `users` 계정: `admin` 부트스트랩 포함. `admin/admin` 폴백은
    `authenticate_user` 로 검증되고 등급이 manager 여야 관리 세션이 열린다.
- **`admin/admin` 폴백**: 시드/부트스트랩된 `admin` 계정. `POST /api/admin/deactivate-others`
  가 `username != 'admin'` 인 모든 계정을 비활성화해도 admin 은 남는다
  (`admin_routes.py:admin_deactivate_others`) — 관리 복구 경로 보존.
- **마지막 책임자 보호**: `users` 테이블에서 마지막 활성 manager 를 강등/비활성/삭제하면
  400 `LAST_MANAGER`(`admin_routes.py:admin_update_user`/`admin_delete_user`).
  본인 등급 변경/비활성도 금지(`CANNOT_CHANGE_SELF_ACCESS`, `CANNOT_DELETE_SELF`).

### 페이지 게이트 vs API 게이트
페이지 라우트는 두 방식이 있다(`src/routers/pages.py`):
- `_protected_page_response` — 서버에서 등급 확인 후 미달이면 `/management/login`
  으로 리다이렉트. **`/admin/users` 만** 이 방식(`pages.py:admin_users_page`).
- `_app_page_response` — **인증 확인 없이 렌더**. `/management`, `/dashboard`,
  `/insight`, `/status` 가 이 방식. 즉 화면 HTML/JS 자체는 누구나 받고, **보안은 그
  뒤의 API 등급 게이트가 담당**한다(레시피·점도 관리 쓰기 = manager, 읽기 = 개방). §8 GAP-1 참고.

---

## 3. 타임아웃 4겹 + 공용 PC 정책

BRM 은 공용 현장 PC 를 전제로 **역할마다 다른 유휴 정책**을 쓴다.

| # | 대상 | 시간 | 위치(서버/클라) | 근거 |
|---|------|------|-----------------|------|
| 1 | 배합 작업자 유휴 | **8시간** | 서버 | `blend_session.py:IDLE_TIMEOUT_SECONDS` (쿠키 8h 와 정렬) |
| 2 | 배합 저장 후 | **5분** | 클라(JS) | `static/js/blend.js:POST_SAVE_LOGOUT_MS` — 폼이 빈 뒤에만 카운트, 새 입력 시 해제 |
| 3 | 책임자 유휴 | **15분** | 서버 | `config.MANAGER_IDLE_TIMEOUT_SECONDS`, `auth.py:_idle_expired` |
| 4 | 근태 유휴 | **5분** | 서버 | `attendance_auth.py:IDLE_TIMEOUT_SECONDS` |

**작동 방식 — 책임자 15분**: `get_current_user` 진입 시 auth 세션이 있으면
`_idle_expired`(마지막 활동 `auth_seen` 기준)를 검사, 초과면 auth 키만 지우고 401
(`_expire_idle`). 인증된 요청마다 `_touch_auth_session` 으로 리셋. 값이 0 이하면 비활성.

**공용 PC 정책(클라이언트)**:
- **근태 카운트다운 배지**(`static/js/attendance_session.js`): 화면 보임 3분 /
  탭 숨김 30초. 활동(mousemove/keydown/touch/scroll/click)마다 리셋(2초 스로틀).
  T=0 에 `/attendance/login` 으로 이동.
- **sendBeacon 강제 로그아웃**: 탭/창 닫힘(`pagehide`/`beforeunload`) 시
  `navigator.sendBeacon('/api/attendance/logout')`. 배합도 동일 패턴 존재.
  → 이 때문에 로그아웃 엔드포인트는 CSRF 면제(sendBeacon 이 커스텀 헤더 불가, §4).
- **홈 재진입 정리**: `/` 진입 시 근태·배합 세션 강제 로그아웃
  (`pages.py:entry_page`) — 다음 사용자에게 이전 사용자 데이터 미노출.
- **5회 잠금**: 근태 로그인 전용(§4 lockout). 책임자/배합에는 계정 잠금이 없고
  IP 레이트리밋만 있다.

---

## 4. CSRF · Rate limit · Lockout (엔드포인트별)

### 세션·CSRF 미들웨어 (`src/main.py:create_app`)
- **세션 쿠키**: `irms_session`, `max_age=8h`, `same_site`=개발 lax/운영 **strict**,
  `https_only`=운영 True. 서명 기반(Starlette `SessionMiddleware`, 서버측 세션 저장소 없음).
- **CSRF**: `starlette_csrf.CSRFMiddleware`, 쿠키 `csrftoken`(double-submit),
  운영 secure. `refresh_csrf_cookie`(`security.py`)는 `httponly=False`(JS 가 읽어
  `x-csrftoken` 헤더로 재전송), `samesite=lax`.
- **CSRF 면제 목록**(`main.py` exempt_urls): `/health`, 로그인 3종
  (`management-login`, `attendance/login`, `blend/session/login`), 로그아웃 2종
  (`attendance/logout`, `blend/session/logout`).
- **로그인 CSRF 보강**: 면제된 로그인 3종은 `LoginOriginMiddleware` 가 교차 출처
  `Origin` POST 를 403 `CROSS_ORIGIN_LOGIN_BLOCKED`(감사 F-10, `src/middleware/login_origin.py`).
  `IRMS_TRUSTED_ORIGINS` 로 프록시 도메인 허용 가능.

### 표: 주요 쓰기 엔드포인트

| 엔드포인트 | 인증 게이트 | CSRF | Rate limit | Lockout |
|------------|-------------|------|-----------|---------|
| `POST /api/auth/management-login` | 없음(자격 검증) | 면제(Origin 검사) | **5/분** | 없음 |
| `POST /api/auth/change-password` | manager | 필수 | 5/분 | 없음 |
| `POST /api/auth/logout` | 선택(있으면 감사) | 필수 | 없음 | — |
| `POST /api/attendance/login` | 없음(자격 검증) | 면제(Origin 검사) | **5/분** | **5회/15분창 → 5분 잠금** |
| `POST /api/attendance/change-password` | 근태 세션 | 필수 | 5/분 | 없음 |
| `POST /api/attendance/logout` | 없음 | 면제 | 없음 | — |
| `POST /api/attendance/admin/*` | manager(`require_irms_manager`) | 필수 | 없음 | 없음 |
| `POST /api/blend/session/login` | 없음(이름 존재확인) | 면제(Origin 검사) | 없음 | 없음 |
| `POST /api/blend/session/logout` | 없음 | 면제 | 없음 | — |
| `POST /api/blend/manager-verify` | 없음(자격 검증) | 필수 | **5/분** | 없음 |
| `POST /api/blend/records` (+bulk/continuous) | 배합 작업자 세션 | 필수 | 없음 | 없음 |
| `PUT /api/blend/records/{id}` · `DELETE` · `restore` · `approve` | **manager** | 필수 | 없음 | 없음 |
| `POST /api/blend/records/{id}/rescale-ack` | manager | 필수 | 없음 | 없음 |
| `POST /api/workers` (등록) | **없음(개방)** | 필수 | 없음 | 없음 |
| `PATCH/DELETE /api/workers/*`, `/manager` | manager | 필수 | 없음 | 없음 |
| 레시피 쓰기(`recipe_manager_routes`, `recipe_import`, `item_code`) | manager | 필수 | 없음 | 없음 |
| 레시피/대시보드/점도 **읽기** | 없음(개방) | — (GET) | 없음 | 없음 |
| 점도 **측정 등록**(`POST /viscosity/readings`) | 없음(개방·현장) | 필수 | 없음 | 없음 |
| 점도 **관리 쓰기**(제품 생성/수정, 측정 삭제, CSV export) | **manager**(정책 ⓑ) | 필수 | 없음 | 없음 |
| `PUT /api/settings/scale-only-input` | manager | 필수 | 없음 | 없음 |
| 관리자 사용자/서명/시트(`/api/admin/*`) | manager(라우터 전체) | 필수 | 없음 | 없음 |

### 근태 잠금 규칙 (유일한 계정 잠금)
`src/attendance_auth.py`: 15분 창(`FAILED_WINDOW_SECONDS`) 안에서 실패 5회
(`MAX_FAILED_ATTEMPTS`) → 5분 잠금(`LOCKOUT_SECONDS`, HTTP 423). 마지막 실패가 창을
벗어나면 카운터가 1부터 재시작(감사 F-11, `_attempts_within_window`) — 며칠에 걸친
오타는 누적되지 않는다. 잠금·실패·프로비저닝 여부는 감사로그에만 남고 공개 응답은
`INVALID_CREDENTIALS` 로 통일(계정 존재 여부 은폐, SC-1/SC-2).

---

## 5. 즉석 인증 — `POST /api/blend/manager-verify`

세션을 **만들지 않고** 책임자 자격만 즉석 확인하고 1회용 승인 토큰을 발급하는 경로
(`src/routers/blend_routes.py:blend_manager_verify`). 현장 배합 화면에서 책임자가
잠깐 승인만 하고 세션은 남기지 않는 설계.

- **용도(`purpose`)**:
  - `"rescale"`(기본) — 초과 계량 증량 승인. 저장 시 `approval_id` 로 **소비(used=1)**
    강제. 감사 `blend_rescale_approved`.
  - `"manual"` — 저울 전용 모드에서 이 배합 한정 수기 입력 허용. `approval_id` 를
    반환하되 소비를 강제하지 않음(서버가 저울/손 입력을 구분 못하므로 통제는
    '책임자 승인 + 기록의 `manual_entry` 표시'로). 감사 `blend_manual_entry_approved`.
- **검증 순서**: `authenticate_manager_worker`(지정 책임자) → 없으면
  `authenticate_user`(레거시). 유효 계정이지만 비-책임자면 403 `FORBIDDEN`,
  자격 오류면 401 `INVALID_CREDENTIALS`. 거부도 감사(`blend_rescale_approve_denied`,
  `reason`/`purpose` 기록).
- **남용 방지**: `management-login` 과 동일한 **5/분 IP 레이트리밋** + CSRF 필수.
  세션을 안 만드니 유휴/공용PC 노출 위험이 없다.
- **감사 관점**: 승인·거부 양쪽 모두 감사로그. 다만 발급된 승인 토큰은 **특정 기록에
  묶이지 않은 전역 1회용 토큰** — 발급받은 사람이 다른 기록의 증량에 쓸 수 있음(저장 시
  소비되긴 함). 저위험이나 §8 GAP-4 로 기록.

---

## 6. 내부망 공개 API 경계 (`InternalNetworkOnlyMiddleware`)

트레이/상위 대시보드가 쓰는 **무로그인** 폴링 API 4종:
`/api/public/attendance-alerts`, `/api/public/material-usage`,
`/api/public/viscosity-reminders`, `/api/public/rescale-alerts`
(`src/main.py` protected_prefixes).

동작(`src/middleware/internal_only.py`):
- `require_api_token=True`(운영 기본, `REQUIRE_TRAY_API_TOKEN=not IS_DEVELOPMENT`):
  `X-IRMS-Tray-Token` 헤더가 `TRAY_API_TOKEN` 과 `hmac.compare_digest` 일치해야 통과.
  아니면 403 `TRAY_TOKEN_REQUIRED`.
- `require_api_token=False`(개발 기본): 클라이언트 IP 가 사설 대역
  (127/8, 10/8, 172.16/12, 192.168/16, ::1, fc00::/7)일 때만 통과, 아니면 403
  `INTERNAL_NETWORK_ONLY`.
- **X-Forwarded-For 무시**(리버스 프록시 없음 전제). 프록시 도입 시 신뢰 설정 추가 필요.

**중요(운영 배포 함정)**: Cloudflare Tunnel 뒤에서는 `cloudflared` 가 **loopback(127.0.0.1)**
에서 오리진에 접속하므로, 외부에서 들어온 요청도 IP 상으로는 '사설'로 보인다. 즉
IP-only 모드였다면 터널 외부 트래픽이 공개 API 를 뚫는다. 운영 기본값
`REQUIRE_TRAY_API_TOKEN=True` 가 토큰을 강제해 이를 막는다 — **운영에서 토큰 요구를
끄면 안 된다**(§8 GAP-2).

---

## 7. 운영(IRMS_ENV=production) 강화 항목

`src/config.py` + 미들웨어에서 개발/운영 분기(`IS_DEVELOPMENT` = env ∈ dev/development/local/test):

- **세션 쿠키**: `same_site=strict`, `https_only=True`(`main.py`).
- **CSRF 쿠키**: `cookie_secure=True`(`main.py`), `csrftoken` 도 secure(`security.py`).
- **HSTS**: `Strict-Transport-Security: max-age=1년` 운영에서만 부착
  (`security_headers.py`). 그 외 헤더는 항상: `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`,
  `Permissions-Policy`(geo/cam/mic/payment 차단), `Cross-Origin-Opener-Policy: same-origin`.
  `SecurityHeadersMiddleware` 는 최외곽에 장착돼 4xx/5xx·CSRF 거부 응답에도 붙는다.
- **필수 시크릿**: `IRMS_REQUIRE_SESSION_SECRET`(운영 기본 True) → `IRMS_SESSION_SECRET`
  없으면 부팅 실패. 미설정 개발에서는 임시 랜덤 시크릿 생성(재시작 시 세션 무효화).
- **트레이 토큰**: `IRMS_REQUIRE_TRAY_API_TOKEN`(운영 기본 True) → `IRMS_TRAY_API_TOKEN`
  없으면 부팅 실패.
- **데모 시드**: `IRMS_SEED_DEMO_DATA` 운영 기본 False(로그인 화면 데모 자격증명 노출도
  `SEED_DEMO_DATA` 로 게이트, `pages.py:management_login_page`).

---

## 8. 점검 결과 — 개선 후보 (BUG / GAP / POLISH)

읽기 전용 리뷰에서 나온 항목. 심각도와 `파일:라인` 근거를 단다. **코드 변경 없음.**

### GAP-1 (심각도 중) — 관리성 페이지 서버측 게이트 부재
`/management`, `/dashboard`, `/insight`, `/status` 페이지는 `_app_page_response`
로 **인증 확인 없이** 렌더된다(`src/routers/pages.py:136-198`). 보안이 전적으로
뒤단 API 등급 게이트에 의존한다. 레시피/사용자 **쓰기**는 manager 로 보호되어 실질
권한상승은 없으나, 레시피·대시보드·인사이트 **읽기 데이터는 무로그인 노출**된다.
- `/dashboard/*` 전 엔드포인트 무인증(`dashboard_routes.py:52-`),
  `/api/recipes/notifications/recipe-imports` 는 감사로그(`recipes_imported`)를
  무인증 노출(`recipe_operator_routes.py:48`) — 감사로그는 원래 manager 전용
  (`/api/admin/audit-logs`)인데 이 필터만 새어나감(정보 노출, 저).
- 운영 판단 필요: Cloudflare Tunnel 로 외부 노출되는 배포라면 이 페이지들이 인터넷에
  열린다. **의도된 posture 인지 확인** 권장. 근거상 배합/점도 무로그인은 명시적 설계
  이나 대시보드/인사이트 개방은 문서상 근거가 약함.

### GAP-2 (심각도 중, 기본값이 방어) — 터널 loopback + 트레이 토큰 의존
§6 참고. `InternalNetworkOnlyMiddleware` 는 X-Forwarded-For 를 무시하고
`request.client.host` 만 본다(`internal_only.py:77`). Cloudflare Tunnel 오리진은
loopback 이라 사설로 판정되므로, 공개 API 4종의 실질 방어는 **오직 트레이 토큰**이다.
운영 기본 `REQUIRE_TRAY_API_TOKEN=True`(`config.py:35`)가 이를 강제해 현재는 안전.
→ 운영에서 이 플래그를 끄거나 토큰을 비우면 공개 API 가 외부에 열린다. 배포 체크리스트에
명시 필요.

### GAP-3 (심각도 하) — 로그인 타이밍 기반 사용자 열거
세 로그인 모두 존재하지 않는 주체엔 `verify_password`(PBKDF2 20만회)를 **건너뛴다**:
- `authenticate_manager_worker`/`authenticate_user` — 행 없으면 즉시 None
  (`auth.py:77,94`).
- `attendance_auth.authenticate` — 미프로비저닝 계정은 해시 검증 없이 반환
  (`attendance_auth.py:238-250`).

응답 코드는 통일됐지만(`INVALID_CREDENTIALS`) **응답 시간 차**로 "이 이름/사번이
존재하는가" 를 구분할 수 있다. 근태는 `employee_exists_in_any_month`(Excel 조회)가
일부 상쇄하나 완전하지 않다. 내부망 전제라 위험은 낮음. 완화안: 미존재 시 더미 해시
비교로 시간 평탄화.

### GAP-4 (심각도 하) — manager-verify 승인 토큰 비-범위성
`blend_manager_verify` 가 발급하는 `approval_id` 는 특정 기록에 바인딩되지 않은 전역
1회용 토큰(`blend_routes.py:518-531`, `blend_service.create_rescale_approval`).
발급자가 의도와 다른 기록의 증량에 소비할 여지가 있다(저장 시 used=1 소비되므로 재사용
불가). 감사로그가 있어 사후 추적은 가능. 위험 낮음.

### GAP-5 (심각도 하) — 문서상 점도 권한과 코드 불일치 — ✅ 해결(2026-07-22, 정책 ⓑ)
"등록=작업자, 설정=관리자" 라는 문서 정책에 코드를 맞췄다. `op_router`(열람·측정 등록)는
개방 유지, `mgr_router`(제품 생성/수정, 측정 삭제, CSV export)는 `api.py` include 에
`dependencies=[Depends(require_access_level("manager"))]` 로 **책임자 강제**
(`src/routers/api.py`, `viscosity_routes.py` docstring 갱신). 이제 문서 정책과 서버 강제가
일치한다. 회귀 방지 `tests/test_viscosity.py` 의 anonymous 거부/개방 유지 테스트.

### POLISH-1 (심각도 정보) — 잔존 주석의 수명값 불일치
`static/js/blend.js:2287` 주석이 "서버 유휴 12h" 라 하지만 실제
`blend_session.py:IDLE_TIMEOUT_SECONDS` 는 8h(쿠키 8h 와 정렬, 해당 파일 주석은 이미
정정됨). 코드 동작엔 영향 없고 주석만 낡음.

### 확인된 '문제 아님' (adversarial 체크 통과)
- **세션 고정(fixation)**: Starlette 세션은 서버측 ID 없는 서명 쿠키라 고정 대상이
  없다. 로그인 시 DB `session_token` 회전으로 단일 세션 강제(`login_user`/
  `login_manager_worker`). 문제 없음.
- **교차 인증 오염**: `_clear_auth_session` 이 auth 키만 pop, `session.clear()`
  미사용 → 세 세션 상호 불간섭 확인(`auth.py:104,111`).
- **권한 2단계 마이그레이션 잔재**: 레거시 `admin` 값이 `ACCESS_LEVEL_RANK`/
  `to_public_user` 양쪽에서 manager 로 정규화되어 stale 권한 판정 없음(`auth.py:14-38`).
- **비-manager 레거시 계정의 관리 접근**: management-login·manager-verify 모두
  `has_access_level(...,"manager")` 를 재확인 → operator 레거시 계정은 관리 세션을
  못 연다(`auth_routes.py:68`, `blend_routes.py:495`).
- **manual_entry 정보 노출**: 비-책임자 응답에서 서버가 값 자체를 마스킹
  (`blend_routes.py:_mask_manual_entry`) — 화면 가림이 아닌 응답 가림.

---

## 부록 — 이 문서에서 검증하지 못한 것
- **실제 운영 `cloudflared/config.yml`**: 저장소에 없어 터널의 `httpHostHeader`/
  오리진 IP 실측값은 코드 주석(`login_origin.py`) 기반 추론이다. GAP-2 의 loopback
  가정은 표준 cloudflared 동작 기준.
- **런타임 감사로그 실제 적재**: 감사 write 경로는 코드로 확인했으나 실 DB 기록 여부는
  서버 미기동으로 미검증.
- **점도/재고 상위 대시보드 소비자**: 공개 API 를 실제로 어떤 외부 시스템이 어떤 토큰으로
  호출하는지는 이 리포지토리 밖이라 미확인.
- 프런트엔드 JS 의 CSRF 헤더 부착 완전성은 대표 파일(`attendance_session.js`,
  `blend.js`)만 확인. 전체 fetch 호출 감사는 범위 밖.
