(function () {
  "use strict";

  const form = document.getElementById("att-change-form");
  const currentInput = document.getElementById("att-current-password");
  const newInput = document.getElementById("att-new-password");
  const confirmInput = document.getElementById("att-new-password-confirm");
  const errorEl = document.getElementById("att-change-error");
  const logoutBtn = document.getElementById("att-change-logout");

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
    if (text.includes("CURRENT_PASSWORD_WRONG"))
      return "현재 비밀번호가 맞지 않습니다.";
    if (text.includes("PASSWORD_TOO_SHORT"))
      return "새 비밀번호는 4자 이상이어야 합니다.";
    if (text.includes("PASSWORD_SAME_AS_EMPID"))
      return "새 비밀번호는 사번과 달라야 합니다.";
    if (text.includes("ATTENDANCE_LOGIN_REQUIRED"))
      return "세션이 만료되었습니다. 다시 로그인해주세요.";
    return text || "비밀번호 변경에 실패했습니다.";
  }

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function postJson(path, body) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "x-csrftoken": csrfToken(),
      },
      body: JSON.stringify(body || {}),
    });
    if (!response.ok) {
      let detail = "";
      try {
        const payload = await response.json();
        detail = payload?.detail?.detail || payload?.detail || "";
      } catch (_) {
        detail = response.statusText;
      }
      throw new Error(String(detail));
    }
    return response.json();
  }

  async function onSubmit(event) {
    event.preventDefault();
    const current = currentInput.value || "";
    const next = newInput.value || "";
    const confirm = confirmInput.value || "";
    if (next !== confirm) {
      setHint("새 비밀번호와 확인 값이 일치하지 않습니다.", "error");
      return;
    }
    setHint("변경 중...", "muted");
    try {
      await postJson("/api/attendance/change-password", {
        current_password: current,
        new_password: next,
      });
      // Clear any dismissed-banner flag so the new state (no banner) is clean.
      try {
        Object.keys(localStorage).forEach((key) => {
          if (key.startsWith("irms_att_reset_dismissed_")) {
            localStorage.removeItem(key);
          }
        });
      } catch (_) {
        /* ignore */
      }
      setHint("변경 완료. 이동 중...", "muted");
      window.location.assign("/attendance");
    } catch (error) {
      setHint(mapError(error.message), "error");
    }
  }

  async function onLogout() {
    try {
      await postJson("/api/attendance/logout", {});
    } catch (_) {
      /* ignore */
    }
    window.location.assign("/attendance/login");
  }

  const laterBtn = document.getElementById("att-change-later");

  form?.addEventListener("submit", onSubmit);
  logoutBtn?.addEventListener("click", onLogout);
  laterBtn?.addEventListener("click", () => {
    window.location.assign("/attendance");
  });
  currentInput?.focus();
})();
