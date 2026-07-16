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

  async function registerWorker(worker) {
    if (workers.some((w) => w.name === worker)) return true;
    if (!window.confirm(`처음 보는 이름입니다: "${worker}"\n작업자로 등록할까요?`)) {
      return false;
    }
    await request("/workers", { method: "POST", body: { name: worker } });
    workers.push({ name: worker, category: null });
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
