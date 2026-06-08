/**
 * lot.js — /management "유통기한·LOT" 탭 컨트롤러.
 *
 * stock.js 와 동형의 자체 완결 IIFE. 쓰기 시 csrftoken 쿠키를 읽어
 * x-csrftoken 헤더로 부착한다(forecast.js/admin_users.js 패턴, IRMS.request 미로드).
 *
 * Design: docs/02-design/features/lot-expiry-tracking.design.md §5.2
 */
(() => {
  const tbody = document.getElementById("lot-body");
  if (!tbody) return;

  const banner = document.getElementById("lot-banner");
  const searchInput = document.getElementById("lot-search");
  const includeInactive = document.getElementById("lot-include-inactive");
  const refreshBtn = document.getElementById("lot-refresh");
  const registerOpenBtn = document.getElementById("lot-register-open");

  const modal = document.getElementById("lot-modal");
  const modalClose = document.getElementById("lot-modal-close");
  const modalSubmit = document.getElementById("lot-modal-submit");
  const mMaterial = document.getElementById("lot-modal-material");
  const mLotNo = document.getElementById("lot-modal-lotno");
  const mQty = document.getElementById("lot-modal-qty");
  const mReceived = document.getElementById("lot-modal-received");
  const mExpiry = document.getElementById("lot-modal-expiry");
  const mNote = document.getElementById("lot-modal-note");

  const actionModal = document.getElementById("lot-action-modal");
  const actionTitle = document.getElementById("lot-action-title");
  const actionMaterial = document.getElementById("lot-action-material");
  const actionAmountGroup = document.getElementById("lot-action-amount-group");
  const actionAmount = document.getElementById("lot-action-amount");
  const actionNote = document.getElementById("lot-action-note");
  const actionSubmit = document.getElementById("lot-action-submit");
  const actionClose = document.getElementById("lot-action-close");

  const state = { items: [], filter: "", action: null, lotId: null };

  const STATE_LABEL = {
    expired: "만료",
    expiring_soon: "임박",
    ok: "정상",
    no_expiry: "무기한",
  };
  const STATE_CLASS = {
    expired: "stock-negative",
    expiring_soon: "stock-low",
    ok: "stock-ok",
    no_expiry: "", // 무기한은 중립 표시(설계 §5.2)
  };
  const STATUS_LABEL = { active: "유효", depleted: "소진", discarded: "폐기" };

  const fmt = (n) =>
    Number(n || 0).toLocaleString("ko-KR", { maximumFractionDigits: 3 });

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function todayIso() {
    const d = new Date();
    const off = d.getTimezoneOffset();
    return new Date(d.getTime() - off * 60000).toISOString().slice(0, 10);
  }

  function dday(n) {
    if (n === null || n === undefined) return "-";
    if (n < 0) return `D+${-n}`;
    if (n === 0) return "D-day";
    return `D-${n}`;
  }

  async function fetchLots() {
    const inactive = includeInactive && includeInactive.checked ? "&include_inactive=true" : "";
    const res = await fetch(`/api/materials/lots?_=1${inactive}`);
    if (!res.ok) return;
    const data = await res.json();
    state.items = data.items || [];
    render();
  }

  function render() {
    const q = state.filter.trim().toLowerCase();
    const filtered = q
      ? state.items.filter(
          (it) =>
            (it.material_name || "").toLowerCase().includes(q) ||
            (it.lot_no || "").toLowerCase().includes(q),
        )
      : state.items;

    tbody.innerHTML =
      filtered
        .map((it) => {
          const st = it.expiry_state;
          const rowClass =
            st === "expired" ? "row-negative" : st === "expiring_soon" ? "row-low" : "";
          const canAct = it.status === "active";
          const actions = canAct
            ? `<button class="btn btn-sm" data-action="consume">소진</button>
               <button class="btn btn-sm" data-action="discard">폐기</button>`
            : "";
          return `
            <tr class="${rowClass}" data-id="${it.id}" data-name="${escapeHtml(it.material_name)}">
              <td>${escapeHtml(it.material_name)}</td>
              <td>${escapeHtml(it.lot_no || "-")}</td>
              <td class="num">${fmt(it.remaining_quantity)}</td>
              <td>${escapeHtml(it.received_at || "-")}</td>
              <td>${escapeHtml(it.expiry_date || "무기한")}</td>
              <td class="num">${dday(it.days_until)}</td>
              <td>
                <span class="stock-status ${STATE_CLASS[st] || ""}">${STATE_LABEL[st] || st}</span>
                ${it.status !== "active" ? `<span class="muted small">(${STATUS_LABEL[it.status] || it.status})</span>` : ""}
              </td>
              <td>${actions}</td>
            </tr>`;
        })
        .join("") || '<tr><td colspan="8" class="muted">LOT이 없습니다.</td></tr>';

    const active = state.items.filter((it) => it.status === "active");
    const expired = active.filter((it) => it.expiry_state === "expired").length;
    const soon = active.filter((it) => it.expiry_state === "expiring_soon").length;
    if (expired || soon) {
      const parts = [];
      if (expired) parts.push(`만료 ${expired}건`);
      if (soon) parts.push(`임박 ${soon}건`);
      banner.textContent = `⚠ 유통기한 주의: ${parts.join(", ")}`;
      banner.hidden = false;
    } else {
      banner.hidden = true;
    }
  }

  async function loadMaterialOptions() {
    if (mMaterial.options.length > 0) return;
    const res = await fetch("/api/materials/stock");
    if (!res.ok) return;
    const data = await res.json();
    mMaterial.innerHTML = (data.items || [])
      .map((m) => `<option value="${m.id}">${escapeHtml(m.name)}</option>`)
      .join("");
  }

  async function openRegister() {
    await loadMaterialOptions();
    mLotNo.value = "";
    mQty.value = "";
    mReceived.value = todayIso();
    mExpiry.value = "";
    mNote.value = "";
    modal.hidden = false;
    mQty.focus();
  }

  async function submitRegister() {
    const materialId = parseInt(mMaterial.value, 10);
    const quantity = parseFloat(mQty.value);
    if (!materialId) {
      IRMS.notify("자재를 선택해주세요.", "error");
      return;
    }
    if (isNaN(quantity) || quantity <= 0) {
      IRMS.notify("입고 수량을 올바르게 입력해주세요.", "error");
      return;
    }
    const body = {
      lot_no: mLotNo.value.trim() || null,
      quantity,
      received_at: mReceived.value || null,
      expiry_date: mExpiry.value || null,
      note: mNote.value.trim() || null,
    };
    const res = await fetch(`/api/materials/${materialId}/lots`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      IRMS.notify(`오류: ${err.detail || res.status}`, "error");
      return;
    }
    modal.hidden = true;
    IRMS.notify("LOT이 등록되었습니다.", "success");
    fetchLots();
  }

  function openAction(mode, id, name) {
    state.action = mode;
    state.lotId = id;
    actionTitle.textContent = mode === "consume" ? "소진 처리" : "폐기 처리";
    actionMaterial.textContent = name;
    actionAmount.value = "";
    actionNote.value = "";
    actionAmountGroup.style.display = mode === "consume" ? "" : "none";
    actionModal.hidden = false;
    if (mode === "consume") actionAmount.focus();
    else actionNote.focus();
  }

  async function submitAction() {
    const id = state.lotId;
    const mode = state.action;
    let url, body;
    if (mode === "consume") {
      const amount = parseFloat(actionAmount.value);
      if (isNaN(amount) || amount <= 0) {
        IRMS.notify("소진량을 올바르게 입력해주세요.", "error");
        return;
      }
      url = `/api/lots/${id}/consume`;
      body = { amount, note: actionNote.value.trim() || null };
    } else {
      const note = actionNote.value.trim();
      if (!note) {
        IRMS.notify("폐기 사유를 입력해주세요.", "error");
        return;
      }
      url = `/api/lots/${id}/discard`;
      body = { note };
    }
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      IRMS.notify(`오류: ${err.detail || res.status}`, "error");
      return;
    }
    actionModal.hidden = true;
    fetchLots();
  }

  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    const row = btn.closest("tr");
    openAction(btn.dataset.action, parseInt(row.dataset.id, 10), row.dataset.name);
  });

  registerOpenBtn.addEventListener("click", openRegister);
  modalSubmit.addEventListener("click", submitRegister);
  modalClose.addEventListener("click", () => (modal.hidden = true));
  actionSubmit.addEventListener("click", submitAction);
  actionClose.addEventListener("click", () => (actionModal.hidden = true));
  refreshBtn.addEventListener("click", fetchLots);
  if (includeInactive) includeInactive.addEventListener("change", fetchLots);
  searchInput.addEventListener("input", () => {
    state.filter = searchInput.value;
    render();
  });

  const tabBtn = document.querySelector('[data-tab="lots"]');
  if (tabBtn) tabBtn.addEventListener("click", fetchLots);

  // 배너용 초기 1회 로드(탭 미진입에도 만료 경고 표시)
  fetchLots();
})();
