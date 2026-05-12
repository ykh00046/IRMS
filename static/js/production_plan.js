/* production_plan.js — INK Request OCR Upload + Match Confirmation */
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

  let currentFile = null;
  let currentResult = null;
  let activeTabIdx = 0;

  // ── File handling ──
  dropZone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files[0]) handleFile(e.target.files[0]);
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  });

  // Clipboard paste
  document.addEventListener("paste", (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        handleFile(item.getAsFile());
        break;
      }
    }
  });

  function handleFile(file) {
    if (!file.type.startsWith("image/")) {
      showToast("이미지 파일만 업로드할 수 있습니다.", "error");
      return;
    }
    currentFile = file;
    const reader = new FileReader();
    reader.onload = (e) => {
      previewImg.src = e.target.result;
      dropZone.hidden = true;
      previewSection.hidden = false;
      progressSection.hidden = true;
      matchSection.hidden = true;
    };
    reader.readAsDataURL(file);
  }

  btnClear.addEventListener("click", resetUpload);
  function resetUpload() {
    currentFile = null;
    currentResult = null;
    fileInput.value = "";
    dropZone.hidden = false;
    previewSection.hidden = true;
    progressSection.hidden = true;
    matchSection.hidden = true;
    chemicalSection.hidden = true;
  }

  // ── Analyze ──
  btnAnalyze.addEventListener("click", async () => {
    if (!currentFile) return;
    previewSection.hidden = true;
    progressSection.hidden = false;

    const formData = new FormData();
    formData.append("file", currentFile);

    try {
      const resp = await fetch("/api/ocr/ink-request", {
        method: "POST",
        body: formData,
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      currentResult = await resp.json();
      renderResults(currentResult);
    } catch (err) {
      showToast(`분석 실패: ${err.message}`, "error");
      previewSection.hidden = false;
    } finally {
      progressSection.hidden = true;
    }
  });

  // ── Render results ──
  function renderResults(data) {
    matchSection.hidden = false;

    // Summary badges
    const s = data.match_summary || {};
    matchSummary.innerHTML = `
      <span class="badge badge-exact">✅ 매칭 ${s.exact || 0}</span>
      <span class="badge badge-fuzzy">⚠️ 후보 ${s.fuzzy || 0}</span>
      <span class="badge badge-none">❌ 미매칭 ${s.unmatched || 0}</span>
    `;

    // Tabs for each shift/date
    matchTabs.innerHTML = "";
    const sheets = data.ink_requests || [];
    sheets.forEach((sheet, idx) => {
      const btn = document.createElement("button");
      btn.className = `pp-tab-btn${idx === 0 ? " active" : ""}`;
      btn.textContent = `${sheet.request_date} ${sheet.shift} (${sheet.line})`;
      btn.addEventListener("click", () => {
        $$(".pp-tab-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        activeTabIdx = idx;
        renderMatchTable(sheets[idx]);
      });
      matchTabs.appendChild(btn);
    });

    if (sheets.length > 0) renderMatchTable(sheets[0]);

    // Chemicals
    const chems = data.chemical_requests || [];
    if (chems.length > 0) {
      chemicalSection.hidden = false;
      chemicalBody.innerHTML = chems
        .map(
          (c) => `
        <tr>
          <td>${esc(c.chemical_name)}</td>
          <td>${esc(c.concentration || "")}</td>
          <td class="num">${c.qty_3f ?? "—"}</td>
          <td class="num">${c.qty_1f ?? "—"}</td>
        </tr>`
        )
        .join("");
    }

    updateConfirmStats();
  }

  function renderMatchTable(sheet) {
    const rows = sheet.rows || [];
    matchBody.innerHTML = rows
      .map((r, idx) => {
        const statusIcon =
          r.match_status === "exact" ? "✅" :
          r.match_status === "fuzzy" ? "⚠️" :
          r.match_status === "skip" ? "⏭️" : "❌";
        const statusClass = `status-${r.match_status}`;

        let matchCell;
        if (r.match_status === "exact") {
          matchCell = `<span>${esc(r.matched_product_name || "")}</span>`;
        } else if (r.match_status === "fuzzy" && r.candidates?.length) {
          const opts = r.candidates
            .map((c) => `<option value="${esc(c.name)}" ${c.name === r.matched_product_name ? "selected" : ""}>${esc(c.name)} (${Math.round(c.score * 100)}%)</option>`)
            .join("");
          matchCell = `<select class="pp-match-select" data-row="${idx}">${opts}<option value="">직접 입력...</option></select>`;
        } else if (r.match_status === "skip") {
          matchCell = `<span class="muted">TEST (건너뜀)</span>`;
        } else {
          matchCell = `<input type="text" class="input pp-match-select" data-row="${idx}" placeholder="제품명 입력..." value="${esc(r.matched_product_name || "")}" />`;
        }

        return `
          <tr>
            <td>${r.machine_no}</td>
            <td>${esc(r.brand)}</td>
            <td title="${esc(r.ocr_product_name)}">${esc(truncate(r.ocr_product_name, 35))}</td>
            <td>${matchCell}</td>
            <td class="${statusClass}">${statusIcon}</td>
          </tr>`;
      })
      .join("");
  }

  // ── Confirm ──
  btnConfirm.addEventListener("click", async () => {
    if (!currentResult) return;
    const sheets = currentResult.ink_requests || [];
    if (!sheets.length) return;

    const allMatches = [];
    sheets.forEach((sheet) => {
      sheet.rows.forEach((r) => {
        if (r.match_status === "skip") return;
        allMatches.push({
          machine_no: r.machine_no,
          line_type: sheet.line,
          shift: sheet.shift,
          brand: r.brand,
          ocr_product_name: r.ocr_product_name,
          matched_product_name: r.matched_product_name || r.ocr_product_name,
          match_confidence: r.match_confidence || 0,
        });
      });
    });

    const scheduleDate = sheets[0]?.request_date || new Date().toISOString().slice(0, 10);

    try {
      const resp = await fetch("/api/ocr/ink-request/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan_id: null,
          schedule_date: scheduleDate,
          matches: allMatches,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const result = await resp.json();
      showToast(`✅ ${result.schedule_count}건 저장 완료 (계획 #${result.plan_id})`, "success");
      resetUpload();
    } catch (err) {
      showToast(`저장 실패: ${err.message}`, "error");
    }
  });

  function updateConfirmStats() {
    if (!currentResult) return;
    const s = currentResult.match_summary || {};
    confirmStats.textContent = `매칭 ${s.exact || 0} / 후보 ${s.fuzzy || 0} / 미매칭 ${s.unmatched || 0} — 총 ${s.total || 0}건`;
  }

  // ── Utils ──
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
})();
