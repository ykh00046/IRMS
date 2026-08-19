from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    import tkinter as tk
except ImportError:
    tk = None  # type: ignore[assignment]


logger = logging.getLogger("irms_notice")

POPUP_MAX_NAMES = 3
POPUP_MAX_ROWS = 8
POPUP_WIDTH = 980
POPUP_MARGIN_X = 24
POPUP_MARGIN_Y = 24
QUEUE_POLL_MS = 100

PANEL_BG = "#ffffff"
PANEL_BORDER = "#d7dee8"
PANEL_TEXT = "#0f172a"
PANEL_MUTED = "#64748b"
LINE_TEXT = "#111827"
TABLE_HEADER_BG = "#e8edf5"
TABLE_BORDER = "#cbd5e1"
TABLE_ROW_ALT = "#f8fafc"
HAIR = "#eef2f7"
BUTTON_SUBTLE_BG = "#f8fafc"
BUTTON_SUBTLE_FG = "#334155"
BUTTON_MUTED_FG = "#64748b"

ACCENT_TOKENS = {
    "live": {
        "dot": "#2563eb",
        "badge_bg": "#dbeafe",
        "badge_fg": "#1d4ed8",
        "primary_bg": "#2563eb",
        "primary_fg": "#ffffff",
        "primary_active": "#1d4ed8",
    },
    "test": {
        "dot": "#475569",
        "badge_bg": "#e2e8f0",
        "badge_fg": "#334155",
        "primary_bg": "#334155",
        "primary_fg": "#ffffff",
        "primary_active": "#1f2937",
    },
    "viscosity": {
        "dot": "#0f766e",
        "badge_bg": "#ccfbf1",
        "badge_fg": "#0f766e",
        "primary_bg": "#0f766e",
        "primary_fg": "#ffffff",
        "primary_active": "#115e59",
    },
    "rescale": {
        "dot": "#ea580c",
        "badge_bg": "#ffedd5",
        "badge_fg": "#c2410c",
        "primary_bg": "#ea580c",
        "primary_fg": "#ffffff",
        "primary_active": "#c2410c",
    },
}

# 알림 종류(action_key) → 섹션 라벨 / 표시 순서. 근태·점도·증량이 같이 오면 한 창에 이 순서로 쌓는다.
KIND_LABEL = {"attendance": "근태", "viscosity": "점도", "rescale": "증량", "info": "안내"}
KIND_ORDER = ("attendance", "viscosity", "rescale", "info")
KIND_SECTION_TITLE = {
    "attendance": "근태 확인",
    "viscosity": "점도 입력",
    "rescale": "증량 확인",
    "info": "안내",
}

# 수동 '바로 확인'(trigger_once) 결과 코드 — 폴러가 콜백으로 돌려주고, 트레이가
# 안내 팝업 여부를 결정한다. 자동(슬롯/주기) 폴링은 콜백이 없어 영향이 없다.
FEEDBACK_ALERTED = "alerted"  # 알림 팝업을 띄웠음 → 추가 안내 불필요
FEEDBACK_EMPTY = "empty"      # 폴링 성공 + 대상 0건
FEEDBACK_FAILED = "failed"    # 서버 연결/응답 실패


def emit_feedback(on_feedback: Callable[[str], None] | None, status: str) -> None:
    """피드백 콜백을 안전하게 호출 — 콜백 오류가 폴러 스레드를 죽이지 않게."""
    if on_feedback is None:
        return
    try:
        on_feedback(status)
    except Exception as exc:  # noqa: BLE001
        logger.exception("manual check feedback failed: %s", exc)


@dataclass(frozen=True, slots=True)
class PopupPayload:
    title: str
    badge_text: str
    summary: str
    lines: list[str]
    table_rows: list[dict[str, str]] = field(default_factory=list)
    accent: str = "live"
    action_key: str = "attendance"
    confirm_text: str = "확인"


def _display_name(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    emp_id = str(item.get("emp_id") or "").strip()
    return name or emp_id or "확인 필요"


def _privacy_safe_line(item: dict[str, Any]) -> str:
    return f"{_display_name(item)} 인원 근태 확인"


def _privacy_safe_lines(items: list[dict[str, Any]], total: int) -> list[str]:
    visible = items[:POPUP_MAX_NAMES]
    lines = [_privacy_safe_line(item) for item in visible]
    remaining = max(0, total - len(visible))
    if remaining > 0:
        lines.append(f"+{remaining}명")
    if not lines:
        lines.append("근태 확인이 필요한 인원이 없습니다.")
    return lines


# '미타각' 유형 제목이 이미 뜻하는 사유 — 내용 칸에서 반복하지 않는다.
_MISSING_PUNCH_ISSUES = frozenset({"출근 누락", "퇴근 누락"})


def _detail_content(detail: dict[str, Any]) -> str:
    """상세 한 건의 '내용' 한 칸 — 유형 제목(content)에 원문 사유(extra_content)를 덧붙인다.

    서버는 유형 제목과 판정 원문 사유를 따로 준다(anomaly._anomaly_detail). 종전 팝업은
    이를 '구분(번호) / 내용 / 추가 내용' 세 칸으로 펼쳤는데, 번호는 설명 없는 내부
    분류값이고 내용·추가 내용은 대개 같은 말이었다(2026-08-19 사용자 지적). 그래서
    한 칸으로 합치고, 원문 사유는 제목과 다르며 제목에 이미 들어 있지 않을 때만
    ' - ' 뒤에 붙인다(예: '근태코드 누락(반차/조퇴 예상) - 조퇴 미처리').
    """
    content = str(detail.get("content") or "").strip()
    extra = str(detail.get("extra_content") or "").strip()
    if not content:
        return extra
    # 사유를 낱개로 보고, 제목이 이미 말하는 것은 접는다 — '출/퇴근 미타각' 제목에
    # '출근 누락 / 퇴근 누락' 사유는 같은 말의 반복이다.
    parts = [part.strip() for part in extra.split(" / ") if part.strip()]
    kept = [
        part for part in parts
        if part not in content
        and not ("미타각" in content and part in _MISSING_PUNCH_ISSUES)
    ]
    if not kept:
        return content
    return f"{content} - {' / '.join(kept)}"


def _detail_rows(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in items:
        details = list(item.get("details") or [])
        if not details:
            rows.append(
                {
                    "emp_id": str(item.get("emp_id") or ""),
                    "name": _display_name(item),
                    "department": str(item.get("department") or ""),
                    "date": ", ".join(str(date) for date in item.get("dates") or []),
                    "content": " / ".join(str(issue) for issue in item.get("issues") or []),
                }
            )
            continue
        for detail in details:
            rows.append(
                {
                    "emp_id": str(item.get("emp_id") or ""),
                    "name": _display_name(item),
                    "department": str(item.get("department") or ""),
                    "date": str(detail.get("display_date") or str(detail.get("date") or "")[-5:]),
                    "content": _detail_content(detail),
                }
            )
    return rows


def build_live_popup_payload(payload: dict[str, Any]) -> PopupPayload:
    items = list(payload.get("items") or [])
    total = max(int(payload.get("total") or len(items)), len(items))
    table_rows = _detail_rows(items)
    row_total = max(len(table_rows), total)
    overflow = max(0, row_total - POPUP_MAX_ROWS)
    lines: list[str] = []
    if overflow > 0:
        lines.append(f"+{overflow}건 추가")
    elif not table_rows:
        lines = _privacy_safe_lines(items, total)
    return PopupPayload(
        title="근태 확인 필요",
        badge_text=f"{row_total}건" if row_total else "확인",
        summary="이번 달 미처리 근태 특이사항을 확인해주세요.",
        lines=lines,
        table_rows=table_rows[:POPUP_MAX_ROWS],
        accent="live",
        action_key="attendance",
        confirm_text="근태 확인",
    )


def build_test_popup_payload() -> PopupPayload:
    items = [{"name": "홍길동"}, {"name": "김현장"}]
    return PopupPayload(
        title="근태 알림 테스트",
        badge_text="TEST",
        summary="팝업 표시와 버튼 동작을 확인하세요.",
        lines=_privacy_safe_lines(items, len(items)),
        accent="test",
        action_key="attendance",
        confirm_text="근태 확인",
    )


def build_info_popup_payload(
    title: str,
    summary: str,
    lines: list[str] | None = None,
    *,
    badge_text: str = "안내",
) -> PopupPayload:
    """단순 정보 팝업 — 수동 '바로 확인' 결과·서버 연결 확인 등 안내 전용.

    action_key="info" 라 근태/점도/증량 섹션을 덮어쓰지 않고, 확인 버튼은 창만 닫는다.
    창에 실제로 그려지는 건 lines 이므로(summary 는 로그용) 안내 문구를 첫 줄로 넣는다.
    """
    body = [summary] if summary else []
    body.extend(lines or [])
    return PopupPayload(
        title=title,
        badge_text=badge_text,
        summary=summary,
        lines=body,
        accent="test",
        action_key="info",
        confirm_text="확인",
    )


def _viscosity_pending_count(item: dict[str, Any]) -> int:
    """품목별 미측정 LOT 수 — 서버가 주는 pending_count(전체 개수) 우선, 없으면
    노출된 pending_lots 길이, 둘 다 없는 구서버 품목은 1건으로 본다."""
    raw = item.get("pending_count")
    if isinstance(raw, int) and raw > 0:
        return raw
    lots = item.get("pending_lots")
    if isinstance(lots, list) and lots:
        return len(lots)
    return 1


def _viscosity_lot_line(item: dict[str, Any]) -> str:
    """품목 한 줄 — 미측정 LOT 번호까지 보여 '무엇을' 재촉하는지 알게 한다.

    서버는 pending_lots 를 최대 10건(오래된 순)만 실어 주므로 줄에는 앞 3건만
    펴고 나머지는 ' 외 N건'으로 센다(pending_count 는 전체 수). pending_lots 키가
    없는 구서버는 종전 문구('점도를 입력하세요')로 폴백한다.
    """
    code = str(item.get("code") or item.get("name") or "").strip()
    lots = item.get("pending_lots")
    if not isinstance(lots, list) or not lots:
        return f"{code} 점도를 입력하세요."
    count = _viscosity_pending_count(item)
    shown = [str(lot.get("product_lot") or "").strip() for lot in lots[:3]]
    lots_text = ", ".join(lot for lot in shown if lot)
    remaining = count - len(shown)
    if remaining > 0:
        lots_text = f"{lots_text} 외 {remaining}건"
    return f"{code} 미측정 {count}건: {lots_text}"


def build_viscosity_popup_payload(payload: dict[str, Any]) -> PopupPayload:
    """점도 미측정 LOT 알림 — 서버 응답(daily_reading_reminders)의 pending_lots 로
    품목별 미측정 LOT 을 나열한다(LOT 단위 알림, 2026-08-19).

    뱃지는 품목 수가 아니라 **미측정 LOT 총수**(pending_count 합) — '2품목 6LOT'처럼
    실제 해야 할 일의 양을 보여준다. 구서버(pending_lots 없음)는 품목당 1건으로 센다.
    """
    items = list(payload.get("items") or [])
    lines = [_viscosity_lot_line(item) for item in items[:POPUP_MAX_NAMES]]
    remaining = max(0, len(items) - len(lines))
    if remaining > 0:
        lines.append(f"+{remaining}개 품목 추가")
    total_lots = sum(_viscosity_pending_count(item) for item in items)
    return PopupPayload(
        title="점도 미측정 LOT",
        badge_text=f"{total_lots}건" if items else "확인",
        summary="측정하지 않은 배합 LOT 이 있습니다. 측정 후 등록하거나 측정 불가로 기록하세요.",
        lines=lines,
        accent="viscosity",
        action_key="viscosity",
        confirm_text="점도 등록",
    )


def build_rescale_popup_payload(payload: dict[str, Any]) -> PopupPayload:
    """책임자 미확인 증량(rescale_unacked) 알림 — 최대 3건의 LOT 를 나열한다.

    count>0 인 동안 폴링 주기마다 반복 표시되므로(사후 확인 독려), 별도 중복 억제
    없이 매번 최신 목록으로 그린다.
    """
    items = list(payload.get("items") or [])
    count = max(int(payload.get("count") or len(items)), len(items))
    # kind: "rescale"(증량 부재) | "manual"(수기 입력 부재) | "both". 서버가 2026-07-25 부터
    # 두 종류를 같은 채널로 보낸다. 구 서버(kind 없음)는 증량으로 간주 — 하위호환.
    _KIND_LABEL = {"manual": "수기 입력 확인 필요", "both": "증량·수기 입력 확인 필요"}
    lines: list[str] = []
    for item in items[:POPUP_MAX_NAMES]:
        product = str(item.get("product_name") or "").strip()
        lot = str(item.get("product_lot") or "").strip()
        label = f"{product} ({lot})" if lot else product or lot or "배합 기록"
        suffix = _KIND_LABEL.get(str(item.get("kind") or "rescale"), "증량 확인 필요")
        lines.append(f"{label} {suffix}")
    remaining = max(0, count - len(lines))
    if remaining > 0:
        lines.append(f"+{remaining}건")
    if not lines:
        lines.append("미확인 항목이 없습니다.")
    return PopupPayload(
        title="미확인 증량 · 수기 입력",
        badge_text=f"{count}건" if count else "확인",
        summary="책임자 확인이 필요한 기록이 있습니다 — 배합 기록에서 확인하세요.",
        lines=lines,
        accent="rescale",
        action_key="rescale",
        confirm_text="배합 기록 열기",
    )


class AttendanceAlertPopupManager:
    """근태·점도 알림을 한 창에 섹션으로 합쳐 보여주는 팝업 매니저.

    두 종류가 겹쳐 와도 하나가 다른 하나를 덮어쓰지 않고, 종류별 섹션(표/목록)과
    종류별 확인 버튼을 한 창에 쌓아 보여준다. 한 종류만 있으면 그 섹션만 뜬다.
    """

    def __init__(
        self,
        on_confirm: Callable[[PopupPayload], None],
        on_dismiss_today: Callable[[], None],
    ) -> None:
        self._on_confirm = on_confirm
        self._on_dismiss_today = on_dismiss_today
        # 큐 항목: ("show", payload) | ("call", fn(root)) | ("shutdown", None).
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._root = None
        self._window = None
        self._body = None  # 섹션을 새로 그리는 컨테이너(매 렌더마다 자식 제거 후 재구성)
        # 현재 창에 표시 중인 섹션들. kind(action_key) -> 최신 payload.
        self._sections: dict[str, PopupPayload] = {}

    def start(self) -> None:
        if tk is None:
            logger.warning("attendance popup unavailable: tkinter missing")
            return
        with self._start_lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name="attendance-popup",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._queue.put(("shutdown", None))
        self._thread.join(timeout=5)

    def show(self, payload: PopupPayload) -> None:
        self.start()
        if self._thread is None:
            logger.warning("attendance popup skipped: ui thread unavailable")
            return
        self._queue.put(("show", payload))

    def run_on_ui(self, fn: Callable[[Any], None]) -> None:
        """Tkinter(UI) 스레드에서 fn(root) 를 실행 — 설정 창 등 부가 창 생성용."""
        self.start()
        if self._thread is None:
            logger.warning("ui call skipped: tkinter thread unavailable")
            return
        self._queue.put(("call", fn))

    def _run(self) -> None:
        assert tk is not None
        try:
            root = tk.Tk()
            root.withdraw()
            self._root = root
            root.after(QUEUE_POLL_MS, self._drain_queue)
            root.mainloop()
        except tk.TclError as exc:
            logger.warning("attendance popup ui failed: %s", exc)

    def _drain_queue(self) -> None:
        if self._root is None:
            return
        should_stop = False
        while True:
            try:
                action, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if action == "shutdown":
                should_stop = True
                break
            if action == "show" and payload is not None:
                self._show_payload(payload)
            elif action == "call" and callable(payload):
                try:
                    payload(self._root)
                except Exception as exc:  # noqa: BLE001 - 부가 창 오류가 UI 스레드를 죽이지 않게
                    logger.warning("ui call failed: %s", exc)
        if should_stop:
            self._destroy_window()
            self._root.quit()
            self._root.destroy()
            self._root = None
            return
        self._root.after(QUEUE_POLL_MS, self._drain_queue)

    # ── 창 생성 ──────────────────────────────────────────────────
    def _ensure_window(self) -> None:
        assert tk is not None
        if self._root is None or self._window is not None:
            return
        window = tk.Toplevel(self._root)
        window.withdraw()
        window.title("IRMS 현장 알림")
        window.attributes("-topmost", True)
        window.overrideredirect(True)
        window.resizable(False, False)
        window.configure(bg=PANEL_BORDER)
        window.protocol("WM_DELETE_WINDOW", self._dismiss)
        body = tk.Frame(
            window,
            bg=PANEL_BG,
            highlightthickness=1,
            highlightbackground=PANEL_BORDER,
            highlightcolor=PANEL_BORDER,
            padx=16,
            pady=14,
        )
        body.pack(fill="both", expand=True)
        self._body = body
        self._window = window

    # ── 병합 + 렌더 ──────────────────────────────────────────────
    def _show_payload(self, payload: PopupPayload) -> None:
        self._sections[payload.action_key] = payload
        self._render()

    def _render(self) -> None:
        self._ensure_window()
        if self._window is None or self._body is None:
            return
        order = [k for k in KIND_ORDER if k in self._sections]
        # 알 수 없는 종류가 있으면 뒤에 붙임(안전)
        order += [k for k in self._sections if k not in KIND_ORDER]
        if not order:
            self._dismiss()
            return

        for child in self._body.winfo_children():
            child.destroy()

        # 한 종류만 있으면 그 알림 하나만(제목 중복 없이). 둘 이상이면 공용 헤더 + 섹션들.
        if len(order) == 1:
            self._build_single(self._sections[order[0]])
        else:
            self._build_multi_header(order)
            for index, kind in enumerate(order):
                if index > 0:
                    tk.Frame(self._body, bg=HAIR, height=1).pack(fill="x", pady=(12, 0))
                self._build_section(self._sections[kind])
            self._build_footer()

        self._window.update_idletasks()
        width = self._window.winfo_reqwidth()
        height = self._window.winfo_reqheight()
        screen_width = self._window.winfo_screenwidth()
        screen_height = self._window.winfo_screenheight()
        x = max(0, screen_width - width - POPUP_MARGIN_X)
        y = max(0, screen_height - height - POPUP_MARGIN_Y)
        self._window.geometry(f"+{x}+{y}")
        self._window.deiconify()
        self._window.lift()

    def _build_single(self, payload: PopupPayload) -> None:
        """한 종류만 있을 때 — 헤더(제목+개수) + 본문 + 하단(해당 확인 버튼). 제목 중복 없음."""
        assert tk is not None
        accent = ACCENT_TOKENS.get(payload.accent, ACCENT_TOKENS["live"])
        header = tk.Frame(self._body, bg=PANEL_BG)
        header.pack(fill="x")
        left = tk.Frame(header, bg=PANEL_BG)
        left.pack(side="left", fill="x", expand=True)
        dot = tk.Canvas(left, width=10, height=10, bg=PANEL_BG, highlightthickness=0, bd=0)
        dot.create_oval(2, 2, 8, 8, fill=accent["dot"], outline="")
        dot.pack(side="left", pady=(3, 0))
        tk.Label(
            left, text=payload.title, bg=PANEL_BG, fg=PANEL_TEXT,
            font=("Malgun Gothic", 11, "bold"), anchor="w", justify="left",
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            header, text=payload.badge_text, bg=accent["badge_bg"], fg=accent["badge_fg"],
            font=("Malgun Gothic", 8, "bold"), padx=10, pady=3,
        ).pack(side="right")

        body = tk.Frame(self._body, bg=PANEL_BG)
        body.pack(fill="x", pady=(12, 0))
        if payload.table_rows:
            self._render_table(body, payload.table_rows)
        self._render_lines(body, payload.lines)

        self._build_footer(primary=payload)

    def _build_multi_header(self, order: list[str]) -> None:
        """둘 이상일 때 공용 헤더 — '현장 확인 필요' + 종류별 배지(근태 N건 · 점도 M개)."""
        assert tk is not None
        header = tk.Frame(self._body, bg=PANEL_BG)
        header.pack(fill="x")
        left = tk.Frame(header, bg=PANEL_BG)
        left.pack(side="left", fill="x", expand=True)
        dot = tk.Canvas(left, width=10, height=10, bg=PANEL_BG, highlightthickness=0, bd=0)
        dot.create_oval(2, 2, 8, 8, fill="#334155", outline="")
        dot.pack(side="left", pady=(3, 0))
        tk.Label(
            left, text="현장 확인 필요", bg=PANEL_BG, fg=PANEL_TEXT,
            font=("Malgun Gothic", 11, "bold"), anchor="w", justify="left",
        ).pack(side="left", padx=(8, 0))

        badges = tk.Frame(header, bg=PANEL_BG)
        badges.pack(side="right")
        for kind in order:
            payload = self._sections[kind]
            accent = ACCENT_TOKENS.get(payload.accent, ACCENT_TOKENS["live"])
            text = f"{KIND_LABEL.get(kind, '')} {payload.badge_text}".strip()
            tk.Label(
                badges, text=text, bg=accent["badge_bg"], fg=accent["badge_fg"],
                font=("Malgun Gothic", 8, "bold"), padx=10, pady=3,
            ).pack(side="left", padx=(6, 0))

    def _build_section(self, payload: PopupPayload) -> None:
        assert tk is not None
        accent = ACCENT_TOKENS.get(payload.accent, ACCENT_TOKENS["live"])
        section = tk.Frame(self._body, bg=PANEL_BG)
        section.pack(fill="x", pady=(12, 0))

        head = tk.Frame(section, bg=PANEL_BG)
        head.pack(fill="x", pady=(0, 8))
        left = tk.Frame(head, bg=PANEL_BG)
        left.pack(side="left")
        dot = tk.Canvas(left, width=8, height=8, bg=PANEL_BG, highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 7, 7, fill=accent["dot"], outline="")
        dot.pack(side="left", pady=(4, 0))
        tk.Label(
            left, text=KIND_SECTION_TITLE.get(payload.action_key, payload.title),
            bg=PANEL_BG, fg=PANEL_TEXT, font=("Malgun Gothic", 10, "bold"),
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            left, text=payload.badge_text, bg=PANEL_BG, fg=PANEL_MUTED,
            font=("Malgun Gothic", 9),
        ).pack(side="left", padx=(6, 0))

        tk.Button(
            head, text=payload.confirm_text, command=lambda p=payload: self._confirm_section(p),
            bg=accent["primary_bg"], fg=accent["primary_fg"],
            activebackground=accent["primary_active"], activeforeground=accent["primary_fg"],
            relief="flat", bd=0, padx=14, pady=6, font=("Malgun Gothic", 9, "bold"), cursor="hand2",
        ).pack(side="right")

        if payload.table_rows:
            self._render_table(section, payload.table_rows)
        self._render_lines(section, payload.lines)

    def _render_table(self, parent: Any, rows: list[dict[str, str]]) -> None:
        assert tk is not None
        # 구분(번호)·추가 내용·처리 상황 열은 뺐다(2026-08-19) — 번호는 뜻을 알 수 없는
        # 내부 분류값, 추가 내용은 내용과 중복, 처리 상황은 서버가 항상 빈칸으로 준다
        # (미처리만 알리므로). 원문 사유는 _detail_content 가 내용 칸에 합친다.
        columns = [
            ("emp_id", "사번", 8, "center"),
            ("name", "성명", 8, "center"),
            ("department", "부서", 12, "w"),
            ("date", "일자", 7, "center"),
            ("content", "내용", 48, "w"),
        ]
        table = tk.Frame(parent, bg=TABLE_BORDER, highlightthickness=1, highlightbackground=TABLE_BORDER)
        table.pack(fill="x")
        for index, (key, label, width, anchor) in enumerate(columns):
            table.grid_columnconfigure(index, weight=1 if key == "content" else 0)
            tk.Label(
                table, text=label, bg=TABLE_HEADER_BG, fg=PANEL_TEXT,
                font=("Malgun Gothic", 8, "bold"), width=width, padx=5, pady=5, anchor=anchor,
            ).grid(row=0, column=index, sticky="nsew", padx=(0, 1), pady=(0, 1))
        for row_index, row_data in enumerate(rows, start=1):
            bg = PANEL_BG if row_index % 2 else TABLE_ROW_ALT
            for column_index, (key, _label, width, anchor) in enumerate(columns):
                tk.Label(
                    table, text=row_data.get(key, ""), bg=bg, fg=LINE_TEXT,
                    font=("Malgun Gothic", 8), width=width, padx=5, pady=5, anchor=anchor,
                    justify="left" if anchor == "w" else "center",
                    wraplength=380 if key == "content" else 180,
                ).grid(row=row_index, column=column_index, sticky="nsew", padx=(0, 1), pady=(0, 1))

    def _render_lines(self, parent: Any, lines: list[str]) -> None:
        assert tk is not None
        for line in lines:
            row = tk.Frame(parent, bg=PANEL_BG)
            row.pack(fill="x", pady=2)
            if line.startswith("+"):
                tk.Label(
                    row, text=line, bg=PANEL_BG, fg=PANEL_MUTED,
                    font=("Malgun Gothic", 9, "bold"), anchor="w", justify="left",
                ).pack(fill="x", anchor="w")
                continue
            dot = tk.Canvas(row, width=8, height=8, bg=PANEL_BG, highlightthickness=0, bd=0)
            dot.create_oval(2, 2, 6, 6, fill="#94a3b8", outline="")
            dot.pack(side="left", pady=(5, 0))
            tk.Label(
                row, text=line, bg=PANEL_BG, fg=LINE_TEXT, font=("Malgun Gothic", 9),
                anchor="w", justify="left", wraplength=POPUP_WIDTH - 52,
            ).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _build_footer(self, primary: PopupPayload | None = None) -> None:
        assert tk is not None
        buttons = tk.Frame(self._body, bg=PANEL_BG)
        buttons.pack(fill="x", pady=(16, 0))
        # 단일 섹션이면 확인 버튼을 하단에 둔다(복수일 땐 확인 버튼이 각 섹션에 있음).
        if primary is not None:
            accent = ACCENT_TOKENS.get(primary.accent, ACCENT_TOKENS["live"])
            tk.Button(
                buttons, text=primary.confirm_text,
                command=lambda p=primary: self._confirm_section(p),
                bg=accent["primary_bg"], fg=accent["primary_fg"],
                activebackground=accent["primary_active"], activeforeground=accent["primary_fg"],
                relief="flat", bd=0, padx=16, pady=8, font=("Malgun Gothic", 9, "bold"), cursor="hand2",
            ).pack(side="left")
        tk.Button(
            buttons, text="닫기", command=self._dismiss,
            bg=BUTTON_SUBTLE_BG, fg=BUTTON_SUBTLE_FG, activebackground="#e2e8f0",
            activeforeground=BUTTON_SUBTLE_FG, relief="flat", bd=0, padx=14, pady=8,
            font=("Malgun Gothic", 9), cursor="hand2",
        ).pack(side="left", padx=(8, 0) if primary is not None else (0, 0))
        tk.Button(
            buttons, text="오늘은 그만", command=self._dismiss_today,
            bg=PANEL_BG, fg=BUTTON_MUTED_FG, activebackground=PANEL_BG,
            activeforeground=BUTTON_SUBTLE_FG, relief="flat", bd=0, padx=4, pady=8,
            font=("Malgun Gothic", 9), cursor="hand2",
        ).pack(side="right")

    # ── 동작 ─────────────────────────────────────────────────────
    def _confirm_section(self, payload: PopupPayload) -> None:
        try:
            self._on_confirm(payload)
        finally:
            # 처리한 섹션만 제거 — 다른 종류 알림은 창에 남겨 둔다.
            self._sections.pop(payload.action_key, None)
            if self._sections:
                self._render()
            else:
                self._dismiss()

    def _dismiss(self) -> None:
        self._sections.clear()
        if self._window is not None:
            self._window.withdraw()

    def _dismiss_today(self) -> None:
        try:
            self._on_dismiss_today()
        finally:
            self._dismiss()

    def _destroy_window(self) -> None:
        if self._window is None:
            return
        self._window.destroy()
        self._window = None
        self._body = None
