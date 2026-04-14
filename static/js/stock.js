(() => {
  const tbody = document.getElementById("stock-body");
  const banner = document.getElementById("mgmt-stock-banner");
  const searchInput = document.getElementById("stock-search");
  const refreshBtn = document.getElementById("stock-refresh");

  const modal = document.getElementById("stock-modal");
  const modalTitle = document.getElementById("stock-modal-title");
  const modalMaterial = document.getElementById("stock-modal-material");
  const modalAmountLabel = document.getElementById("stock-modal-amount-label");
  const modalAmount = document.getElementById("stock-modal-amount");
  const modalNote = document.getElementById("stock-modal-note");
  const modalSubmit = document.getElementById("stock-modal-submit");
  const modalClose = document.getElementById("stock-modal-close");

  const logModal = document.getElementById("stock-log-modal");
  const logTitle = document.getElementById("stock-log-title");
  const logBody = document.getElementById("stock-log-body");
  const logClose = document.getElementById("stock-log-close");

  if (!tbody) return;

  const fmt = (n) => {
    const v = Number(n || 0);
    return v.toLocaleString("ko-KR", { maximumFractionDigits: 3 });
  };
  const fmtTime = (iso) => {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString("ko-KR", { hour12: false });
    } catch (_) {
      return iso;
    }
  };

  const state = {
    items: [],
    filter: "",
    modalMode: null,
    modalMaterialId: null,
  };

  async function fetchStock() {
    const res = await fetch("/api/materials/stock");
    if (!res.ok) return;
    const data = await res.json();
    state.items = data.items || [];
    render();
  }

  function render() {
    const q = state.filter.trim().toLowerCase();
    const filtered = q
      ? state.items.filter((m) => (m.name || "").toLowerCase().includes(q))
      : state.items;

    tbody.innerHTML = filtered
      .map((m) => {
        const statusLabel =
          m.status === "negative" ? "음수" : m.status === "low" ? "부족" : "정상";
        const rowClass =
          m.status === "negative" ? "row-negative" : m.status === "low" ? "row-low" : "";
        return `
          <tr class="${rowClass}" data-id="${m.id}" data-name="${escapeHtml(m.name)}">
            <td>${escapeHtml(m.name)}</td>
            <td>${escapeHtml(m.category || "")}</td>
            <td class="num">${fmt(m.stock_quantity)}</td>
            <td class="num">${fmt(m.stock_threshold)}</td>
            <td><span class="stock-status stock-${m.status}">${statusLabel}</span></td>
            <td>
              <button class="btn btn-sm" data-action="restock">입고</button>
              <button class="btn btn-sm" data-action="adjust">조정</button>
              <button class="btn btn-sm" data-action="discard">폐기</button>
              <button class="btn btn-sm" data-action="threshold">임계치</button>
              <button class="btn btn-sm" data-action="log">이력</button>
            </td>
          </tr>`;
      })
      .join("");

    const alerts = state.items.filter((m) => m.status !== "ok");
    if (alerts.length) {
      const low = alerts.filter((m) => m.status === "low").length;
      const neg = alerts.filter((m) => m.status === "negative").length;
      const parts = [];
      if (neg) parts.push(`음수 재고 ${neg}개`);
      if (low) parts.push(`임계치 미달 ${low}개`);
      banner.textContent = `⚠ 재고 주의: ${parts.join(", ")}`;
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function openModal(mode, id, name) {
    state.modalMode = mode;
    state.modalMaterialId = id;
    const titles = {
      restock: "입고 처리",
      adjust: "재고 조정",
      discard: "폐기 처리",
      threshold: "임계치 설정",
    };
    const labels = {
      restock: "입고량 (g)",
      adjust: "실제 재고량 (g)",
      discard: "폐기량 (g)",
      threshold: "임계치 (g)",
    };
    modalTitle.textContent = titles[mode];
    modalMaterial.textContent = name;
    modalAmountLabel.textContent = labels[mode];
    modalAmount.value = "";
    modalNote.value = "";
    modalNote.parentElement.style.display = mode === "threshold" ? "none" : "";
    modal.hidden = false;
    modalAmount.focus();
  }

  function closeModal() {
    modal.hidden = true;
    state.modalMode = null;
  }

  async function submitModal() {
    const id = state.modalMaterialId;
    const mode = state.modalMode;
    const amount = parseFloat(modalAmount.value);
    const note = modalNote.value.trim();

    if (isNaN(amount)) {
      alert("숫자를 입력해주세요.");
      return;
    }

    let url, body;
    if (mode === "restock") {
      url = `/api/materials/${id}/stock/restock`;
      body = { amount, note: note || null };
    } else if (mode === "adjust") {
      if (!note) return alert("조정 사유를 입력해주세요.");
      url = `/api/materials/${id}/stock/adjust`;
      body = { new_quantity: amount, note };
    } else if (mode === "discard") {
      if (!note) return alert("폐기 사유를 입력해주세요.");
      url = `/api/materials/${id}/stock/discard`;
      body = { amount, note };
    } else if (mode === "threshold") {
      url = `/api/materials/${id}/stock-threshold`;
      body = { threshold: amount };
    }

    const res = await fetch(url, {
      method: mode === "threshold" ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`오류: ${err.detail || res.status}`);
      return;
    }
    closeModal();
    fetchStock();
  }

  async function openLog(id, name) {
    logTitle.textContent = `${name} - 입출고 이력`;
    logBody.innerHTML = '<tr><td colspan="6">로딩 중...</td></tr>';
    logModal.hidden = false;
    const res = await fetch(`/api/materials/${id}/stock-log?limit=100`);
    if (!res.ok) {
      logBody.innerHTML = '<tr><td colspan="6">불러오기 실패</td></tr>';
      return;
    }
    const data = await res.json();
    const reasonLabels = {
      measurement: "계량 차감",
      restock: "입고",
      adjust: "조정",
      discard: "폐기",
    };
    logBody.innerHTML = (data.items || [])
      .map(
        (row) => `
        <tr>
          <td>${fmtTime(row.created_at)}</td>
          <td>${reasonLabels[row.reason] || row.reason}</td>
          <td class="num ${row.delta < 0 ? "text-negative" : "text-positive"}">${row.delta >= 0 ? "+" : ""}${fmt(row.delta)}</td>
          <td class="num">${fmt(row.balance_after)}</td>
          <td>${escapeHtml(row.actor_name || "")}</td>
          <td>${escapeHtml(row.note || "")}</td>
        </tr>`,
      )
      .join("") || '<tr><td colspan="6">이력이 없습니다.</td></tr>';
  }

  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const row = btn.closest("tr");
    const id = parseInt(row.dataset.id, 10);
    const name = row.dataset.name;
    const action = btn.dataset.action;
    if (action === "log") openLog(id, name);
    else openModal(action, id, name);
  });

  modalSubmit.addEventListener("click", submitModal);
  modalClose.addEventListener("click", closeModal);
  logClose.addEventListener("click", () => (logModal.hidden = true));

  searchInput.addEventListener("input", () => {
    state.filter = searchInput.value;
    render();
  });
  refreshBtn.addEventListener("click", fetchStock);

  document.querySelector('[data-tab="stock"]').addEventListener("click", fetchStock);

  // Initial fetch for banner (even if tab not opened)
  fetchStock();
})();
