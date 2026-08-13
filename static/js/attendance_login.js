(function () {
  "use strict";

  const form = document.getElementById("att-login-form");
  const empInput = document.getElementById("att-emp-id");
  const passwordInput = document.getElementById("att-password");
  const errorEl = document.getElementById("att-login-error");
  const noticeEl = document.getElementById("att-login-notice");
  if (!form) return;

  // 자동 로그아웃으로 되돌아온 경우의 안내(attendance_session.js 가 ?reason=idle 로
  // 되돌린다). 이유를 안 적으면 보던 화면이 왜 사라졌는지 알 수 없다.
  const REASON_NOTICES = {
    idle: "자동 로그아웃되었습니다 — 다시 로그인해 주세요.",
  };

  function showReasonNotice() {
    if (!noticeEl) return;
    let reason = "";
    try {
      reason = new URLSearchParams(window.location.search).get("reason") || "";
    } catch (_) {
      reason = "";
    }
    const message = REASON_NOTICES[reason];
    if (!message) return;
    noticeEl.textContent = message;
    noticeEl.hidden = false;
  }

  function minutesUntil(isoText) {
    const when = Date.parse(String(isoText || ""));
    if (!isFinite(when)) return 0;
    return Math.max(1, Math.ceil((when - Date.now()) / 60000));
  }

  function setHint(message, tone) {
    if (!errorEl) return;
    if (!message) {
      errorEl.textContent = "";
      errorEl.hidden = true;
      return;
    }
    errorEl.textContent = message;
    errorEl.hidden = false;
    errorEl.className = tone === "muted" ? "login-note" : "login-error";
  }

  // detail 은 {detail: "CODE", remaining: N} / {detail: "LOCKED", locked_until: "...Z"}
  // 구조체다(src/attendance_auth.py). 예전에는 코드 문자열만 읽어, 몇 번 남았는지도
  // 언제 풀리는지도 알려주지 못했다(2026-08-14 검토 6번).
  function mapError(detail) {
    const isObject = detail && typeof detail === "object";
    const text = String((isObject ? detail.detail || detail.code : detail) || "");

    if (text.includes("LOCKED")) {
      const minutes = isObject ? minutesUntil(detail.locked_until) : 0;
      const when = minutes
        ? `약 ${minutes}분 후 자동 해제`
        : "잠시 후 자동 해제";
      return `계정이 잠겼습니다(${when}). 급하면 책임자에게 잠금 해제를 요청하세요.`;
    }
    // 로그인(authenticate)은 계정 없음·미프로비저닝·엑셀에 없는 사번을 전부
    // INVALID_CREDENTIALS 로 통일 응답한다(§4.1 보안 계약). EMP_NOT_IN_EXCEL 은
    // 책임자 재발급 경로에서만 나오므로 이 로그인 화면에선 도달 불가라 제거했다.
    if (text.includes("INVALID_CREDENTIALS")) {
      const remaining = isObject ? Number(detail.remaining) : NaN;
      if (isFinite(remaining) && remaining > 0) {
        return `사번 또는 비밀번호가 올바르지 않습니다 — 남은 시도 ${remaining}회.`;
      }
      return "사번 또는 비밀번호가 올바르지 않습니다.";
    }
    if (text.includes("MONTH_FILE_NOT_FOUND"))
      return "이번 달 근태 파일이 아직 없습니다.";
    return text || "로그인에 실패했습니다.";
  }

  async function submit(event) {
    event.preventDefault();
    if (noticeEl) noticeEl.hidden = true;   // 자동 로그아웃 안내는 한 번만
    const empId = (empInput.value || "").trim();
    const password = passwordInput.value || "";
    if (!empId || !password) {
      setHint("사번과 비밀번호를 입력해주세요.", "error");
      return;
    }
    setHint("로그인 중...", "muted");
    try {
      const response = await fetch("/api/attendance/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ emp_id: empId, password }),
      });
      if (!response.ok) {
        let detail = "";
        try {
          const payload = await response.json();
          detail = payload?.detail ?? "";
        } catch (_) {
          detail = response.statusText;
        }
        setHint(mapError(detail), "error");
        return;
      }
      await response.json();
      setHint("로그인 성공. 이동 중...", "muted");
      window.location.assign("/attendance");
    } catch (error) {
      setHint(error.message || "네트워크 오류", "error");
    }
  }

  form.addEventListener("submit", submit);
  showReasonNotice();
  empInput?.focus();

  window.IRMS = window.IRMS || {};
  window.IRMS.__attendanceLoginTest = { mapError, minutesUntil };
})();
