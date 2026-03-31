document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("management-login-form");
  const usernameInput = document.getElementById("manager-username");
  const passwordInput = document.getElementById("manager-password");
  const submitBtn = document.getElementById("management-login-submit");
  const errorNode = document.getElementById("management-login-error");
  const nextInput = document.getElementById("next-url");

  function setError(message) {
    if (!errorNode) {
      return;
    }
    if (!message) {
      errorNode.hidden = true;
      errorNode.textContent = "";
      return;
    }
    errorNode.hidden = false;
    errorNode.textContent = message;
  }

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();

    const username = String(usernameInput?.value || "").trim();
    const password = String(passwordInput?.value || "");

    if (!username || !password) {
      setError("매니저와 비밀번호를 모두 입력하세요.");
      return;
    }

    setError("");
    if (submitBtn) {
      submitBtn.disabled = true;
    }

    try {
      await IRMS.loginManager(username, password);
      const nextUrl = String(nextInput?.value || "/management");
      window.location.assign(nextUrl.startsWith("/") ? nextUrl : "/management");
    } catch (error) {
      setError(error.message === "INVALID_CREDENTIALS" ? "비밀번호가 올바르지 않습니다." : error.message);
      if (submitBtn) {
        submitBtn.disabled = false;
      }
    }
  });
});
