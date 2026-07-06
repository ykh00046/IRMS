# PyInstaller 런타임 훅 — simplejson 함정 차단.
#
# spec 의 excludes 가 simplejson 을 '빈 네임스페이스 패키지'로 남기면, 프리즈된 앱에서
# `import simplejson` 이 성공해버려 requests.compat 이 has_simplejson=True 로 판단하고,
# 이어서 `from simplejson import JSONDecodeError` 가 실체 없어 실패("unknown location")→크래시.
#
# sys.modules 에 None 을 박아 `import simplejson` 이 ImportError 를 내게 하면, requests 는
# 설계대로 표준 라이브러리 json 으로 폴백한다(완전 동작). run.py(및 requests) 임포트보다
# 먼저 실행되도록 spec 의 runtime_hooks 에 등록한다.
import sys

sys.modules["simplejson"] = None
