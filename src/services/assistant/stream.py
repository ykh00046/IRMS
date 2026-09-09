"""AI 도우미 SSE 스트리밍.

프레임 형식은 한 가지뿐이다::

    event: <name>\\ndata: <json>\\n\\n

이벤트 순서: ``meta`` → ``tool_call``* → ``token``* → ``done`` 또는 ``error``.
대기 중에는 ``: heartbeat`` 주석 줄을 흘려 역방향 프록시가 연결을 끊지 않게 한다.

LLM SDK 가 동기 반복자라 생성은 작업 스레드에서 돌리고, 이벤트는
``asyncio.Queue`` 로 넘겨 이 제너레이터가 이벤트 루프에서 뽑아 쓴다.
토큰이 만들어지는 대로 나가야 화면에 글자가 흐르기 때문이다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from ... import config
from . import llm, session as session_store

_logger = logging.getLogger(__name__)

# 오류 코드 — 화면이 분기하는 값이라 문자열을 바꾸면 위젯도 같이 고쳐야 한다.
ERR_DISABLED = "DISABLED"
ERR_RATE_LIMITED = "RATE_LIMITED"
ERR_TIMEOUT = "TIMEOUT"
ERR_LLM_ERROR = "LLM_ERROR"
ERR_BAD_REQUEST = "BAD_REQUEST"

ERR_MESSAGES = {
    ERR_DISABLED: "AI 도우미가 꺼져 있습니다. 책임자가 시스템 설정에서 켤 수 있습니다.",
    ERR_RATE_LIMITED: "요청이 너무 잦습니다. 잠시 뒤에 다시 물어보세요.",
    ERR_TIMEOUT: "응답이 늦어 중단했습니다. 잠시 뒤에 다시 물어보세요.",
    ERR_LLM_ERROR: "지금은 답을 만들지 못했습니다. 잠시 뒤에 다시 물어보세요.",
    ERR_BAD_REQUEST: "질문을 이해하지 못했습니다. 다시 적어 주세요.",
}

_END_SENTINEL: dict[str, Any] = {"__sentinel__": True}


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def format_sse(event: str, data: dict[str, Any]) -> str:
    """이벤트 한 프레임. UTF-8 그대로(ensure_ascii=False) 내보낸다."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


async def _drain_with_heartbeat(
    queue: "asyncio.Queue[dict[str, Any]]",
    heartbeat_sec: float,
) -> AsyncGenerator[str, None]:
    """큐에서 프레임을 뽑아 흘린다. 조용하면 하트비트 주석을 끼운다."""
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=heartbeat_sec)
        except TimeoutError:
            yield ": heartbeat\n\n"
            continue
        if item is _END_SENTINEL:
            return
        yield format_sse(item["event"], item["data"])


async def stream_answer(
    query: str,
    *,
    session_id: str | None = None,
    context: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """질문 하나에 대한 SSE 프레임 스트림.

    설정이 꺼져 있거나 쓸 수 있는 공급자가 없으면 ``meta``(enabled=false) 뒤에
    ``error``(DISABLED)를 보내고 닫는다 — 화면이 조용히 멈추지 않게 하려는 것.
    """
    request_id = request_id or new_request_id()
    started_at = time.perf_counter()

    cfg = llm.load_config()
    yield format_sse(
        "meta",
        {
            "session_id": session_id,
            "model": cfg.model,
            "provider": cfg.provider,
            "enabled": cfg.enabled,
            "request_id": request_id,
        },
    )

    if not cfg.enabled:
        _logger.info("[assistant %s] disabled — provider=%s", request_id, cfg.provider)
        yield format_sse(
            "error", {"code": ERR_DISABLED, "message": ERR_MESSAGES[ERR_DISABLED]}
        )
        return

    history = session_store.get_history(session_id)
    queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
    loop = asyncio.get_running_loop()
    state: dict[str, Any] = {"result": None, "error": None}

    def _emit(event: str, data: dict[str, Any]) -> None:
        """작업 스레드 → 이벤트 루프. 루프가 이미 닫혔으면 조용히 버린다."""
        try:
            asyncio.run_coroutine_threadsafe(
                queue.put({"event": event, "data": data}), loop
            )
        except RuntimeError:  # pragma: no cover - 루프 종료 경합
            pass

    async def _producer() -> None:
        try:
            state["result"] = await asyncio.to_thread(
                llm.run_answer, query, history, context, cfg, _emit
            )
        except Exception as exc:  # noqa: BLE001 - 코드 분류 후 error 프레임으로
            state["error"] = exc
        finally:
            await queue.put(_END_SENTINEL)

    producer = asyncio.create_task(_producer())

    try:
        async with asyncio.timeout(config.ASSISTANT_TIMEOUT_SEC):
            async for frame in _drain_with_heartbeat(
                queue, config.ASSISTANT_HEARTBEAT_SEC
            ):
                yield frame
            await producer
    except TimeoutError:
        producer.cancel()
        _logger.warning(
            "[assistant %s] timeout after %.0fms provider=%s model=%s",
            request_id,
            (time.perf_counter() - started_at) * 1000,
            cfg.provider,
            cfg.model,
        )
        yield format_sse(
            "error", {"code": ERR_TIMEOUT, "message": ERR_MESSAGES[ERR_TIMEOUT]}
        )
        return
    except asyncio.CancelledError:  # pragma: no cover - 클라이언트가 창을 닫은 경우
        producer.cancel()
        raise

    if state["error"] is not None:
        exc = state["error"]
        code = getattr(exc, "code", None) or ERR_LLM_ERROR
        _logger.warning(
            "[assistant %s] failed provider=%s model=%s %s: %s",
            request_id,
            cfg.provider,
            cfg.model,
            type(exc).__name__,
            exc,
        )
        yield format_sse(
            "error",
            {"code": code, "message": ERR_MESSAGES.get(code, ERR_MESSAGES[ERR_LLM_ERROR])},
        )
        return

    result: llm.AnswerResult = state["result"]
    duration_ms = (time.perf_counter() - started_at) * 1000
    session_store.append_exchange(session_id, query, result.answer)

    tool_names = [t["name"] for t in result.tools_used]
    if not tool_names:
        _logger.warning(
            "[assistant %s] no tool call — 답이 조회 없이 나왔다 provider=%s query=%r",
            request_id,
            result.provider,
            query[:80],
        )
    _logger.info(
        "[assistant %s] done provider=%s model=%s tools=%s tokens=%s "
        "chars=%d duration_ms=%.0f",
        request_id,
        result.provider,
        result.model,
        tool_names,
        result.usage or {},
        len(result.answer),
        duration_ms,
    )

    yield format_sse(
        "done",
        {
            "answer": result.answer,
            "tools_used": result.tools_used,
            "suggestions": result.suggestions,
            "session_id": session_id,
            "provider": result.provider,
            "model": result.model,
            "request_id": request_id,
            "duration_ms": round(duration_ms, 1),
        },
    )
