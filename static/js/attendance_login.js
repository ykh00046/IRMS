(function () {
  "use strict";

  const form = document.getElementById("att-login-form");
  const empInput = document.getElementById("att-emp-id");
  const passwordInput = document.getElementById("att-password");
  const errorEl = document.getElementById("att-login-error");
  if (!form) return;

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

  function mapError(raw) {
    const text = String(raw || "");
    if (text.includes("LOCKED"))
      return "잠금 상태입니다. 5분 후 다시 시도해주세요.";
    // 로그인(authenticate)은 계정 없음·미프로비저닝·엑셀에 없는 사번을 전부
    // INVALID_CREDENTIALS 로 통일 응답한다(§4.1 보안 계약). EMP_NOT_IN_EXCEL 은
    // 책임자 재발급 경로에서만 나오므로 이 로그인 화면에선 도달 불가라 제거했다.
    if (text.includes("INVALID_CREDENTIALS"))
      return "사번 또는 비밀번호가 올바르지 않습니다.";
    if (text.includes("MONTH_FILE_NOT_FOUND"))
      return "이번 달 근태 파일이 아직 없습니다.";
    return text || "로그인에 실패했습니다.";
  }

  async function submit(event) {
    event.preventDefault();
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
          detail = payload?.detail?.detail || payload?.detail || "";
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
  empInput?.focus();
})();
