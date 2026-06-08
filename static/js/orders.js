(() => {
  const tbody = document.getElementById("orders-body");
  if (!tbody) return;

  const windowSel = document.getElementById("orders-window");
  const createBtn = document.getElementById("orders-create");
  const refreshBtn = document.getElementById("orders-refresh");

  const modal = document.getElementById("order-modal");
  const modalTitle = document.getElementById("order-modal-title");
  const modalMeta = document.getElementById("order-modal-meta");
  const modalItems = document.getElementById("order-modal-items");
  const modalNote = document.getElementById("order-modal-note");
  const saveBtn = document.getElementById("order-modal-save");
  const excelBtn = document.getElementById("order-modal-excel");
  const printBtn = document.getElementById("order-modal-print");
  const sendBtn = document.getElementById("order-modal-send");
  const receiveBtn = document.getElementById("order-modal-receive");
  const cancelBtn = document.getElementById("order-modal-cancel");
  const closeBtn = document.getElementById("order-modal-close");
  const receiptsWrap = document.getElementById("order-receipts-wrap");
  const receiptsBody = document.getElementById("order-receipts");

  const receiveModal = document.getElementById("receive-modal");
  const receiveItems = document.getElementById("receive-modal-items");
  const receiveNote = document.getElementById("receive-modal-note");
  const receiveConfirm = document.getElementById("receive-modal-confirm");
  const receiveCloseBtn = document.getElementById("receive-modal-close");
  const receiveCancelBtn = document.getElementById("receive-modal-cancel");

  const STATUS_LABEL = { draft: "작성중", sent: "전송됨", failed: "실패", cancelled: "취소" };
  const RECEIPT_LABEL = { pending: "미입고", partial: "부분입고", received: "입고완료" };
  const RECEIPT_CLASS = { pending: "low", partial: "low", received: "ok" };
  const URGENCY_LABEL = { urgent: "긴급", soon: "임박" };

  const state = { current: null };

  const fmt = (n) => {
    if (n === null || n === undefined) return "-";
    return Number(n).toLocaleString("ko-KR", { maximumFractionDigits: 3 });
  };

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Management 화면에는 IRMS.request(공통 CSRF 래퍼)가 로드되지 않으므로
  // forecast.js / admin_users.js 와 동일하게 csrftoken 쿠키를 헤더로 전송한다.
  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    if (method !== "GET") opts.headers["x-csrftoken"] = csrfToken();
    const res = await fetch(url, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `오류 ${res.status}`);
    }
    return res.status === 204 ? null : res.json();
  }

  async function fetchOrders() {
    try {
      const data = await api("GET", "/api/orders");
      renderList(data.orders || []);
    } catch (e) {
      IRMS.notify(`발주서 목록을 불러오지 못했습니다: ${e.message}`, "error");
    }
  }

  function renderList(orders) {
    tbody.innerHTML =
      orders
        .map(
          (o) => `
        <tr data-id="${o.id}">
          <td>${escapeHtml(o.order_no)}</td>
          <td>${escapeHtml((o.created_at || "").slice(0, 10))}</td>
          <td class="num">${o.item_count}</td>
          <td class="num">${fmt(o.total_qty)}</td>
          <td><span class="stock-status stock-${o.status === "failed" ? "negative" : o.status === "sent" ? "ok" : "low"}">${STATUS_LABEL[o.status] || o.status}</span></td>
          <td><span class="stock-status stock-${RECEIPT_CLASS[o.receipt_status] || "low"}">${RECEIPT_LABEL[o.receipt_status] || "미입고"}</span></td>
          <td>${escapeHtml(o.created_by)}</td>
          <td><button class="btn btn-sm" data-action="open">상세</button></td>
        </tr>`,
        )
        .join("") || '<tr><td colspan="8">발주서가 없습니다.</td></tr>';
  }

  async function createOrder() {
    const w = parseInt(windowSel.value || "30", 10);
    try {
      const order = await api("POST", "/api/orders", { window_days: w });
      IRMS.notify(`발주서 ${order.order_no} 생성됨`, "success");
      await fetchOrders();
      openModal(order.id);
    } catch (e) {
      IRMS.notify(e.message, "error");
    }
  }

  async function openModal(id) {
    try {
      const order = await api("GET", `/api/orders/${id}`);
      state.current = order;
      renderModal(order);
      modal.hidden = false;
    } catch (e) {
      IRMS.notify(e.message, "error");
    }
  }

  function renderModal(order) {
    const editable = order.status === "draft";
    modalTitle.textContent = `발주서 ${order.order_no} (${STATUS_LABEL[order.status] || order.status})`;
    modalMeta.textContent = `작성일 ${(order.created_at || "").slice(0, 10)} · 작성자 ${order.created_by} · 분석기간 ${order.window_days}일`;
    modalNote.value = order.note || "";
    modalNote.disabled = !editable;

    modalItems.innerHTML = order.items
      .map(
        (it) => `
        <tr data-item-id="${it.id}">
          <td>${escapeHtml(it.material_name)}</td>
          <td class="num">${fmt(it.recommended_qty)}</td>
          <td class="num">
            <input type="number" class="input order-qty" step="0.1" min="0"
                   value="${it.order_qty}" ${editable ? "" : "disabled"}
                   style="width:90px;text-align:right;" />
          </td>
          <td>${escapeHtml(it.predicted_stockout_date || "-")}</td>
          <td>${URGENCY_LABEL[it.urgency_status] || escapeHtml(it.urgency_status || "")}</td>
          <td><input type="text" class="input order-note" value="${escapeHtml(it.note || "")}"
                     ${editable ? "" : "disabled"} style="width:120px;" /></td>
        </tr>`,
      )
      .join("");

    saveBtn.disabled = !editable;
    sendBtn.disabled = !(order.status === "draft" || order.status === "failed");
    cancelBtn.disabled = !(order.status === "draft" || order.status === "failed");
    // 입고는 ERP 전송(sent)된 발주서에만 노출
    receiveBtn.hidden = order.status !== "sent";
    if (order.status === "sent") {
      loadReceipts(order.id);
    } else {
      receiptsWrap.hidden = true;
    }
  }

  async function loadReceipts(orderId) {
    try {
      const data = await api("GET", `/api/orders/${orderId}/receipts`);
      const receipts = data.receipts || [];
      if (!receipts.length) {
        receiptsWrap.hidden = true;
        return;
      }
      receiptsBody.innerHTML = receipts
        .map(
          (r) => `
          <tr>
            <td>${escapeHtml(r.receipt_no)}</td>
            <td>${escapeHtml((r.received_at || "").slice(0, 10))}</td>
            <td class="num">${r.item_count}</td>
            <td class="num">${fmt(r.total_qty)}</td>
            <td>${escapeHtml(r.received_by)}</td>
          </tr>`,
        )
        .join("");
      receiptsWrap.hidden = false;
    } catch (e) {
      receiptsWrap.hidden = true;
    }
  }

  function closeModal() {
    modal.hidden = true;
    state.current = null;
  }

  function collectItems() {
    return Array.from(modalItems.querySelectorAll("tr")).map((tr) => ({
      id: parseInt(tr.dataset.itemId, 10),
      order_qty: parseFloat(tr.querySelector(".order-qty").value) || 0,
      note: tr.querySelector(".order-note").value || null,
    }));
  }

  async function saveOrder() {
    if (!state.current) return;
    try {
      const order = await api("PATCH", `/api/orders/${state.current.id}`, {
        note: modalNote.value,
        items: collectItems(),
      });
      state.current = order;
      renderModal(order);
      await fetchOrders();
      IRMS.notify("저장되었습니다.", "success");
    } catch (e) {
      IRMS.notify(e.message, "error");
    }
  }

  async function sendOrder() {
    if (!state.current) return;
    if (!confirm("이 발주서를 ERP로 전송할까요? 전송 후에는 수정할 수 없습니다.")) return;
    try {
      const res = await api("POST", `/api/orders/${state.current.id}/send`);
      IRMS.notify(`전송 완료 (${res.erp_mode}, 코드 ${res.erp_status_code})`, "success");
      await fetchOrders();
      await openModal(state.current.id);
    } catch (e) {
      IRMS.notify(e.message, "error");
      await fetchOrders();
    }
  }

  async function cancelOrder() {
    if (!state.current) return;
    if (!confirm("이 발주서를 취소할까요?")) return;
    try {
      await api("POST", `/api/orders/${state.current.id}/cancel`);
      IRMS.notify("발주서가 취소되었습니다.", "success");
      closeModal();
      await fetchOrders();
    } catch (e) {
      IRMS.notify(e.message, "error");
    }
  }

  function openReceiveModal() {
    if (!state.current) return;
    const order = state.current;
    receiveItems.innerHTML = order.items
      .filter((it) => (it.order_qty || 0) > 0)
      .map((it) => {
        const remaining = Math.max(0, (it.order_qty || 0) - (it.received_qty || 0));
        return `
        <tr data-item-id="${it.id}">
          <td>${escapeHtml(it.material_name)}</td>
          <td class="num">${fmt(it.order_qty)}</td>
          <td class="num">${fmt(it.received_qty || 0)}</td>
          <td class="num">${fmt(remaining)}</td>
          <td class="num">
            <input type="number" class="input receive-qty" step="0.1" min="0"
                   value="${remaining || ""}" style="width:90px;text-align:right;" />
          </td>
          <td><input type="text" class="input receive-lot" maxlength="100" style="width:110px;" /></td>
          <td><input type="date" class="input receive-expiry" style="width:140px;" /></td>
        </tr>`;
      })
      .join("");
    receiveNote.value = "";
    receiveModal.hidden = false;
  }

  function closeReceiveModal() {
    receiveModal.hidden = true;
  }

  function collectReceiveLines() {
    return Array.from(receiveItems.querySelectorAll("tr"))
      .map((tr) => ({
        order_item_id: parseInt(tr.dataset.itemId, 10),
        received_qty: parseFloat(tr.querySelector(".receive-qty").value) || 0,
        lot_no: tr.querySelector(".receive-lot").value || null,
        expiry_date: tr.querySelector(".receive-expiry").value || null,
      }))
      .filter((l) => l.received_qty > 0);
  }

  async function confirmReceive() {
    if (!state.current) return;
    const lines = collectReceiveLines();
    if (!lines.length) {
      IRMS.notify("입고수량을 입력하세요.", "error");
      return;
    }
    try {
      const res = await api("POST", `/api/orders/${state.current.id}/receipts`, {
        note: receiveNote.value || null,
        lines,
      });
      IRMS.notify(
        `입고 완료 ${res.receipt_no} (${RECEIPT_LABEL[res.receipt_status] || res.receipt_status})`,
        "success",
      );
      closeReceiveModal();
      await fetchOrders();
      await openModal(state.current.id);
    } catch (e) {
      IRMS.notify(e.message, "error");
    }
  }

  tbody.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action='open']");
    if (!btn) return;
    openModal(parseInt(btn.closest("tr").dataset.id, 10));
  });

  createBtn.addEventListener("click", createOrder);
  refreshBtn.addEventListener("click", fetchOrders);
  saveBtn.addEventListener("click", saveOrder);
  sendBtn.addEventListener("click", sendOrder);
  receiveBtn.addEventListener("click", openReceiveModal);
  cancelBtn.addEventListener("click", cancelOrder);
  closeBtn.addEventListener("click", closeModal);
  receiveConfirm.addEventListener("click", confirmReceive);
  receiveCloseBtn.addEventListener("click", closeReceiveModal);
  receiveCancelBtn.addEventListener("click", closeReceiveModal);
  excelBtn.addEventListener("click", () => {
    if (state.current) window.open(`/api/orders/${state.current.id}/export.xlsx`, "_blank");
  });
  printBtn.addEventListener("click", () => {
    if (state.current) window.open(`/api/orders/${state.current.id}/print`, "_blank");
  });

  const tabBtn = document.querySelector('[data-tab="orders"]');
  if (tabBtn) tabBtn.addEventListener("click", fetchOrders);
})();
