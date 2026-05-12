/* ink_plan.js — Full INK Plan: Upload + Board + Plan List + Detail */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // ── Elements ──
  const dropZone = $("#drop-zone");
  const fileInput = $("#file-input");
  const previewSection = $("#upload-preview");
  const previewImg = $("#preview-img");
  const progressSection = $("#upload-progress");
  const btnAnalyze = $("#btn-analyze");
  const btnClear = $("#btn-clear");
  const matchSection = $("#match-section");
  const matchSummary = $("#match-summary");
  const matchTabs = $("#match-tabs");
  const matchBody = $("#match-body");
  const chemicalSection = $("#chemical-section");
  const chemicalBody = $("#chemical-body");
  const btnConfirm = $("#btn-confirm");
  const confirmStats = $("#confirm-stats");
  const boardSection = $("#board-section");
  const boardTabs = $("#board-tabs");
  const boardGrid = $("#board-grid");
  const btnBackUpload = $("#btn-back-upload");
  const plansEmpty = $("#plans-empty");
  const plansTable = $("#plans-table");
  const plansBody = $("#plans-body");
  const detailOverlay = $("#plan-detail-overlay");

  let currentFile = null;
  let currentResult = null;

  // ══════════════════════════════════════════
  // 1. FILE HANDLING
  // ══════════════════════════════════════════
  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files[0]) handleFile(e.target.files[0]);
  });
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragover"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault(); dropZone.classList.remove("dragover");
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  });
  document.addEventListener("paste", (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) { handleFile(item.getAsFile()); break; }
    }
  });

  function handleFile(file) {
    if (!file.type.startsWith("image/")) { showToast("이미지 파일만 업로드 가능합니다.", "error"); return; }
    currentFile = file;
    const reader = new FileReader();
    reader.onload = (e) => {
      previewImg.src = e.target.result;
      dropZone.hidden = true;
      previewSection.hidden = false;
      progressSection.hidden = true;
      matchSection.hidden = true;
      boardSection.hidden = true;
    };
    reader.readAsDataURL(file);
  }

  btnClear.addEventListener("click", resetUpload);
  btnBackUpload.addEventListener("click", resetUpload);

  function resetUpload() {
    currentFile = null; currentResult = null;
    fileInput.value = "";
    dropZone.hidden = false;
    previewSection.hidden = true;
    progressSection.hidden = true;
    matchSection.hidden = true;
    chemicalSection.hidden = true;
    boardSection.hidden = true;
  }

  // ══════════════════════════════════════════
  // 2. ANALYZE (OCR)
  // ══════════════════════════════════════════
  btnAnalyze.addEventListener("click", async () => {
    if (!currentFile) return;
    previewSection.hidden = true;
    progressSection.hidden = false;

    const formData = new FormData();
    formData.append("file", currentFile);
    try {
      const resp = await fetch("/api/ocr/ink-request", { method: "POST", body: formData });
      if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${resp.status}`); }
      currentResult = await resp.json();
      renderResults(currentResult);
    } catch (err) {
      showToast(`분석 실패: ${err.message}`, "error");
      previewSection.hidden = false;
    } finally {
      progressSection.hidden = true;
    }
  });

  // ══════════════════════════════════════════
  // 3. MATCH RESULTS
  // ══════════════════════════════════════════
  function renderResults(data) {
    matchSection.hidden = false;
    const s = data.match_summary || {};
    matchSummary.innerHTML = `
      <span class="badge badge-exact">✅ 매칭 ${s.exact || 0}</span>
      <span class="badge badge-fuzzy">⚠️ 후보 ${s.fuzzy || 0}</span>
      <span class="badge badge-none">❌ 미매칭 ${s.unmatched || 0}</span>
    `;

    matchTabs.innerHTML = "";
    const sheets = data.ink_requests || [];
    sheets.forEach((sheet, idx) => {
      const btn = document.createElement("button");
      btn.className = `pp-tab-btn${idx === 0 ? " active" : ""}`;
      btn.textContent = `${sheet.request_date} ${sheet.shift} (${sheet.line})`;
      btn.addEventListener("click", () => {
        $$(".pp-tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        renderMatchTable(sheets[idx]);
      });
      matchTabs.appendChild(btn);
    });
    if (sheets.length > 0) renderMatchTable(sheets[0]);

    // Chemicals
    const chems = data.chemical_requests || [];
    if (chems.length > 0) {
      chemicalSection.hidden = false;
      chemicalBody.innerHTML = chems.map(c => `
        <tr>
          <td>${esc(c.chemical_name)}</td><td>${esc(c.concentration || "")}</td>
          <td class="num">${c.qty_3f ?? "—"}</td><td class="num">${c.qty_1f ?? "—"}</td>
        </tr>`).join("");
    }
    updateConfirmStats();
  }

  function renderMatchTable(sheet) {
    const rows = sheet.rows || [];
    matchBody.innerHTML = rows.map((r, idx) => {
      const statusIcon = r.match_status === "exact" ? "✅" : r.match_status === "fuzzy" ? "⚠️" : r.match_status === "skip" ? "⏭️" : "❌";
      let matchCell;
      if (r.match_status === "exact") {
        matchCell = `<span>${esc(r.matched_product_name || "")}</span>`;
      } else if (r.match_status === "fuzzy" && r.candidates?.length) {
        const opts = r.candidates.map(c => `<option value="${esc(c.name)}" ${c.name === r.matched_product_name ? "selected" : ""}>${esc(c.name)} (${Math.round(c.score * 100)}%)</option>`).join("");
        matchCell = `<select class="pp-match-select" data-row="${idx}" data-sheet="${sheet.request_date}">${opts}<option value="">직접 입력...</option></select>`;
      } else if (r.match_status === "skip") {
        matchCell = `<span class="muted">TEST (건너뜀)</span>`;
      } else {
        matchCell = `<input type="text" class="input pp-match-select" data-row="${idx}" placeholder="제품명 입력..." value="${esc(r.matched_product_name || "")}" />`;
      }
      return `<tr>
        <td>${r.machine_no}</td><td>${esc(r.brand)}</td>
        <td title="${esc(r.ocr_product_name)}">${esc(truncate(r.ocr_product_name, 30))}</td>
        <td>${matchCell}</td><td class="status-${r.match_status}">${statusIcon}</td>
      </tr>`;
    }).join("");
  }

  function updateConfirmStats() {
    if (!currentResult) return;
    const s = currentResult.match_summary || {};
    confirmStats.textContent = `매칭 ${s.exact || 0} / 후보 ${s.fuzzy || 0} / 미매칭 ${s.unmatched || 0} — 총 ${s.total || 0}건`;
  }

  // ══════════════════════════════════════════
  // 4. CONFIRM & SHOW BOARD
  // ══════════════════════════════════════════
  btnConfirm.addEventListener("click", async () => {
    if (!currentResult) return;
    const sheets = currentResult.ink_requests || [];
    if (!sheets.length) return;

    // Gather user-edited match values from selects/inputs
    const selectEls = matchBody.querySelectorAll(".pp-match-select");
    const editMap = {};
    selectEls.forEach(el => {
      const row = el.dataset.row;
      const val = el.value;
      if (val) editMap[row] = val;
    });

    const allMatches = [];
    sheets.forEach(sheet => {
      sheet.rows.forEach((r, idx) => {
        if (r.match_status === "skip") return;
        allMatches.push({
          machine_no: r.machine_no, line_type: sheet.line, shift: sheet.shift,
          brand: r.brand, ocr_product_name: r.ocr_product_name,
          matched_product_name: editMap[String(idx)] || r.matched_product_name || r.ocr_product_name,
          match_confidence: r.match_confidence || 0,
        });
      });
    });

    const scheduleDate = sheets[0]?.request_date || new Date().toISOString().slice(0, 10);

    try {
      const resp = await fetch("/api/ocr/ink-request/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: null, schedule_date: scheduleDate, matches: allMatches }),
      });
      if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${resp.status}`); }
      const result = await resp.json();
      showToast(`✅ ${result.schedule_count}건 저장 완료 (계획 #${result.plan_id})`, "success");

      // Switch to board view
      matchSection.hidden = true;
      chemicalSection.hidden = true;
      dropZone.hidden = true;
      await showPlanBoard(result.plan_id);
      loadPlans();
    } catch (err) {
      showToast(`저장 실패: ${err.message}`, "error");
    }
  });

  // ══════════════════════════════════════════
  // 5. WEEKLY BOARD VIEW
  // ══════════════════════════════════════════
  async function showPlanBoard(planId, targetGrid, targetTabs) {
    const grid = targetGrid || boardGrid;
    const tabs = targetTabs || boardTabs;
    const section = targetGrid ? null : boardSection;

    try {
      const resp = await fetch(`/api/ocr/plans/${planId}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      if (section) section.hidden = false;

      const board = data.board || [];
      tabs.innerHTML = "";
      board.forEach((slot, idx) => {
        const btn = document.createElement("button");
        btn.className = `pp-tab-btn${idx === 0 ? " active" : ""}`;
        btn.textContent = `${slot.schedule_date} ${slot.shift || ""} (${slot.line || ""})`;
        btn.addEventListener("click", () => {
          tabs.querySelectorAll(".pp-tab-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
          renderBoardGrid(slot, grid);
        });
        tabs.appendChild(btn);
      });

      if (board.length > 0) renderBoardGrid(board[0], grid);

      // Chemicals in overlay
      if (targetGrid && data.chemicals?.length) {
        const chemPanel = $("#plan-detail-chemicals");
        const chemBody = $("#plan-detail-chem-body");
        chemPanel.hidden = false;
        chemBody.innerHTML = data.chemicals.map(c => `
          <tr><td>${esc(c.chemical_name)}</td><td>${esc(c.concentration || "")}</td>
          <td class="num">${c.qty_3f ?? "—"}</td><td class="num">${c.qty_1f ?? "—"}</td></tr>
        `).join("");
      }

      return data;
    } catch (err) {
      showToast(`보드 로딩 실패: ${err.message}`, "error");
    }
  }

  function renderBoardGrid(slot, grid) {
    const machines = slot.machines || [];
    if (!machines.length) {
      grid.innerHTML = `<p class="pp-empty">이 시프트에 등록된 스케줄이 없습니다.</p>`;
      return;
    }
    grid.innerHTML = `
      <div class="board-row board-row-header">
        <span class="board-cell board-cell-no">호기</span>
        <span class="board-cell board-cell-brand">구분</span>
        <span class="board-cell board-cell-product">제품</span>
      </div>
      ${machines.map(m => `
        <div class="board-row">
          <span class="board-cell board-cell-no">${m.machine_no}</span>
          <span class="board-cell board-cell-brand">${esc(m.brand)}</span>
          <span class="board-cell board-cell-product board-status-${m.match_status}">${esc(m.product)}</span>
        </div>
      `).join("")}
    `;
  }

  // ══════════════════════════════════════════
  // 6. SAVED PLANS LIST
  // ══════════════════════════════════════════
  async function loadPlans() {
    try {
      const resp = await fetch("/api/ocr/plans");
      if (!resp.ok) return;
      const data = await resp.json();
      const plans = data.items || [];

      if (!plans.length) {
        plansEmpty.hidden = false;
        plansEmpty.querySelector("p").textContent = "아직 저장된 계획이 없습니다. INK 요청서 이미지를 업로드하여 시작하세요.";
        plansTable.hidden = true;
        return;
      }

      plansEmpty.hidden = true;
      plansTable.hidden = false;
      plansBody.innerHTML = plans.map(p => {
        const statusLabel = { draft: "초안", active: "진행", completed: "완료" }[p.status] || p.status;
        const statusClass = { draft: "badge-fuzzy", active: "badge-exact", completed: "badge-none" }[p.status] || "";
        const created = p.created_at ? p.created_at.slice(0, 16).replace("T", " ") : "";
        return `<tr data-plan-id="${p.id}">
          <td><a href="#" class="plan-link" data-id="${p.id}">${esc(p.plan_name)}</a></td>
          <td>${p.week_start}${p.week_end !== p.week_start ? " ~ " + p.week_end : ""}</td>
          <td><span class="badge ${statusClass}">${statusLabel}</span></td>
          <td>${created}</td>
          <td>
            <button class="btn compact plan-view-btn" data-id="${p.id}">보기</button>
            <button class="btn compact plan-del-btn" data-id="${p.id}">삭제</button>
          </td>
        </tr>`;
      }).join("");

      // Event listeners
      plansBody.querySelectorAll(".plan-view-btn, .plan-link").forEach(el => {
        el.addEventListener("click", (e) => { e.preventDefault(); openPlanDetail(el.dataset.id); });
      });
      plansBody.querySelectorAll(".plan-del-btn").forEach(el => {
        el.addEventListener("click", () => deletePlan(el.dataset.id));
      });
    } catch (err) {
      plansEmpty.querySelector("p").textContent = "계획 목록 로딩 실패";
    }
  }

  // ══════════════════════════════════════════
  // 7. PLAN DETAIL OVERLAY
  // ══════════════════════════════════════════
  async function openPlanDetail(planId) {
    const title = $("#plan-detail-title");
    const grid = $("#plan-detail-board");
    const tabs = $("#plan-detail-tabs");
    const chemPanel = $("#plan-detail-chemicals");

    title.textContent = "로딩 중...";
    grid.innerHTML = "";
    tabs.innerHTML = "";
    chemPanel.hidden = true;
    detailOverlay.hidden = false;

    const data = await showPlanBoard(planId, grid, tabs);
    if (data?.plan) {
      title.textContent = `${data.plan.plan_name} (${data.plan.week_start})`;
    }
  }

  $("#btn-close-detail").addEventListener("click", () => {
    detailOverlay.hidden = true;
  });

  // ══════════════════════════════════════════
  // 8. DELETE PLAN
  // ══════════════════════════════════════════
  async function deletePlan(planId) {
    if (!confirm("이 생산계획을 삭제하시겠습니까?")) return;
    try {
      const resp = await fetch(`/api/ocr/plans/${planId}`, { method: "DELETE" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      showToast("삭제 완료", "success");
      loadPlans();
    } catch (err) {
      showToast(`삭제 실패: ${err.message}`, "error");
    }
  }

  // ══════════════════════════════════════════
  // UTILS
  // ══════════════════════════════════════════
  function esc(str) {
    if (!str) return "";
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }
  function truncate(str, len) {
    return str && str.length > len ? str.slice(0, len) + "..." : str || "";
  }
  function showToast(msg, type) {
    const root = document.getElementById("toast-root");
    if (!root) { alert(msg); return; }
    const el = document.createElement("div");
    el.className = `toast toast-${type || "info"}`;
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(() => el.remove(), 5000);
  }

  // ── Init ──
  loadPlans();
})();
