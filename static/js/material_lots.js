/**
 * material_lots.js — 자재 LOT 조회·관리 (/material-lots).
 *
 * ERP 엑셀 LOT(재고 기준) + 수동 LOT(책임자 등록) 를 한 목록으로 보여주고,
 * 책임자는 수동 LOT 추가·삭제를 한다. 검색은 클라이언트 필터.
 *
 * 데이터: GET /api/material-lots/status  (수동 LOT 행에 id 포함)
 * 쓰기 : POST /api/material-lots/manual · DELETE /api/material-lots/manual/{id}
 *         (CSRF 토큰은 IRMS._core.request 가 쿠키에서 자동 첨부)
 */
document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = IRMS.notify || ((_msg) => {});
  const $ = (id) => document.getElementById(id);

  const shell = document.querySelector(".app-shell");
  const CAN_MANAGE = shell && shell.dataset.canManage === "1";

  let lastItems = []; // 검색 필터용 원본

  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  function fmtStock(v) {
    if (v === null || v === undefined || v === "") return "-";
    return Number(v).toLocaleString("ko-KR", { maximumFractionDigits: 2 });
  }

  // 상태 셀 — valid(사용 가능) / 엑셀 재고 0(소진) / 재고 음수(재고 이상) / 수동.
  // 음수는 소진과 다르다: ERP 전표 지연·누락 신호라(실물은 돌고 있을 확률이 높다)
  // '소진'으로 뭉뚱그리면 전산 정리 대상이 눈에 안 띈다.
  function statusCell(lot) {
    if (lot.source === "manual") {
      return '<span class="mlot-status-cell mlot-status-manual">수동</span>';
    }
    if (lot.valid) {
      return '<span class="mlot-status-cell mlot-status-valid">사용 가능</span>';
    }
    if (Number(lot.stock) < 0) {
      return '<span class="mlot-status-cell mlot-status-spent" title="ERP 재고가 마이너스 — 전표 반영 지연·누락일 수 있습니다">재고 이상(−)</span>';
    }
    return '<span class="mlot-status-cell mlot-status-spent">소진</span>';
  }

  function sourceCell(lot) {
    return lot.source === "manual"
      ? '<span class="mlot-source-manual">수동</span>'
      : '<span class="mlot-source-erp">ERP</span>';
  }

  function manageCell(lot) {
    // 수동 LOT 만 삭제 가능(엑셀 LOT 는 ERP 원본이라 여기서 지울 수 없다).
    if (lot.source !== "manual" || !lot.id) return "";
    return (
      '<button class="btn btn-sm mlot-del" type="button" ' +
      `data-id="${esc(lot.id)}" data-code="${esc(lot.code)}" ` +
      `data-lot="${esc(lot.lot)}">삭제</button>`
    );
  }

  function rowHtml(item, lot) {
    return (
      "<tr>" +
      `<td>${esc(item.code)}</td>` +
      `<td>${esc(item.name)}</td>` +
      `<td>${esc(lot.lot)}</td>` +
      `<td class="num">${fmtStock(lot.stock)}</td>` +
      `<td>${statusCell(lot)}</td>` +
      `<td>${sourceCell(lot)}</td>` +
      (CAN_MANAGE ? `<td>${manageCell(lot) || "-"}</td>` : "") +
      "</tr>"
    );
  }

  function renderLots() {
    const body = $("mlot-body");
    const empty = $("mlot-empty");
    const q = ($("mlot-search").value || "").trim().toLowerCase();

    const flat = [];
    for (const item of lastItems) {
      for (const lot of item.lots || []) {
        // 수동 행에 code/id 를 실어 삭제 버튼이 쓸 수 있게 한다.
        flat.push({ item, lot: Object.assign({}, lot, { code: item.code }) });
      }
    }
    const filtered = q
      ? flat.filter(({ item, lot }) => (
          String(item.code || "").toLowerCase().includes(q) ||
          String(item.name || "").toLowerCase().includes(q) ||
          String(lot.lot || "").toLowerCase().includes(q)
        ))
      : flat;

    if (!filtered.length) {
      body.innerHTML = "";
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    body.innerHTML = filtered.map(({ item, lot }) => rowHtml(item, lot)).join("");
    Array.from(body.querySelectorAll(".mlot-del")).forEach((btn) => {
      btn.addEventListener("click", () => onDelete(btn));
    });
  }

  function renderSummary(data) {
    const fileCard = $("mlot-card-file");
    const fileDateCard = $("mlot-card-filedate");
    const staleCard = $("mlot-card-stale");
    const staleNote = $("mlot-card-stale-note");
    const countsCard = $("mlot-card-counts");

    if (!data.file_ok) {
      fileCard.textContent = "없음";
      fileDateCard.textContent = "ERP 파일을 읽을 수 없습니다";
      staleCard.textContent = "-";
      staleCard.className = "metric-value";
      staleNote.textContent = "기준 파일 없음";
      countsCard.textContent = "-";
      return;
    }
    fileCard.textContent = data.file_name || "-";
    fileDateCard.textContent = data.file_date
      ? `기준일 ${data.file_date}`
      : "파일명 날짜 없음";

    const stale = data.stale_days;
    if (stale === null || stale === undefined) {
      staleCard.textContent = "-";
      staleCard.className = "metric-value";
      staleNote.textContent = "파일명 날짜 없음";
    } else {
      staleCard.textContent = `${stale}일`;
      const warn = stale >= 2;
      staleCard.className = "metric-value " + (warn ? "mlot-stale-warn" : "mlot-stale-ok");
      staleNote.textContent = warn ? "오래됨 — 갱신 필요" : "최근";
    }

    let valid = 0;
    let manual = 0;
    for (const item of data.items || []) {
      for (const lot of item.lots || []) {
        if (lot.source === "manual") manual += 1;
        else if (lot.valid) valid += 1;
      }
    }
    countsCard.textContent = `${valid} / ${manual}`;
  }

  async function loadStatus() {
    const tablePanel = $("mlot-table-panel");
    const warnPanel = $("mlot-warn-panel");
    const warnText = $("mlot-warn-text");

    try {
      const data = await request("/material-lots/status");
      renderSummary(data);

      if (!data.file_ok) {
        tablePanel.hidden = true;
        warnPanel.hidden = false;
        warnText.textContent =
          "ERP 재고 파일을 찾을 수 없습니다. 자재 LOT 를 불러올 수 없습니다 — 파일이 올바른 위치에 있는지 확인하세요. 엑셀 문제로 현장 계량은 막지 않습니다(fail-open).";
        lastItems = [];
        return;
      }
      warnPanel.hidden = true;
      tablePanel.hidden = false;

      lastItems = data.items || [];
      renderLots();
      fillCodeCandidates();
    } catch (e) {
      tablePanel.hidden = true;
      warnPanel.hidden = false;
      warnText.textContent = `자재 LOT 조회에 실패했습니다: ${e.message}`;
      lastItems = [];
      notify(`자재 LOT 조회 실패: ${e.message}`, "error");
    }
  }

  async function onAdd(ev) {
    ev.preventDefault();
    const errEl = $("mlot-add-error");
    const btn = $("mlot-add-btn");
    errEl.hidden = true;
    const code = $("mlot-add-code").value.trim();
    const lot = $("mlot-add-lot").value.trim();
    const note = $("mlot-add-note").value.trim();
    if (!code || !lot) {
      errEl.textContent = "품목코드와 LOT 를 입력하세요.";
      errEl.hidden = false;
      return;
    }
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "추가 중…";
    try {
      await request("/material-lots/manual", {
        method: "POST",
        body: { material_code: code, lot, note: note || undefined },
      });
      notify("수동 LOT 를 추가했습니다.", "success");
      $("mlot-add-lot").value = "";
      $("mlot-add-note").value = "";
      await loadStatus();
    } catch (e) {
      errEl.textContent = e.message;
      errEl.hidden = false;
      notify(`추가 실패: ${e.message}`, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  }

  async function onDelete(btn) {
    const id = btn.dataset.id;
    const code = btn.dataset.code;
    const lot = btn.dataset.lot;
    if (!confirm(`수동 LOT 를 삭제합니다:\n${code} / ${lot}`)) return;
    try {
      await request(`/material-lots/manual/${id}`, { method: "DELETE" });
      notify("수동 LOT 를 삭제했습니다.", "success");
      await loadStatus();
    } catch (e) {
      notify(`삭제 실패: ${e.message}`, "error");
    }
  }

  function fillCodeCandidates() {
    const dl = $("mlot-code-candidates");
    if (!dl) return;
    dl.innerHTML = "";
    const seen = new Set();
    for (const item of lastItems) {
      if (!item.code || seen.has(item.code)) continue;
      seen.add(item.code);
      const o = document.createElement("option");
      o.value = item.code;
      o.textContent = item.name ? `${item.code} · ${item.name}` : item.code;
      dl.appendChild(o);
    }
  }

  // 초기 바인딩
  if ($("mlot-search")) $("mlot-search").addEventListener("input", renderLots);
  if ($("mlot-refresh")) $("mlot-refresh").addEventListener("click", loadStatus);
  const addForm = $("mlot-add-form");
  if (addForm) addForm.addEventListener("submit", onAdd);

  loadStatus();
});
