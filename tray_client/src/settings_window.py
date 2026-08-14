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
from .version import __version__

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
        self._rescale_var: Any = None
        self._scale_var: Any = None
        self._autostart_var: Any = None
        self._server_var: Any = None
        self._token_var: Any = None
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
        self._rescale_var = tk.BooleanVar(value=bool(cfg.rescale_alerts_enabled))
        self._scale_var = tk.BooleanVar(value=bool(cfg.scale_enabled))
        self._autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        self._server_var = tk.StringVar(value=str(cfg.server_url or ""))
        self._token_var = tk.StringVar(value=str(getattr(cfg, "tray_api_token", "") or ""))

        # ── 알림 ──
        self._section(pad, "알림")
        self._check(pad, "근태 알림 받기", self._att_var)
        self._check(pad, "점도 알림 받기", self._vis_var)
        self._check(pad, "증량 확인 알림 받기", self._rescale_var)
        # 알림 시각 — 쉼표로 구분한 24시간제 시(時). 야간 근무 대응(2026-08-14).
        # 잘못 입력하면 저장 시 유효값만 남긴다(전부 무효면 기존 값 유지).
        self._hours_var = tk.StringVar(
            value=", ".join(str(h) for h in (cfg.alert_hours or []))
        )
        tk.Label(
            pad, text="알림 시각 (24시간제, 쉼표 구분 - 예: 1, 9, 13, 16, 21)",
            bg=_BG, fg=_MUTED, font=(_FONT, 9), anchor="w",
        ).pack(fill="x", anchor="w")
        tk.Entry(pad, textvariable=self._hours_var, font=(_FONT, 10), width=36).pack(
            fill="x", anchor="w", pady=(2, 8)
        )

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
        # 서버가 트레이 토큰을 요구하도록 설정된 경우에만 채운다. 비워두면 미사용(기본).
        tk.Label(
            pad, text="트레이 토큰 (선택)", bg=_BG, fg=_MUTED, font=(_FONT, 9), anchor="w",
        ).pack(fill="x", anchor="w")
        tk.Entry(
            pad, textvariable=self._token_var, font=(_FONT, 10), width=36, show="*",
        ).pack(fill="x", anchor="w", pady=(2, 8))
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

        # 버전 표기 — 현장 문의 시 설치본 확인용(version.py 단일 소스).
        tk.Label(
            pad, text=f"버전 {__version__}", bg=_BG, fg=_MUTED, font=(_FONT, 8), anchor="e",
        ).pack(fill="x", anchor="e", pady=(10, 0))

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
                rescale_alerts=bool(self._rescale_var.get()),
                scale_enabled=bool(self._scale_var.get()),
                server_url=str(self._server_var.get()),
                autostart_enabled=bool(self._autostart_var.get()),
                tray_api_token=str(self._token_var.get()),
                alert_hours=self._parse_hours(str(self._hours_var.get())),
            )
            self._refresh_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("settings save failed: %s", exc)
        self._close()

    @staticmethod
    def _parse_hours(raw: str) -> list | None:
        """"1, 9, 13" 형태 입력 → 시각 리스트. 유효값이 하나도 없으면 None(기존 유지)."""
        hours = []
        for part in (raw or "").replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                continue
            if 0 <= value <= 23:
                hours.append(value)
        return sorted(set(hours)) or None

    def _close(self) -> None:
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None
