(() => {
  const tbody = document.getElementById("forecast-body");
  if (!tbody) return;

  const banner = document.getElementById("forecast-banner");
  const windowSel = document.getElementById("forecast-window");
  const onlyReorder = document.getElementById("forecast-only-reorder");
  const refreshBtn = document.getElementById("forecast-refresh");
  const exportBtn = document.getElementById("forecast-export");

  const modal = document.getElementById("forecast-modal");
  const modalMaterial = document.getElementById("forecast-modal-material");
  const modalLead = document.getElementById("forecast-modal-lead");
  const modalCycle = document.getElementById("forecast-modal-cycle");
  const modalSubmit = document.getElementById("forecast-modal-submit");
  const modalClose = document.getElementById("forecast-modal-close");

  const fmt = (n) => {
    if (n === null || n === undefined) return "-";
    return Number(n).toLocaleString("ko-KR", { maximumFractionDigits: 3 });
  };

  // Management 화면에는 IRMS.request(공통 CSRF 래퍼)가 로드되지 않으므로
  // admin_users.js 와 동일하게 csrftoken 쿠키를 읽어 x-csrftoken 헤더로 전송한다.
  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  const STATUS_LABEL = {
    urgent: "긴급",
    soon: "임박",
    ok: "정상",
    no_data: "데이터없음",
  };
  // urgent → 음수행 강조, soon → 부족행 강조 (기존 재고 클래스 재사용)
  const ROW_CLASS = { urgent: "row-negative", soon: "row-low" };

  const state = { items: [], modalMaterialId: null };

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function fetchForecast() {
    const w = windowSel.value || "30";
    const res = await fetch(`/api/forecast/materials?window_days=${w}`);
    if (!res.ok) return;
    const data = await res.json();
    state.items = data.items || [];
    render(data.summary || {});
  }

  function render(summary) {
    const onlyR = onlyReorder.checked;
    const rows = onlyR
      ? state.items.filter((it) => it.status === "urgent" || it.status === "soon")
      : state.items;

    tbody.innerHTML =
      rows
        .map((it) => {
          const rowClass = ROW_CLASS[it.status] || "";
          return `
          <tr class="${rowClass}" data-id="${it.material_id}"
              data-name="${escapeHtml(it.name)}"
              data-lead="${it.lead_time_days}" data-cycle="${it.reorder_cycle_days}">
            <td>${escapeHtml(it.name)}</td>
            <td>${escapeHtml(it.category || "")}</td>
            <td class="num">${fmt(it.stock_quantity)}</td>
            <td class="num">${fmt(it.avg_daily)}</td>
            <td>${escapeHtml(it.predicted_stockout_date || "-")}</td>
            <td class="num">${it.days_remaining === null ? "-" : fmt(it.days_remaining)}</td>
            <td class="num">${fmt(it.recommended_order_qty)}</td>
            <td><span class="stock-status stock-${it.status === "urgent" ? "negative" : it.status === "soon" ? "low" : "ok"}">${STATUS_LABEL[it.status] || it.status}</span></td>
            <td><button class="btn btn-sm" data-action="params">설정</button></td>
          </tr>`;
        })
        .join("") || '<tr><td colspan="9">표시할 자재가 없습니다.</td></tr>';

    const reorder = summary.reorder_recommended || 0;
    if (reorder > 0) {
      banner.textContent = `⚠ 발주 권장 ${reorder}건 (긴급 ${summary.urgent || 0}, 임박 ${summary.soon || 0})`;
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }
  }

  function openModal(id, name, lead, cycle) {
    state.modalMaterialId = id;
    modalMaterial.textContent = name;
    modalLead.value = lead || 0;
    modalCycle.value = cycle || 0;
    modal.hidden = false;
    modalLead.focus();
  }

  function closeModal() {
    modal.hidden = true;
    state.modalMaterialId = null;
  }

  async function submitModal() {
    const id = state.modalMaterialId;
    const lead = parseFloat(modalLead.value);
    const cycle = parseFloat(modalCycle.value);
    if (isNaN(lead) || isNaN(cycle) || lead < 0 || cycle < 0) {
      IRMS.notify("0 이상의 숫자를 입력해주세요.", "error");
      return;
    }
    const res = await fetch(`/api/materials/${id}/forecast-params`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
      body: JSON.stringify({ lead_time_days: lead, reorder_cycle_days: cycle }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      IRMS.notify(`오류: ${err.detail || res.status}`, "error");
      return;
    }
    closeModal();
    fetchForecast();
  }

  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const row = btn.closest("tr");
    openModal(
      parseInt(row.dataset.id, 10),
      row.dataset.name,
      parseFloat(row.dataset.lead),
      parseFloat(row.dataset.cycle),
    );
  });

  modalSubmit.addEventListener("click", submitModal);
  modalClose.addEventListener("click", closeModal);
  refreshBtn.addEventListener("click", fetchForecast);
  windowSel.addEventListener("change", fetchForecast);
  onlyReorder.addEventListener("change", () => render({
    reorder_recommended: state.items.filter((it) => it.status === "urgent" || it.status === "soon").length,
    urgent: state.items.filter((it) => it.status === "urgent").length,
    soon: state.items.filter((it) => it.status === "soon").length,
  }));

  exportBtn.addEventListener("click", () => {
    const w = windowSel.value || "30";
    const only = onlyReorder.checked ? "&only_reorder=true" : "";
    window.location = `/api/forecast/export?window_days=${w}${only}`;
  });

  const tabBtn = document.querySelector('[data-tab="forecast"]');
  if (tabBtn) tabBtn.addEventListener("click", fetchForecast);

  fetchForecast();
})();
