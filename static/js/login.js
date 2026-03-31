document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("login-form");
  const usernameInput = document.getElementById("username");
  const passwordInput = document.getElementById("password");
  const submitBtn = document.getElementById("login-submit");
  const errorNode = document.getElementById("login-error");
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
      setError("아이디와 비밀번호를 모두 입력하세요.");
      return;
    }

    setError("");
    if (submitBtn) {
      submitBtn.disabled = true;
    }

    try {
      await IRMS.login(username, password);
      const nextUrl = String(nextInput?.value || "/");
      window.location.assign(nextUrl.startsWith("/") ? nextUrl : "/");
    } catch (error) {
      setError(error.message === "INVALID_CREDENTIALS" ? "아이디 또는 비밀번호가 올바르지 않습니다." : error.message);
      if (submitBtn) {
        submitBtn.disabled = false;
      }
    }
  });
});
