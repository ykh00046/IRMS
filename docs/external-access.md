# 외부 접속 가이드 (Cloudflare Tunnel)

> **대상**: IRMS 서버 PC 운영자 (비개발자)
> **목표**: 사무실 외부(영업·재택·출장)에서 `https://irms.<도메인>/`으로 IRMS 접속 가능하게 한다.
> **소요 시간**: 약 30분 (도메인 구입 제외)
> **비용**: 도메인 비용 (`.xyz` 약 $1~3/년) 외 무료

---

## 1. 개요

### 무엇을 하나
- 운영 PC에서 **cloudflared**라는 작은 프로그램이 Cloudflare 본사로 안전한 통로(터널)를 만든다.
- 외부 사용자가 `https://irms.<도메인>/`에 접속하면 Cloudflare가 그 터널을 통해 운영 PC의 IRMS로 전달한다.
- 운영 PC의 방화벽 포트를 열 필요가 없다 (들어오는 연결 X, 나가는 연결 O).

### 왜 Cloudflare Tunnel?
- 무료, URL 영구 고정, HTTPS 자동 적용
- 운영 PC IP가 외부에 노출되지 않음 (Cloudflare가 중계)
- 회사 네트워크의 방화벽 변경 불필요

### 한계
- 운영 PC와 cloudflared가 모두 켜져 있어야 외부 접속 가능
- Cloudflare가 장애날 때는 외부 접속도 멈춤 (LAN 내부는 영향 없음)

---

## 2. 사전 준비

### 2.1 도메인 구입 (1회, 약 5분)
- 추천: [Namecheap](https://www.namecheap.com), [Cloudflare Registrar](https://www.cloudflare.com/products/registrar/)
- `.xyz` `.click` 등은 첫해 $1~3, 갱신 시 $10 내외
- 예: `mycompany.xyz` 구입

### 2.2 Cloudflare 계정 (1회, 약 5분)
- [Cloudflare](https://dash.cloudflare.com/sign-up) 무료 가입
- 좌측 메뉴 → **Add a Site** → 위에서 산 도메인 입력
- 화면에 표시되는 두 개의 **Nameserver**(예: `ada.ns.cloudflare.com`)를 도메인 등록업체(Namecheap 등) 관리 페이지에서 등록
- 변경 반영까지 5~30분 소요 (Cloudflare가 "Active" 표시되면 완료)

---

## 3. 자동 설정 (권장)

운영 PC에서 **관리자 권한**으로 명령 프롬프트를 열고 IRMS 폴더에서:

```bat
setup_tunnel.bat
```

순서:
1. cloudflared 자동 설치 (winget)
2. 브라우저가 열림 → Cloudflare 로그인 → 도메인 선택 → "Authorize" 클릭
3. 터널 이름 입력 (기본: `irms`)
4. 외부 호스트명 입력 (예: `irms.mycompany.xyz`)
5. DNS 자동 연결

스크립트 종료 시 화면에 **"NEXT STEPS"** 가 표시된다. §5로 계속.

> **winget이 없으면** §4(수동 설정)로 진행.

---

## 4. 수동 설정 (winget 미동작 시)

1. [cloudflared 다운로드 페이지](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)에서 Windows 64-bit 설치 파일 다운로드 후 실행
2. 명령 프롬프트(관리자):
   ```
   cloudflared tunnel login
   cloudflared tunnel create irms
   cloudflared tunnel route dns irms irms.mycompany.xyz
   ```
3. 출력된 **터널 UUID**와 **credentials 파일 경로**를 기록

이후 §5로 진행.

---

## 5. 설정 파일 + Windows 서비스 등록

### 5.1 config.yml 작성
1. `cloudflared\config.example.yml`을 `cloudflared\config.yml`로 복사
2. 메모장으로 열어서 다음 4곳을 실제 값으로 교체:
   - `<TUNNEL_UUID>` → setup 출력에 나온 UUID
   - `<USER>` → 운영 PC의 Windows 사용자 이름 (예: `interojo`)
   - `irms.<your-domain>.<tld>` → 실제 외부 호스트명 (2곳)

### 5.2 임시 실행 (검증용)
```bat
run_tunnel.bat
```
- 로그에 `Registered tunnel connection` 4줄이 보이면 정상
- 브라우저에서 `https://irms.mycompany.xyz/health` → `{"status":"ok",...}` 확인
- `Ctrl+C`로 종료

### 5.3 Windows 서비스 등록 (자동 시작)
관리자 권한 명령 프롬프트:
```
cloudflared service install
```
- 등록 후 검증:
  ```
  sc query cloudflared
  ```
  → `STATE : 4  RUNNING` 보이면 성공
- 이후 PC 재부팅 시 자동으로 터널 가동

---

## 6. 검증 체크리스트

외부 통신망에서 (또는 휴대폰 데이터):

| 항목 | 기대 결과 |
|------|-----------|
| `https://irms.<host>/health` | 200 OK, `{"status":"ok",...}` |
| `https://irms.<host>/` | 로그인 화면 정상 표시 |
| 로그인 후 메뉴 동작 | LAN과 동일 |
| 브라우저 DevTools → Network → 첫 응답 헤더 | `Strict-Transport-Security: max-age=31536000` 존재 |
| 브라우저 DevTools → 콘솔 | 에러 0건 |
| `https://irms.<host>/api/public/attendance-alerts/test` | 403 INTERNAL_NETWORK_ONLY (외부 차단 정상) |

---

## 7. 운영 배포 체크리스트

`update_and_run.bat`을 외부 노출 환경에서 안전하게 돌리기 위해 운영 PC `.env` 파일을 다음과 같이 작성:

```ini
IRMS_ENV=production
IRMS_REQUIRE_SESSION_SECRET=true
IRMS_SESSION_SECRET=<여기에 64자리 hex>
IRMS_PUBLIC_HOST=irms.mycompany.xyz
IRMS_SEED_DEMO_DATA=false
```

`IRMS_SESSION_SECRET` 생성:
```
python -c "import secrets; print(secrets.token_hex(32))"
```

설정 후 `update_and_run.bat` 재실행.

---

## 8. 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| `https://irms.<host>/` → **502 Bad Gateway** | IRMS 서버(uvicorn) 미기동 | `update_and_run.bat` 실행 |
| `https://irms.<host>/` → **502** + IRMS는 실행 중 | 9000 포트 방화벽 차단 (loopback도 막힘 — 드문 케이스) | Windows Defender 인바운드 9000 허용 |
| **DNS 미반영** ("이 사이트에 연결할 수 없음") | DNS 전파 지연 | 10~30분 대기, 또는 `nslookup irms.<host> 1.1.1.1` |
| 로그인 후 쿠키 무시됨 (재로그인 반복) | `IRMS_ENV=production` 미설정 → Cookie Secure 미부착 | §7 환경 변수 확인 |
| HSTS 캐시 때문에 도메인 변경이 막힘 | 브라우저가 `https` 강제 | Chrome: `chrome://net-internals/#hsts` → Delete |
| 터널은 살아있는데 서비스로 안 뜸 | `cloudflared service install` 시 권한 부족 | 관리자 권한 cmd로 재실행 |
| `sc query cloudflared` → SERVICE NOT FOUND | 서비스 미등록 | §5.3 재실행 |

---

## 9. 추가 보안 권장 (선택)

본 가이드는 외부 노출의 기본만 다룬다. 다음은 별도 설정 권장:

- **Cloudflare Access (Zero Trust)**: 이메일 OTP / Google 계정 게이트를 IRMS 앞단에 추가. 5명 이하 무료. `dash.cloudflare.com → Zero Trust → Access → Applications`
- **WAF 규칙**: 의심스러운 트래픽 자동 차단. `Security → WAF → Custom rules`
- **국가 차단**: 한국·일본·미국 외 차단. `Security → WAF → Tools → IP Access Rules`
- **Bot Fight Mode**: `Security → Bots → Bot fight mode → On`

---

## 10. 롤백 (외부 접속 끄기)

1. Windows 서비스 제거:
   ```
   cloudflared service uninstall
   ```
2. (선택) 터널 삭제: `cloudflared tunnel delete irms`
3. (선택) Cloudflare 대시보드에서 DNS 레코드 제거
4. LAN 내부 접속은 그대로 동작 (`http://<server-ip>:9000`)

---

## 참고

- 공식 문서: <https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/>
- IRMS 보안 헤더 구현: `src/middleware/security_headers.py`
- IRMS `/health` 엔드포인트: `src/main.py:62`
