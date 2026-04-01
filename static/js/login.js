document.addEventListener("DOMContentLoaded", () => {
  IRMS.bindLoginForm({
    formId: "login-form",
    usernameId: "username",
    passwordId: "password",
    submitId: "login-submit",
    errorId: "login-error",
    nextId: "next-url",
    loginFn: IRMS.login,
    defaultNext: "/",
    emptyMsg: "아이디와 비밀번호를 모두 입력하세요.",
    failMsg: "아이디 또는 비밀번호가 올바르지 않습니다.",
  });
});
