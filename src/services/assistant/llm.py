"""AI 도우미 공급자 사슬 — Gemini → Groq → Fake.

- **Gemini**: ``google-genai`` 의 자동 함수 호출(automatic function calling)에
  ``tools.ASSISTANT_TOOLS`` 를 그대로 넘긴다. SDK 가 우리 파이썬 함수를 프로세스
  안에서 실행하므로 별도 왕복이 없다.
- **Groq**: 함수 호출을 쓰지 않는다. 대신 두 단계로 나눈다 — ① 허용 목록 중에서
  ``{"tool": 이름, "parameters": {...}}`` JSON 을 받아 우리가 실행하고, ② 그 결과를
  글로 붙여 다시 물어 답을 받는다. 모델이 키 이름을 흔들어 적는 일이 잦아
  ``tool``/``tool_code``/``tool_name`` 을 모두 받아준다.
- **Fake**: 키가 없거나 ``IRMS_ASSISTANT_FAKE=1`` 일 때. 실제 호출 없이 결정론적으로
  도구 하나를 부르고 그 숫자를 담은 짧은 답을 흘린다(화면·테스트용).

API 키 값은 어떤 경로로도 로그에 남기지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace, field
from datetime import date, timedelta
from typing import Any

from ...db import get_connection
from . import guide as assistant_guide
from . import settings as assistant_settings
from . import guide as guide_book
from . import tools as assistant_tools

_logger = logging.getLogger(__name__)

# Groq 는 함수 호출을 안 쓰므로 모델이 고를 수 있는 도구를 우리가 좁혀 준다.
GROQ_MAX_TOKENS = 1024
GEMINI_MAX_TOKENS = 1024
TEMPERATURE = 0.3

_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


class AssistantError(Exception):
    """스트림이 error 프레임으로 바꿔 내보내는 오류. ``code`` 는 stream 의 코드값."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


GEMINI_BACKUP_MODEL = "gemini-2.5-flash-lite"  # 메인(2.5 flash) 이 막혔을 때 같은 키로 한 번 더


@dataclass
class ProviderConfig:
    """이번 요청에 실제로 쓸 공급자·모델·키."""

    enabled: bool = False
    provider: str | None = None  # "gemini" | "groq" | "fake" | None
    model: str = assistant_settings.DEFAULT_MODEL
    gemini_key: str = ""
    groq_key: str = ""


@dataclass
class AnswerResult:
    answer: str
    tools_used: list[dict[str, str]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)


def load_config() -> ProviderConfig:
    """DB(app_settings) + 환경변수에서 이번 요청의 공급자 설정을 읽는다."""
    with get_connection() as connection:
        enabled_switch = assistant_settings.get_enabled(connection)
        model = assistant_settings.get_model(connection)
        gemini_key = assistant_settings.get_gemini_key(connection)[0]
        groq_key = assistant_settings.get_groq_key(connection)[0]

    if assistant_settings.fake_mode():
        provider = "fake"
    elif gemini_key:
        provider = "gemini"
    elif groq_key:
        provider = "groq"
    else:
        provider = None

    return ProviderConfig(
        enabled=bool(enabled_switch and provider),
        provider=provider,
        model=assistant_settings.GROQ_MODEL if provider == "groq" else model,
        gemini_key=gemini_key,
        groq_key=groq_key,
    )


# ============================================================
# 시스템 프롬프트
# ============================================================
def build_system_prompt(today: date | None = None, context: dict[str, Any] | None = None) -> str:
    """한국어 시스템 프롬프트. 기준 날짜와 화면 맥락을 끼워 넣는다."""
    ref = today or date.today()
    label = f"{ref.year}년 {ref.month}월 {ref.day}일 {_WEEKDAYS[ref.weekday()]}요일"
    week_from = (ref - timedelta(days=ref.weekday())).isoformat()
    month_from = ref.replace(day=1).isoformat()

    prompt = f"""너는 BRM(배합·레시피 관리)의 현장 도우미다.
현장 사람이 묻는 배합·점도·자재·레시피 이야기에 답한다.

[역할]
너는 데이터 조회 담당이면서 프로그램 사용법 안내 담당이다.
숫자·건수·목록을 묻는 데이터 질문은 조회 도구로 답한다.
"어떻게 하나요", "어디서 하나요", "안 돼요", "저장이 안 된 것 같아요",
"무슨 뜻이야" 같은 사용법 질문은 먼저 get_usage_guide 를 부른다.
"~이 궁금해요", "~을 알고 싶어요"처럼 레시피·자재·LOT·날짜를 하나도 집지 않은
막연한 물음도 사용법 질문으로 보고 get_usage_guide 로 어느 화면에서 보는지 안내한다.
사용법 답은 그 도구가 준 entries 안의 말만 쓴다.
메뉴 이름·단추 이름·경로를 지어내지 않는다.

[기준 날짜]
오늘은 {label}({ref.isoformat()})이다.
"이번 주"는 {week_from}부터 {ref.isoformat()}까지다.
"이번 달"은 {month_from}부터 {ref.isoformat()}까지다.
날짜를 안 주면 이 기준으로 도구를 부른다.

[데이터 조회 규칙]
1. 건수·총량·제품별·작업자별 요약 → get_blend_summary
2. "최근", "마지막", "며칠간 뭐 했나" → get_recent_records
3. LOT 하나를 콕 집어 물으면 → get_record
4. 점도, 스펙아웃, 관리한계 → get_viscosity_status
5. 자재 LOT 이 언제 바뀌었나 → get_lot_changes
6. 자재를 얼마나 썼나 → get_material_usage
7. 레시피 구성·비율·허용 편차 → get_recipe
8. "지금 뭐 봐야 하나", 오늘 할 일 → get_attention
9. 화면 사용법·문제 해결·용어 뜻 → get_usage_guide
10. 반드시 도구로 조회한 값만 말한다.
11. LOT·수치·날짜를 지어내지 않는다. 도구가 error 를 주면 모른다고 말한다.

[화면 지도]
{assistant_guide.screen_map_block()}

[답변 형식]
1. 결론을 먼저 한 줄로 말한다.
2. 숫자는 천 단위 구분과 단위를 붙인다(1,250 g, 12.5 kg).
3. 여러 줄 비교는 Markdown 표로 보여준다.
4. 표 아래에 2~3문장으로 뜻을 풀어 준다.
5. 한 문장은 40자 안으로 짧게 쓴다.
6. 줄표(—)를 쓰지 않는다.
7. 조사는 앞말에 붙여 쓴다(LOT을, 점도가).
7-1. 존댓말로 답한다("~입니다", "~합니다"). 반말("~이다", "~한다")은 쓰지 않는다.
8. 마지막 줄에 그 데이터를 더 볼 수 있는 화면을 [화면 지도]에서 골라
   **메뉴**(경로) 꼴로 한 줄 붙인다.
9. 도구가 "안내"와 "화면"을 돌려주면 값을 지어내지 말고 그 화면으로 안내한다.

[안내 답변 형식]
1. 첫 줄은 지금 상황을 현장 말로 한 줄 적는다.
2. 그다음 번호를 붙여 할 일을 적는다.
3. 각 단계는 메뉴를 굵게, 경로를 괄호에 적는다.
   예: 1. **작성 중 배합**(/blend/drafts) 화면을 엽니다.
4. 마지막 줄에 누구에게 알릴지와 무엇이 표시로 남는지 한 줄 적는다.
5. 책임자만 할 수 있는 일은 "책임자 전용"이라고 밝힌다.
6. 데이터와 사용법이 섞인 물음이면 조회를 먼저 하고,
   찾은 기록이 없을 때만 뒤에 안내 단계를 덧붙인다.

[거절]
BRM 데이터 밖의 질문은 한 문장으로 정중히 거절한다.
'잉크'라는 말은 쓰지 않는다. 제품은 반제품, PB 같은 이름으로 부른다.
"""

    hint = _context_hint(context)
    if hint:
        prompt += f"\n[지금 보고 있는 화면]\n{hint}\n"
    return prompt


def _context_hint(context: dict[str, Any] | None) -> str:
    """화면 맥락은 힌트 한 줄로만 붙인다(강제하지 않는다)."""
    if not context:
        return ""
    parts: list[str] = []
    path = str(context.get("path") or "").strip()
    product = str(context.get("product") or "").strip()
    recipe = str(context.get("recipe") or "").strip()
    if path:
        parts.append(f"화면 {path}")
    if product:
        parts.append(f"제품 {product}")
    if recipe:
        parts.append(f"레시피 {recipe}")
    if not parts:
        return ""
    return "사용자는 " + ", ".join(parts) + "을 보고 있다. 물음이 모호하면 이걸 기준으로 본다."


# ============================================================
# 후속 질문 제안
# ============================================================
_SUGGESTION_BY_TOOL = {
    "get_blend_summary": ["이번 주 자재 사용량은?", "최근 배합 기록 보여줘"],
    "get_recent_records": ["이 LOT 상세 알려줘", "오늘 점도 이상 있나?"],
    "get_record": ["같은 제품 최근 기록은?", "이 제품 점도 상태는?"],
    "get_viscosity_status": ["최근 점도 이상 LOT 알려줘", "이 제품 레시피 보여줘"],
    "get_lot_changes": ["이 자재 사용량 추이는?", "교체 뒤 점도는 어땠나?"],
    "get_material_usage": ["이번 달 배합 요약 보여줘", "이 자재 LOT 교체 이력은?"],
    "get_recipe": ["이 제품 최근 배합 기록은?", "이 제품 점도 상태는?"],
    "get_attention": ["오늘 배합 요약 보여줘", "점도 이상 목록 보여줘"],
    "get_usage_guide": ["작성 중 배합은 어떻게 쓰나요?", "수기 입력 승인은 어떻게 받나요?"],
}
DEFAULT_SUGGESTIONS = ["오늘 배합 요약 보여줘", "지금 봐야 할 것 알려줘"]


def _same_question(a: str, b: str) -> bool:
    norm = lambda t: "".join(ch for ch in str(t or "").casefold() if ch.isalnum())  # noqa: E731
    return bool(norm(a)) and norm(a) == norm(b)


def suggestions_for(tools_used: list[dict[str, str]], query: str = "") -> list[str]:
    """다음에 물을 만한 질문 두 개.

    사용법 안내는 물음에 맞는 항목에서 뽑는다 — 고정 두 개만 돌려주면 수기 승인을
    답한 뒤에도 같은 질문을 다시 권한다(2026-09-10 현장 지적). 방금 물은 것과 같은
    질문은 언제나 뺀다.
    """
    picked: list[str] = []
    for entry in reversed(tools_used):
        name = entry.get("name", "")
        if name == "get_usage_guide":
            picked = guide_book.follow_ups_for(query, query)
        if not picked:
            picked = list(_SUGGESTION_BY_TOOL.get(name) or [])
        if picked:
            break
    out = [s for s in picked if not _same_question(s, query)]
    for fallback in DEFAULT_SUGGESTIONS:
        if len(out) >= 2:
            break
        if not _same_question(fallback, query) and fallback not in out:
            out.append(fallback)
    return out[:2]


# ============================================================
# 오류 분류
# ============================================================
def extract_http_status(exc: Exception) -> int:
    """google.genai 오류의 HTTP status 추출.

    SDK 규약: ``.code`` 는 int(HTTP status), ``.status`` 는 문자열(RPC 이름)이다.
    그래서 code 를 먼저 보고, 없으면 메시지 앞부분을 훑는다. 모르면 0.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code:
        return code
    status = getattr(exc, "status", None)
    if isinstance(status, int) and status:
        return status
    message = str(exc)
    for candidate in (429, 503, 502, 500, 403, 401, 400):
        if str(candidate) in message[:80]:
            return candidate
    return 0


def is_fallbackable(exc: Exception) -> bool:
    """Groq 로 넘길 만한 오류인지(429/5xx/시간초과/연결)."""
    status = extract_http_status(exc)
    if status in (429, 500, 502, 503, 504):
        return True
    text = str(exc).lower()
    return any(
        word in text
        for word in ("timeout", "timed out", "deadline", "unavailable", "overload", "quota")
    )


# ============================================================
# 도구 실행 기록기
# ============================================================
class _ToolRecorder:
    """도구 실행을 tool_call 이벤트로 흘리고 최종 목록을 모은다."""

    def __init__(self, emit: Callable[[str, dict[str, Any]], None] | None) -> None:
        self._emit = emit
        self.entries: list[dict[str, str]] = []

    def __call__(self, name: str, status: str) -> None:
        label = assistant_tools.TOOL_LABELS.get(name, name)
        if self._emit is not None:
            self._emit("tool_call", {"name": name, "label": label, "status": status})
        if status in ("done", "error"):
            for entry in self.entries:
                if entry["name"] == name:
                    entry["status"] = status
                    return
            self.entries.append({"name": name, "label": label, "status": status})


# ============================================================
# 공개 진입점
# ============================================================
def run_answer(
    query: str,
    history: list[tuple[str, str]],
    context: dict[str, Any] | None,
    cfg: ProviderConfig,
    emit: Callable[[str, dict[str, Any]], None],
) -> AnswerResult:
    """동기 실행. 스트림 계층이 작업 스레드에서 부른다.

    Gemini 가 429/5xx 로 넘어지면 Groq 로 이어 붙인다. 둘 다 안 되면
    ``AssistantError`` 를 올린다.
    """
    system_prompt = build_system_prompt(context=context)
    recorder = _ToolRecorder(emit)
    assistant_tools.set_emitter(recorder)
    try:
        if cfg.provider == "fake":
            return _run_fake(query, cfg, recorder, emit)

        last_exc: Exception | None = None
        if cfg.provider == "gemini" and cfg.gemini_key:
            # 메인은 설정 모델(기본 gemini-2.5-flash). 429/5xx 면 같은 키로 flash-lite 를
            # 한 번 더 시도하고(분당 한도는 모델별), 그것도 안 되면 Groq 로 넘긴다.
            attempts = [cfg.model]
            if cfg.model != GEMINI_BACKUP_MODEL:
                attempts.append(GEMINI_BACKUP_MODEL)
            for attempt_model in attempts:
                attempt_cfg = cfg if attempt_model == cfg.model else replace(cfg, model=attempt_model)
                try:
                    return _run_gemini(query, history, system_prompt, attempt_cfg, recorder, emit)
                except AssistantError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 분류 후 다음 단계로 넘긴다
                    last_exc = exc
                    _logger.warning(
                        "[assistant] gemini 실패 model=%s %s: %s",
                        attempt_model,
                        type(exc).__name__,
                        exc,
                    )
                    if not is_fallbackable(exc):
                        raise AssistantError(_code_for(exc), str(exc)) from exc
            if not cfg.groq_key:
                raise AssistantError(_code_for(last_exc), str(last_exc)) from last_exc

        if cfg.groq_key:
            try:
                return _run_groq(query, history, system_prompt, cfg, recorder, emit)
            except AssistantError:
                raise
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "[assistant] groq 실패 %s: %s", type(exc).__name__, exc
                )
                raise AssistantError(_code_for(exc), str(exc)) from exc

        if last_exc is not None:
            raise AssistantError(_code_for(last_exc), str(last_exc)) from last_exc
        raise AssistantError("DISABLED", "쓸 수 있는 공급자가 없습니다.")
    finally:
        assistant_tools.set_emitter(None)


def _code_for(exc: Exception) -> str:
    return "RATE_LIMITED" if extract_http_status(exc) == 429 else "LLM_ERROR"


# ============================================================
# Gemini
# ============================================================
def _run_gemini(
    query: str,
    history: list[tuple[str, str]],
    system_prompt: str,
    cfg: ProviderConfig,
    recorder: _ToolRecorder,
    emit: Callable[[str, dict[str, Any]], None],
) -> AnswerResult:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=cfg.gemini_key)

    # 자동 함수 호출은 chats 쪽이 SDK 권장 경로다(models.generate_content_stream 에
    # 직접 태우면 경고가 뜨고 함수 호출 왕복을 SDK 가 대신 돌려주지 않는다).
    past: list[Any] = [
        types.Content(role=role, parts=[types.Part.from_text(text=text)])
        for role, text in history
    ]

    def _build_config(with_thinking: bool) -> Any:
        kwargs: dict[str, Any] = {
            "system_instruction": system_prompt,
            "tools": list(assistant_tools.ASSISTANT_TOOLS),
            "temperature": TEMPERATURE,
            "max_output_tokens": GEMINI_MAX_TOKENS,
        }
        if with_thinking:
            # 생각 예산 0 — 현장 질문은 즉답이 낫고, 토큰도 아낀다. 모델이
            # 이 설정을 모르면 아래에서 빼고 한 번 더 시도한다.
            try:
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            except Exception:  # noqa: BLE001 - 구버전 SDK 방어
                pass
        return types.GenerateContentConfig(**kwargs)

    def _stream(with_thinking: bool) -> tuple[str, dict[str, int]]:
        buffer: list[str] = []
        usage: dict[str, int] = {}
        chat = client.chats.create(
            model=cfg.model,
            config=_build_config(with_thinking),
            history=past,
        )
        for chunk in chat.send_message_stream(query):
            text = _chunk_text_only(chunk)
            if text:
                buffer.append(text)
                emit("token", {"text": text})
            usage = _extract_usage(chunk) or usage
        return "".join(buffer), usage

    try:
        answer, usage = _stream(True)
    except Exception as exc:  # noqa: BLE001
        if "thinking" not in str(exc).lower():
            raise
        _logger.info("[assistant] thinking_config 미지원 — 빼고 재시도")
        answer, usage = _stream(False)

    answer = answer.strip()
    if not answer:
        raise AssistantError("LLM_ERROR", "빈 응답")

    return AnswerResult(
        answer=answer,
        tools_used=recorder.entries,
        suggestions=suggestions_for(recorder.entries, query),
        provider="gemini",
        model=cfg.model,
        usage=usage,
    )


def _chunk_text_only(chunk: Any) -> str:
    """청크에서 글자 부분만 꺼낸다.

    함수 호출이 섞인 청크에서 ``chunk.text`` 를 읽으면 SDK 가 경고를 뱉는다.
    파트를 직접 훑어 텍스트만 모으면 경고 없이 같은 결과를 얻는다.
    """
    candidates = getattr(chunk, "candidates", None) or []
    pieces: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text and not getattr(part, "thought", False):
                pieces.append(text)
    if pieces:
        return "".join(pieces)
    if candidates:
        return ""
    return getattr(chunk, "text", None) or ""


def _extract_usage(chunk: Any) -> dict[str, int]:
    meta = getattr(chunk, "usage_metadata", None)
    if not meta:
        return {}
    return {
        "prompt_tokens": int(getattr(meta, "prompt_token_count", 0) or 0),
        "response_tokens": int(getattr(meta, "candidates_token_count", 0) or 0),
        "total_tokens": int(getattr(meta, "total_token_count", 0) or 0),
    }


# ============================================================
# Groq — 함수 호출이 없어서 두 단계로 나눈다
# ============================================================
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_TOOL_NAME_KEYS = ("tool", "tool_code", "tool_name", "name", "function")
_TOOL_ARGS_KEYS = ("parameters", "params", "arguments", "args")


def parse_tool_plan(text: str) -> tuple[str, dict[str, Any]] | None:
    """모델이 뱉은 계획 JSON 을 관대하게 읽는다.

    ``tool``/``tool_code``/``tool_name`` 어느 키로 와도 받고, 코드펜스나 앞뒤
    잡담이 섞여 있어도 중괄호 덩어리만 떼어 읽는다. 허용 목록 밖 이름이면
    None 을 돌려 준다 — 모델이 지어낸 도구를 부르지 않기 위해서다.
    """
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    name = ""
    for key in _TOOL_NAME_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break
    if name not in assistant_tools.TOOL_NAMES:
        return None

    params: dict[str, Any] = {}
    for key in _TOOL_ARGS_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            params = value
            break
    return name, params


def _groq_client(api_key: str) -> Any:
    from groq import Groq

    return Groq(api_key=api_key)


def _is_model_missing(exc: Exception) -> bool:
    return extract_http_status(exc) == 404 or "model_not_found" in str(exc)


def _run_groq(
    query: str,
    history: list[tuple[str, str]],
    system_prompt: str,
    cfg: ProviderConfig,
    recorder: _ToolRecorder,
    emit: Callable[[str, dict[str, Any]], None],
) -> AnswerResult:
    """Groq 모델 사슬. 은퇴한 모델(404)·한도(429)·서버 오류면 다음 모델로 넘어간다."""
    last_exc: Exception | None = None
    for model in assistant_settings.GROQ_MODELS:
        try:
            return _run_groq_model(query, history, system_prompt, cfg, recorder, emit, model)
        except AssistantError:
            raise
        except Exception as exc:  # noqa: BLE001 - 분류 후 다음 모델
            last_exc = exc
            _logger.warning("[assistant] groq 실패 model=%s %s: %s", model, type(exc).__name__, exc)
            if not (_is_model_missing(exc) or is_fallbackable(exc)):
                raise
    assert last_exc is not None
    raise last_exc


def _run_groq_model(
    query: str,
    history: list[tuple[str, str]],
    system_prompt: str,
    cfg: ProviderConfig,
    recorder: _ToolRecorder,
    emit: Callable[[str, dict[str, Any]], None],
    model: str,
) -> AnswerResult:
    client = _groq_client(cfg.groq_key)

    catalog = "\n".join(
        f"- {name}({', '.join(params)}): {desc}"
        for name, params, desc in assistant_tools.tool_catalog()
    )
    plan_prompt = (
        "다음 질문에 답하려면 어떤 조회가 필요한지 고른다.\n"
        f"[고를 수 있는 조회]\n{catalog}\n\n"
        f"[질문]\n{query}\n\n"
        '설명 없이 JSON 한 덩어리만 답한다: {"tool": "이름", "parameters": {...}}\n'
        "필요 없으면 {\"tool\": \"none\"} 이라고 답한다."
    )

    plan_text = _groq_complete(
        client,
        model,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": plan_prompt},
        ],
        max_tokens=200,
    )
    plan = parse_tool_plan(plan_text)

    tool_block = "(조회 없음)"
    if plan is not None:
        name, params = plan
        result = assistant_tools.call_tool(name, params)
        tool_block = f"{name} 결과:\n{json.dumps(result, ensure_ascii=False)}"

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for role, text in history:
        messages.append(
            {"role": "assistant" if role == "model" else "user", "content": text}
        )
    messages.append(
        {
            "role": "user",
            "content": (
                f"[조회 결과]\n{tool_block}\n\n"
                f"[질문]\n{query}\n\n"
                "위 결과만 근거로 답한다. 없는 값은 지어내지 않는다."
            ),
        }
    )

    buffer: list[str] = []
    for piece in _groq_stream(client, model, messages):
        buffer.append(piece)
        emit("token", {"text": piece})
    answer = "".join(buffer).strip()
    if not answer:
        raise AssistantError("LLM_ERROR", "빈 응답")

    return AnswerResult(
        answer=answer,
        tools_used=recorder.entries,
        suggestions=suggestions_for(recorder.entries, query),
        provider="groq",
        model=model,
    )


def _groq_complete(
    client: Any, model: str, messages: list[dict[str, str]], max_tokens: int
) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()


def _groq_stream(client: Any, model: str, messages: list[dict[str, str]]) -> Any:
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=GROQ_MAX_TOKENS,
        stream=True,
    )
    for chunk in stream:
        try:
            piece = chunk.choices[0].delta.content
        except (AttributeError, IndexError):
            piece = None
        if piece:
            yield piece


# ============================================================
# Fake — 키 없이 도는 결정론적 답변
# ============================================================
FAKE_TOKEN_DELAY_SEC = 0.01

# 가짜 모델이 "사용법 물음"으로 보는 말투. 실제 모델은 프롬프트를 보고 고른다.
_HOWTO_HINTS = (
    "저장 안",
    "어디서",
    "어떻게",
    "안 돼",
    "안돼",
    "안 됩니",
    "무슨 뜻",
    "사용법",
)


def _run_fake(
    query: str,
    cfg: ProviderConfig,
    recorder: _ToolRecorder,
    emit: Callable[[str, dict[str, Any]], None],
) -> AnswerResult:
    today = date.today().isoformat()
    lowered = query or ""

    # 사용법 말투가 먼저다. "배합 기록이 저장 안 된 것 같아요" 처럼 데이터 낱말이
    # 섞여 있어도 이 줄들이 있으면 안내로 본다.
    if any(word in lowered for word in _HOWTO_HINTS):
        name, params = "get_usage_guide", {"question": lowered}
    elif "점도" in lowered:
        name, params = "get_viscosity_status", {"product": "PB"}
    elif "배합" in lowered or "오늘" in lowered:
        name, params = "get_blend_summary", {"date_from": today, "date_to": today}
    else:
        name, params = "get_attention", {}

    result = assistant_tools.call_tool(name, params)
    answer = _fake_answer(name, result)

    for piece in _chunk_text(answer):
        emit("token", {"text": piece})
        time.sleep(FAKE_TOKEN_DELAY_SEC)

    return AnswerResult(
        answer=answer,
        tools_used=recorder.entries,
        suggestions=suggestions_for(recorder.entries, query),
        provider="fake",
        model="fake",
    )


def _fake_answer(name: str, result: dict[str, Any]) -> str:
    """도구 결과에서 숫자 하나를 꺼내 담은 짧은 한국어 답."""
    label = assistant_tools.TOOL_LABELS.get(name, name)
    if result.get("error"):
        return f"{label}을 읽지 못했습니다. 잠시 뒤에 다시 물어보세요."

    if name == "get_usage_guide":
        return _fake_guide_answer(result)

    number = _first_number(result)
    if name == "get_viscosity_status":
        return (
            f"PB 점도는 최근값 {number} 로 확인됩니다.\n"
            "관리한계 안이면 그대로 진행하면 됩니다.\n"
            "자세한 값은 점도 화면에서 볼 수 있습니다."
        )
    if name == "get_blend_summary":
        return (
            f"오늘 배합은 {number} 건으로 집계됩니다.\n"
            "총량과 제품별 건수는 기록 화면에서 볼 수 있습니다."
        )
    return (
        f"지금 볼 항목은 {number} 건입니다.\n"
        "대시보드에서 항목별로 확인하면 됩니다."
    )


def _fake_guide_answer(result: dict[str, Any]) -> str:
    """안내 항목 첫 개의 제목·경로·단계를 그대로 흘린다(화면·테스트용)."""
    entries = result.get("entries") or []
    if not entries:
        return (
            "맞는 안내를 찾지 못했습니다.\n"
            "왼쪽 메뉴에서 화면을 고른 뒤 다시 물어보세요."
        )
    first = entries[0]
    lines = [f"{first.get('title', '')}"]
    lines.append(f"**{first.get('screen', '')}**({first.get('path', '')})")
    for index, step in enumerate(first.get("steps") or [], start=1):
        lines.append(f"{index}. {step}")
    notes = str(first.get("notes") or "").strip()
    if notes:
        lines.append(notes)
    return "\n".join(lines)


def _first_number(payload: Any) -> str:
    """중첩된 딕셔너리에서 처음 만나는 숫자를 사람이 읽는 꼴로."""
    if isinstance(payload, bool):
        return "0"
    if isinstance(payload, int):
        return f"{payload:,}"
    if isinstance(payload, float):
        return f"{payload:,.2f}".rstrip("0").rstrip(".")
    if isinstance(payload, dict):
        for value in payload.values():
            found = _first_number(value)
            if found != "0":
                return found
        return "0"
    if isinstance(payload, list):
        for value in payload:
            found = _first_number(value)
            if found != "0":
                return found
        return "0"
    return "0"


def _chunk_text(text: str, size: int = 6) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]
