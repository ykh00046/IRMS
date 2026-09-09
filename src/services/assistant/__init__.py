"""AI 도우미(assistant) 패키지.

구성:
- ``settings``: 켬/끔·모델·API 키(app_settings + 환경변수 폴백)
- ``tools``:    읽기 전용 도구 함수(기존 서비스 재사용). 함수 호출 스키마의 원본
- ``llm``:      Gemini → Groq → Fake 순서의 공급자 사슬, 한국어 시스템 프롬프트
- ``stream``:   SSE 프레임 생성(meta/tool_call/token/done/error)
- ``session``:  대화 이력(메모리, 30분 유휴 만료)

라우터는 ``src/routers/assistant_routes.py``.
"""

from . import llm, session, settings, stream, tools

__all__ = ["llm", "session", "settings", "stream", "tools"]
