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
}


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
                    "code": "",
                    "content": " / ".join(str(issue) for issue in item.get("issues") or []),
                    "extra_content": "",
                    "status": "",
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
                    "code": str(detail.get("code") or ""),
                    "content": str(detail.get("content") or ""),
                    "extra_content": str(detail.get("extra_content") or ""),
                    "status": str(detail.get("status") or ""),
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


def build_viscosity_popup_payload(payload: dict[str, Any]) -> PopupPayload:
    items = list(payload.get("items") or [])
    lines = [
        f"{str(item.get('code') or item.get('name') or '').strip()} 점도를 입력하세요."
        for item in items[:POPUP_MAX_NAMES]
    ]
    remaining = max(0, len(items) - len(lines))
    if remaining > 0:
        lines.append(f"+{remaining}개 품목 추가")
    return PopupPayload(
        title="점도 입력 필요",
        badge_text=f"{len(items)}개" if items else "확인",
        summary="지정된 품목 중 오늘 점도 기록이 없는 품목이 있습니다.",
        lines=lines,
        accent="viscosity",
        action_key="viscosity",
        confirm_text="점도 등록",
    )


class AttendanceAlertPopupManager:
    def __init__(
        self,
        on_confirm: Callable[[PopupPayload], None],
        on_dismiss_today: Callable[[], None],
    ) -> None:
        self._on_confirm = on_confirm
        self._on_dismiss_today = on_dismiss_today
        self._queue: queue.Queue[tuple[str, PopupPayload | None]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._root = None
        self._window = None
        self._title_var = None
        self._badge_var = None
        self._summary_var = None
        self._lines_frame = None
        self._status_dot = None
        self._badge_label = None
        self._confirm_button = None
        self._current_payload: PopupPayload | None = None

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
        if should_stop:
            self._destroy_window()
            self._root.quit()
            self._root.destroy()
            self._root = None
            return
        self._root.after(QUEUE_POLL_MS, self._drain_queue)

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
        window.protocol("WM_DELETE_WINDOW", self._dismiss_window)

        panel = tk.Frame(
            window,
            bg=PANEL_BG,
            highlightthickness=1,
            highlightbackground=PANEL_BORDER,
            highlightcolor=PANEL_BORDER,
            padx=16,
            pady=16,
        )
        panel.pack(fill="both", expand=True)

        self._title_var = tk.StringVar()
        self._badge_var = tk.StringVar()
        self._summary_var = tk.StringVar()

        header = tk.Frame(panel, bg=PANEL_BG)
        header.pack(fill="x")
        title_row = tk.Frame(header, bg=PANEL_BG)
        title_row.pack(side="left", fill="x", expand=True)

        status_dot = tk.Canvas(title_row, width=10, height=10, bg=PANEL_BG, highlightthickness=0, bd=0)
        status_dot.create_oval(2, 2, 8, 8, fill=ACCENT_TOKENS["live"]["dot"], outline="")
        status_dot.pack(side="left", pady=(3, 0))
        self._status_dot = status_dot

        tk.Label(
            title_row,
            textvariable=self._title_var,
            bg=PANEL_BG,
            fg=PANEL_TEXT,
            font=("Malgun Gothic", 11, "bold"),
            anchor="w",
            justify="left",
        ).pack(side="left", padx=(8, 0))

        badge_label = tk.Label(
            header,
            textvariable=self._badge_var,
            bg=ACCENT_TOKENS["live"]["badge_bg"],
            fg=ACCENT_TOKENS["live"]["badge_fg"],
            font=("Malgun Gothic", 8, "bold"),
            padx=10,
            pady=3,
        )
        badge_label.pack(side="right")
        self._badge_label = badge_label

        tk.Label(
            panel,
            textvariable=self._summary_var,
            bg=PANEL_BG,
            fg=PANEL_MUTED,
            font=("Malgun Gothic", 9),
            anchor="w",
            justify="left",
            wraplength=POPUP_WIDTH - 48,
            pady=10,
        ).pack(fill="x")

        divider = tk.Frame(panel, bg="#eef2f7", height=1)
        divider.pack(fill="x", pady=(0, 12))

        lines_frame = tk.Frame(panel, bg=PANEL_BG)
        lines_frame.pack(fill="x")
        self._lines_frame = lines_frame

        buttons = tk.Frame(panel, bg=PANEL_BG)
        buttons.pack(fill="x", pady=(14, 0))
        confirm_button = tk.Button(
            buttons,
            text="확인",
            command=self._confirm,
            bg=ACCENT_TOKENS["live"]["primary_bg"],
            fg=ACCENT_TOKENS["live"]["primary_fg"],
            activebackground=ACCENT_TOKENS["live"]["primary_active"],
            activeforeground=ACCENT_TOKENS["live"]["primary_fg"],
            relief="flat",
            bd=0,
            padx=16,
            pady=8,
            font=("Malgun Gothic", 9, "bold"),
            cursor="hand2",
        )
        confirm_button.pack(side="left")
        self._confirm_button = confirm_button

        tk.Button(
            buttons,
            text="닫기",
            command=self._dismiss_window,
            bg=BUTTON_SUBTLE_BG,
            fg=BUTTON_SUBTLE_FG,
            activebackground="#e2e8f0",
            activeforeground=BUTTON_SUBTLE_FG,
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            font=("Malgun Gothic", 9),
            cursor="hand2",
        ).pack(side="left", padx=(8, 0))
        tk.Button(
            buttons,
            text="오늘은 그만",
            command=self._dismiss_today,
            bg=PANEL_BG,
            fg=BUTTON_MUTED_FG,
            activebackground=PANEL_BG,
            activeforeground=BUTTON_SUBTLE_FG,
            relief="flat",
            bd=0,
            padx=4,
            pady=8,
            font=("Malgun Gothic", 9),
            cursor="hand2",
        ).pack(side="right")

        self._window = window

    def _apply_accent(self, payload: PopupPayload) -> None:
        assert tk is not None
        accent = ACCENT_TOKENS.get(payload.accent, ACCENT_TOKENS["live"])
        if self._status_dot is not None:
            self._status_dot.delete("all")
            self._status_dot.create_oval(2, 2, 8, 8, fill=accent["dot"], outline="")
        if self._badge_label is not None:
            self._badge_label.configure(bg=accent["badge_bg"], fg=accent["badge_fg"])
        if self._confirm_button is not None:
            self._confirm_button.configure(
                bg=accent["primary_bg"],
                fg=accent["primary_fg"],
                activebackground=accent["primary_active"],
                activeforeground=accent["primary_fg"],
                text=payload.confirm_text,
            )

    def _render_table(self, payload: PopupPayload) -> None:
        assert tk is not None
        if self._lines_frame is None:
            return
        columns = [
            ("emp_id", "사번", 8, "center"),
            ("name", "성명", 8, "center"),
            ("department", "부서", 12, "w"),
            ("date", "일자", 7, "center"),
            ("code", "구분", 5, "center"),
            ("content", "내용", 24, "w"),
            ("extra_content", "추가 내용", 30, "w"),
            ("status", "처리 상황", 22, "w"),
        ]
        table = tk.Frame(
            self._lines_frame,
            bg=TABLE_BORDER,
            highlightthickness=1,
            highlightbackground=TABLE_BORDER,
        )
        table.pack(fill="x")

        for index, (_key, label, width, anchor) in enumerate(columns):
            table.grid_columnconfigure(index, weight=1 if index in (5, 6, 7) else 0)
            tk.Label(
                table,
                text=label,
                bg=TABLE_HEADER_BG,
                fg=PANEL_TEXT,
                font=("Malgun Gothic", 8, "bold"),
                width=width,
                padx=5,
                pady=5,
                anchor=anchor,
            ).grid(row=0, column=index, sticky="nsew", padx=(0, 1), pady=(0, 1))

        for row_index, row_data in enumerate(payload.table_rows, start=1):
            bg = PANEL_BG if row_index % 2 else TABLE_ROW_ALT
            for column_index, (key, _label, width, anchor) in enumerate(columns):
                tk.Label(
                    table,
                    text=row_data.get(key, ""),
                    bg=bg,
                    fg=LINE_TEXT,
                    font=("Malgun Gothic", 8),
                    width=width,
                    padx=5,
                    pady=5,
                    anchor=anchor,
                    justify="left" if anchor == "w" else "center",
                    wraplength=260 if key in ("extra_content", "status") else 180,
                ).grid(row=row_index, column=column_index, sticky="nsew", padx=(0, 1), pady=(0, 1))

    def _render_lines(self, payload: PopupPayload) -> None:
        assert tk is not None
        if self._lines_frame is None:
            return
        for child in self._lines_frame.winfo_children():
            child.destroy()
        if payload.table_rows:
            self._render_table(payload)
        for line in payload.lines:
            row = tk.Frame(self._lines_frame, bg=PANEL_BG)
            row.pack(fill="x", pady=2)
            if line.startswith("+"):
                tk.Label(
                    row,
                    text=line,
                    bg=PANEL_BG,
                    fg=PANEL_MUTED,
                    font=("Malgun Gothic", 9, "bold"),
                    anchor="w",
                    justify="left",
                ).pack(fill="x", anchor="w")
                continue
            dot = tk.Canvas(row, width=8, height=8, bg=PANEL_BG, highlightthickness=0, bd=0)
            dot.create_oval(2, 2, 6, 6, fill="#94a3b8", outline="")
            dot.pack(side="left", pady=(5, 0))
            tk.Label(
                row,
                text=line,
                bg=PANEL_BG,
                fg=LINE_TEXT,
                font=("Malgun Gothic", 9),
                anchor="w",
                justify="left",
                wraplength=POPUP_WIDTH - 52,
            ).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _show_payload(self, payload: PopupPayload) -> None:
        self._ensure_window()
        if (
            self._window is None
            or self._title_var is None
            or self._badge_var is None
            or self._summary_var is None
        ):
            return
        self._current_payload = payload
        self._title_var.set(payload.title)
        self._badge_var.set(payload.badge_text)
        self._summary_var.set(payload.summary)
        self._apply_accent(payload)
        self._render_lines(payload)

        self._window.update_idletasks()
        height = self._window.winfo_reqheight()
        screen_width = self._window.winfo_screenwidth()
        screen_height = self._window.winfo_screenheight()
        x = max(0, screen_width - POPUP_WIDTH - POPUP_MARGIN_X)
        y = max(0, screen_height - height - POPUP_MARGIN_Y)
        self._window.geometry(f"{POPUP_WIDTH}x{height}+{x}+{y}")
        self._window.deiconify()
        self._window.lift()

    def _confirm(self) -> None:
        payload = self._current_payload
        if payload is None:
            self._dismiss_window()
            return
        try:
            self._on_confirm(payload)
        finally:
            self._dismiss_window()

    def _dismiss_today(self) -> None:
        try:
            self._on_dismiss_today()
        finally:
            self._dismiss_window()

    def _dismiss_window(self) -> None:
        if self._window is None:
            return
        self._window.withdraw()

    def _destroy_window(self) -> None:
        if self._window is None:
            return
        self._window.destroy()
        self._window = None
