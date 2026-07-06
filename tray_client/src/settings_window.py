"""설정 창 — 흩어진 토글/버튼을 한 창으로.

트레이 메뉴에 버튼을 계속 늘리는 대신, 근태·점도 알림 개별 on/off, 저울 연동(+상태·재연결),
서버 주소, 부팅 자동 실행을 이 창 하나에서 관리한다. Tkinter Toplevel 이며 팝업 매니저가
소유한 UI 스레드에서 생성/조작된다(TrayApp.open_settings → popup.run_on_ui).
"""

from __future__ import annotations

import logging
from typing import Any

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - 헤드리스/비GUI 환경
    tk = None  # type: ignore[assignment]

from . import autostart

logger = logging.getLogger("irms_notice")

_BG = "#ffffff"
_TEXT = "#0f172a"
_MUTED = "#64748b"
_ACCENT = "#2563eb"
_FONT = "Malgun Gothic"


class SettingsWindow:
    """단일 설정 창(재사용). open() 은 반드시 UI(Tkinter) 스레드에서 호출된다."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._win: Any = None
        self._att_var: Any = None
        self._vis_var: Any = None
        self._scale_var: Any = None
        self._autostart_var: Any = None
        self._server_var: Any = None
        self._scale_status: Any = None

    # UI 스레드에서 호출됨 (popup.run_on_ui 경유)
    def open(self, root: Any) -> None:
        if tk is None or root is None:
            logger.warning("settings window unavailable: tkinter/root missing")
            return
        if self._win is not None:
            try:
                self._win.deiconify()
                self._win.lift()
                self._win.focus_force()
                return
            except tk.TclError:
                self._win = None

        cfg = self._app.config
        win = tk.Toplevel(root)
        win.title("IRMS 현장 도우미 설정")
        win.configure(bg=_BG)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close)
        self._win = win

        pad = tk.Frame(win, bg=_BG, padx=18, pady=16)
        pad.pack(fill="both", expand=True)

        self._att_var = tk.BooleanVar(value=bool(cfg.attendance_alerts_enabled))
        self._vis_var = tk.BooleanVar(value=bool(cfg.viscosity_alerts_enabled))
        self._scale_var = tk.BooleanVar(value=bool(cfg.scale_enabled))
        self._autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        self._server_var = tk.StringVar(value=str(cfg.server_url or ""))

        # ── 알림 ──
        self._section(pad, "알림")
        self._check(pad, "근태 알림 받기", self._att_var)
        self._check(pad, "점도 알림 받기", self._vis_var)

        # ── 저울 연동 ──
        self._section(pad, "저울 연동")
        self._check(pad, "저울 연동 사용 (저울 있는 PC에서만)", self._scale_var)
        row = tk.Frame(pad, bg=_BG)
        row.pack(fill="x", anchor="w", pady=(0, 2))
        self._scale_status = tk.Label(
            row, text=self._app.scale.status_line(), bg=_BG, fg=_MUTED, font=(_FONT, 9), anchor="w"
        )
        self._scale_status.pack(side="left")
        tk.Button(
            row, text="다시 연결", command=self._reconnect, relief="flat", bd=0,
            bg="#f1f5f9", fg=_TEXT, font=(_FONT, 9), padx=10, pady=3, cursor="hand2",
        ).pack(side="right")

        # ── 일반 ──
        self._section(pad, "일반")
        tk.Label(pad, text="서버 주소", bg=_BG, fg=_MUTED, font=(_FONT, 9), anchor="w").pack(fill="x", anchor="w")
        tk.Entry(pad, textvariable=self._server_var, font=(_FONT, 10), width=36).pack(fill="x", anchor="w", pady=(2, 8))
        self._check(pad, "부팅 시 자동 실행", self._autostart_var)
        tk.Button(
            pad, text="로그 폴더 열기", command=self._open_logs, relief="flat", bd=0,
            bg="#f1f5f9", fg=_TEXT, font=(_FONT, 9), padx=10, pady=4, cursor="hand2", anchor="w",
        ).pack(fill="x", anchor="w", pady=(2, 0))

        # ── 버튼 ──
        buttons = tk.Frame(pad, bg=_BG)
        buttons.pack(fill="x", pady=(16, 0))
        tk.Button(
            buttons, text="저장", command=self._save, relief="flat", bd=0,
            bg=_ACCENT, fg="#ffffff", activebackground="#1d4ed8", activeforeground="#ffffff",
            font=(_FONT, 10, "bold"), padx=18, pady=7, cursor="hand2",
        ).pack(side="left")
        tk.Button(
            buttons, text="닫기", command=self._close, relief="flat", bd=0,
            bg="#f1f5f9", fg=_TEXT, font=(_FONT, 10), padx=14, pady=7, cursor="hand2",
        ).pack(side="left", padx=(8, 0))

        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        win.deiconify()
        win.lift()
        win.focus_force()

    # ── 위젯 헬퍼 ──
    def _section(self, parent: Any, title: str) -> None:
        tk.Label(
            parent, text=title, bg=_BG, fg=_TEXT, font=(_FONT, 10, "bold"), anchor="w",
        ).pack(fill="x", anchor="w", pady=(10, 4))

    def _check(self, parent: Any, label: str, var: Any) -> None:
        tk.Checkbutton(
            parent, text=label, variable=var, bg=_BG, fg=_TEXT, font=(_FONT, 10),
            activebackground=_BG, anchor="w", padx=0, selectcolor="#ffffff",
        ).pack(fill="x", anchor="w")

    # ── 동작 ──
    def _reconnect(self) -> None:
        try:
            self._app.scale.reconnect()
            if self._win is not None:
                self._win.after(1500, self._refresh_status)
        except Exception as exc:  # noqa: BLE001
            logger.warning("settings reconnect failed: %s", exc)

    def _refresh_status(self) -> None:
        if self._scale_status is not None:
            try:
                self._scale_status.configure(text=self._app.scale.status_line())
            except tk.TclError:
                pass

    def _open_logs(self) -> None:
        try:
            self._app.open_logs_folder()
        except Exception as exc:  # noqa: BLE001
            logger.warning("settings open logs failed: %s", exc)

    def _save(self) -> None:
        try:
            self._app.apply_settings(
                attendance_alerts=bool(self._att_var.get()),
                viscosity_alerts=bool(self._vis_var.get()),
                scale_enabled=bool(self._scale_var.get()),
                server_url=str(self._server_var.get()),
                autostart_enabled=bool(self._autostart_var.get()),
            )
            self._refresh_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("settings save failed: %s", exc)
        self._close()

    def _close(self) -> None:
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None
