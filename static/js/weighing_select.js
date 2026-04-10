document.addEventListener("DOMContentLoaded", () => {
  IRMS.bindLoginForm({
    formId: "operator-login-form",
    usernameId: "operator-username",
    passwordId: "operator-password",
    submitId: "operator-login-submit",
    errorId: "operator-login-error",
    nextId: "next-url",
    loginFn: IRMS.loginOperator,
    defaultNext: "/weighing",
    emptyMsg: "담당자와 비밀번호를 모두 입력하세요.",
    failMsg: "비밀번호가 올바르지 않습니다.",
  });
});
