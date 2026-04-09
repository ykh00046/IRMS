document.addEventListener("DOMContentLoaded", () => {
  const nextInput = document.getElementById("next-url");
  const buttons = Array.from(document.querySelectorAll("[data-operator-select]"));

  async function handleSelect(button) {
    const userId = Number(button.dataset.userId);
    if (!Number.isFinite(userId)) {
      return;
    }

    buttons.forEach((node) => {
      node.disabled = true;
    });

    try {
      await IRMS.selectOperator(userId);
      const nextUrl = String(nextInput?.value || "/weighing");
      window.location.assign(nextUrl.startsWith("/") ? nextUrl : "/weighing");
    } catch (error) {
      IRMS.notify(`담당자 선택 실패: ${error.message}`, "error");
      buttons.forEach((node) => {
        node.disabled = false;
      });
    }
  }

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      handleSelect(button);
    });
  });
});
