(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const safeNextUrl = IRMS._core && IRMS._core.safeNextUrl;
  const form = document.getElementById("blend-login-form");
  const workerInput = document.getElementById("blend-login-worker");
  const workerList = document.getElementById("blend-login-workers");
  const errorEl = document.getElementById("blend-login-error");
  let workers = [];

  if (!form || !request) return;

  function setMessage(message, muted) {
    if (!errorEl) return;
    if (!message) {
      errorEl.textContent = "";
      errorEl.hidden = true;
      return;
    }
    errorEl.textContent = message;
    errorEl.hidden = false;
    errorEl.className = muted ? "login-note" : "login-error";
  }

  function fillWorkers(items) {
    workers = (items || []).map((item) => item.name);
    if (!workerList) return;
    workerList.innerHTML = workers
      .map((name) => `<option value="${name}"></option>`)
      .join("");
  }

  async function registerWorker(worker) {
    if (workers.includes(worker)) return true;
    if (!window.confirm(`처음 보는 이름입니다: "${worker}"\n작업자로 등록할까요?`)) {
      return false;
    }
    await request("/workers", { method: "POST", body: { name: worker } });
    workers.push(worker);
    if (workerList) {
      workerList.insertAdjacentHTML("beforeend", `<option value="${worker}"></option>`);
    }
    return true;
  }

  async function submit(event) {
    event.preventDefault();
    const worker = (workerInput.value || "").trim();
    if (!worker) {
      setMessage("작업자 이름을 입력하세요.", false);
      return;
    }
    try {
      setMessage("작업자 확인 중...", true);
      const canProceed = await registerWorker(worker);
      if (!canProceed) {
        setMessage("등록된 작업자 이름을 선택하거나 새 작업자를 등록하세요.", false);
        return;
      }
      await request("/blend/session/login", { method: "POST", body: { worker } });
      const nextUrl = safeNextUrl
        ? safeNextUrl(form.dataset.nextUrl || "/blend", "/blend")
        : "/blend";
      window.location.assign(nextUrl);
    } catch (error) {
      setMessage(error.message || "작업자 확인에 실패했습니다.", false);
    }
  }

  request("/workers")
    .then((data) => fillWorkers(data.items || []))
    .catch(() => fillWorkers([]));
  form.addEventListener("submit", submit);
  workerInput.focus();
})();
