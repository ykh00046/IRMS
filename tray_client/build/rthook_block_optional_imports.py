# PyInstaller 런타임 훅 — '부분 번들된 선택적 의존성' 함정 차단.
#
# requests/urllib3 는 simplejson·brotlicffi·brotli 를 "있으면 쓰는" 선택 의존성으로
# import 한다. 빌드 환경에 이들이 (부분적으로) 존재하면 PyInstaller 가 빈 네임스페이스
# 패키지로 번들할 수 있고, 그러면 프리즈된 앱에서 `import X` 가 성공해버린 뒤 속성
# 접근에서 크래시한다. 실사고 2건:
#   - simplejson: requests.compat 이 `from simplejson import JSONDecodeError` 실패
#   - brotlicffi: urllib3.response BaseHTTPResponse 가 `brotli.error` AttributeError
#     (2026-08-03, hermes venv python3.11 로 빌드된 exe — brotlicffi 1.2.0.1 이
#      namespace 로 들어가 기동 즉시 크래시)
#
# sys.modules 에 None 을 박으면 `import X` 가 ImportError 를 내고, requests/urllib3 는
# 설계된 폴백(stdlib json / brotli 없음)으로 완전 동작한다. run.py 임포트보다 먼저
# 실행되도록 spec 의 runtime_hooks 에 등록한다. spec 의 excludes 와 이중 방어.
import sys

# backports.zstd / compression.zstd: urllib3 의 zstd 폴백 경로 — 이름이 zstandard 와
# 달라 1차 차단 목록에서 누락됐었다 (2026-08-03 두 번째 기동 즉사: 구버전 설치 잔재
# _internal 디렉터리가 phantom namespace 로 import 돼 `backports.zstd.ZstdError` AttributeError).
# rthook 은 디스크에 잔재가 있어도 import 자체를 막으므로 잔재 혼입에도 안전하다.
for _name in ("simplejson", "brotlicffi", "brotli", "zstandard", "backports.zstd", "compression.zstd"):
    sys.modules[_name] = None
