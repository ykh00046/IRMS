document.addEventListener("DOMContentLoaded", () => {
  IRMS.bindLoginForm({
    formId: "management-login-form",
    usernameId: "manager-username",
    passwordId: "manager-password",
    submitId: "management-login-submit",
    errorId: "management-login-error",
    nextId: "next-url",
    loginFn: IRMS.loginManager,
    defaultNext: "/management",
    emptyMsg: "책임자와 비밀번호를 모두 입력하세요.",
    failMsg: "비밀번호가 올바르지 않습니다.",
  });
});
