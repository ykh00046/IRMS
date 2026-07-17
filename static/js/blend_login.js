(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const safeNextUrl = IRMS._core && IRMS._core.safeNextUrl;
  const form = document.getElementById("blend-login-form");
  const workerInput = document.getElementById("blend-login-worker");
  const suggestBox = document.getElementById("blend-login-suggest");
  const errorEl = document.getElementById("blend-login-error");
  const partSelect = document.getElementById("blend-login-part");
  // 새 작업자 등록 파트 선택 모달(confirm 대체)
  const partDialog = document.getElementById("blend-part-dialog");
  const partDialogMessage = document.getElementById("blend-part-dialog-message");
  const partDialogCancel = document.getElementById("blend-part-dialog-cancel");
  let partDialogResolver = null;   // 선택/취소 를 Promise 로 전달
  let isPartDialogOpen = false;    // 폼 submit 가드(Enter 재제출 방지)
  let workers = [];        // [{name, category}] — category 는 파트(약품/합성/잉크/용수 | null)

  if (!form || !request) return;

  function currentPart() {
    return partSelect ? partSelect.value : "";
  }

  // ── 자체 제안 목록: 파트 선택 → 그 파트 이름만. 타이핑 검색은 전체에서 동작 ──
  function renderSuggest() {
    if (!suggestBox) return;
    const query = (workerInput.value || "").trim().toLowerCase();
    const part = currentPart();
    let pool;
    if (query) {
      pool = workers;                                        // 타이핑 = 전체 검색
    } else if (part === "__none__") {
      pool = workers.filter((w) => !w.category);             // 파트 미지정만
    } else if (part) {
      pool = workers.filter((w) => w.category === part);     // 선택한 파트만
    } else {
      suggestBox.hidden = true;                              // 파트 미선택 → 목록 없음
      return;
    }
    const matches = query
      ? pool.filter((w) => w.name.toLowerCase().includes(query))
      : pool;
    suggestBox.innerHTML = "";
    if (!matches.length) {
      suggestBox.hidden = true;
      return;
    }
    matches.forEach((w) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "worker-suggest-item";
      item.textContent = w.name;
      // blur 보다 먼저 실행되도록 mousedown 사용
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        workerInput.value = w.name;
        suggestBox.hidden = true;
        workerInput.focus();
      });
      suggestBox.appendChild(item);
    });
    suggestBox.hidden = false;
  }

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
    workers = (items || []).map((item) => ({
      name: item.name,
      category: item.category || null,
    }));
  }

  // ── 새 작업자 파트 선택 모달 — confirm() 대체 ──
  // resolve(null) = 취소, resolve("약품" 등) = 선택한 파트.
  function openPartDialog(worker) {
    if (!partDialog) {
      // 모달이 없는 환경(예: 마크업 누락)은 미지정(undefined)으로 등록 진행 —
      // null(취소)과 구분해야 등록이 막히지 않는다.
      return Promise.resolve(undefined);
    }
    partDialogMessage.textContent =
      `"${worker}" 님은 처음 등록하는 작업자입니다. 소속 파트를 선택해 주세요.`;
    partDialog.hidden = false;
    isPartDialogOpen = true;
    const firstBtn = partDialog.querySelector("[data-part]");
    if (firstBtn) firstBtn.focus();
    return new Promise((resolve) => {
      partDialogResolver = resolve;
    });
  }

  function closePartDialog() {
    if (partDialog) partDialog.hidden = true;
    isPartDialogOpen = false;
    const resolve = partDialogResolver;
    partDialogResolver = null;
    return resolve;
  }

  if (partDialog) {
    partDialog.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-part]");
      if (btn) {
        const resolve = closePartDialog();
        if (resolve) resolve(btn.dataset.part);
      }
    });
    if (partDialogCancel) {
      partDialogCancel.addEventListener("click", () => {
        const resolve = closePartDialog();
        if (resolve) resolve(null);
      });
    }
    // Esc = 취소(모달이 포커스를 잃기 전 입력 요소에서 누른 경우 포함)
    partDialog.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const resolve = closePartDialog();
        if (resolve) resolve(null);
      }
    });
  }

  async function registerWorker(worker) {
    if (workers.some((w) => w.name === worker)) return true;
    const category = await openPartDialog(worker);
    if (category === null) {
      // 취소 — 기존 confirm 취소 경로와 동일 메시지 유지(submit 호출부)
      return false;
    }
    await request("/workers", { method: "POST", body: { name: worker, category: category || null } });
    workers.push({ name: worker, category: category || null });
    return true;
  }

  async function submit(event) {
    event.preventDefault();
    // 모달이 열려 있으면 Enter 재제출 차단(파트 버튼 클릭만 진행)
    if (isPartDialogOpen) return;
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
    .then((data) => {
      fillWorkers(data.items || []);
      // 자동 포커스가 명단 로딩보다 빠른 경우 — 로딩 완료 시점에 목록 표시
      if (document.activeElement === workerInput) renderSuggest();
    })
    .catch(() => fillWorkers([]));
  form.addEventListener("submit", submit);
  // 파트 드롭다운 — 고르면 그 파트 명단이 바로 펼쳐진다
  if (partSelect) {
    partSelect.addEventListener("change", () => {
      workerInput.value = "";
      workerInput.focus();
      renderSuggest();
    });
  }
  workerInput.addEventListener("focus", renderSuggest);
  workerInput.addEventListener("input", renderSuggest);
  workerInput.addEventListener("blur", () => {
    if (suggestBox) suggestBox.hidden = true;
  });
  workerInput.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && suggestBox) suggestBox.hidden = true;
  });
  workerInput.focus();
})();
