/**
 * blend.js — 배합 실적 컨트롤러.
 *
 * 입력: 레시피 선택 → 총 배합량 → 비율/이론량 자동 → 실제량·자재LOT 입력 → 저장
 * 조회: 기간/작업자/검색 필터 → 목록 → 상세(DHR) → 인쇄/취소
 * CSRF·인증 리다이렉트는 IRMS._core.request 가 처리. 무로그인 개방 화면.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = IRMS.notify || function (m) { console.log(m); };
  const $ = (id) => document.getElementById(id);

  const state = { recipes: [], current: null, items: [], detailId: null, viscProducts: [], lotMap: {}, workers: [] };

  async function loadWorkerNames() {
    try {
      const data = await request("/workers");
      state.workers = (data.items || []).map((w) => w.name);
      const dl = $("worker-names");
      if (dl) dl.innerHTML = state.workers.map((n) => `<option value="${n}"></option>`).join("");
    } catch (_e) { /* optional */ }
  }

  // 처음 보는 이름이면 등록 확인. 등록 거부 시 false 반환(저장 중단).
  async function ensureWorker(name) {
    const clean = (name || "").trim();
    if (!clean) return false;
    if (state.workers.includes(clean)) return true;
    if (!window.confirm(`처음 보는 이름입니다: "${clean}"\n작업자로 등록할까요?`)) return false;
    try {
      await request("/workers", { method: "POST", body: { name: clean } });
      state.workers.push(clean);
      const dl = $("worker-names");
      if (dl) dl.insertAdjacentHTML("beforeend", `<option value="${clean}"></option>`);
      return true;
    } catch (e) { notify(`작업자 등록 실패: ${e.message}`, "error"); return false; }
  }

  // ── 전자서명 패드 (마우스/터치로 직접 그림) ──────────────────
  function attachSignaturePad(canvas) {
    if (!canvas || canvas._padAttached) return canvas && canvas._pad;
    const ctx2 = canvas.getContext("2d");
    ctx2.lineWidth = 2; ctx2.lineCap = "round"; ctx2.strokeStyle = "#111";
    let drawing = false, dirty = false;
    const pos = (e) => {
      const r = canvas.getBoundingClientRect();
      const t = e.touches ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    };
    const start = (e) => { drawing = true; const p = pos(e); ctx2.beginPath(); ctx2.moveTo(p.x, p.y); e.preventDefault(); };
    const move = (e) => { if (!drawing) return; const p = pos(e); ctx2.lineTo(p.x, p.y); ctx2.stroke(); dirty = true; e.preventDefault(); };
    const end = () => { drawing = false; };
    canvas.addEventListener("mousedown", start); canvas.addEventListener("mousemove", move);
    window.addEventListener("mouseup", end);
    canvas.addEventListener("touchstart", start); canvas.addEventListener("touchmove", move);
    canvas.addEventListener("touchend", end);
    const pad = {
      clear() { ctx2.clearRect(0, 0, canvas.width, canvas.height); dirty = false; },
      isEmpty() { return !dirty; },
      dataUrl() { return dirty ? canvas.toDataURL("image/png") : null; },
    };
    canvas._padAttached = true; canvas._pad = pad;
    return pad;
  }

  function fmt(v, d) {
    if (v === null || v === undefined || v === "") return "-";
    return Number(v).toFixed(d === undefined ? 1 : d);
  }
  function todayISO() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  function nowTime() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  // ── 모드 전환 ──────────────────────────────────────────────
  function setMode(mode) {
    // /blend = 배합 입력, /blend/bulk = 일괄 생성. 기록 조회는 '배합 기록'(/status) 메뉴로 일원화.
    $("blend-entry-mode").hidden = mode !== "entry";
    $("blend-bulk-mode").hidden = mode !== "bulk";
    if (mode === "bulk") initBulk();
  }

  // ── 일괄 생성 ──────────────────────────────────────────────
  function initBulk() {
    fillBulkRecipes();
    if (!$("bulk-body").children.length) addBulkRow();
    const dhrToggle = $("bulk-dhr");
    if (dhrToggle && !dhrToggle._bound) {
      dhrToggle._bound = true;
      dhrToggle.addEventListener("change", fillBulkRecipes);
    }
  }

  async function fillBulkRecipes() {
    // DHR 전용 체크 시 DHR 전용 레시피, 아니면 일반 레시피 — 배합일지 2종류 소스 분리.
    const sel = $("bulk-recipe");
    const dhr = $("bulk-dhr") && $("bulk-dhr").checked;
    try {
      const d = await request(`/blend/recipes${dhr ? "?dhr=1" : ""}`);
      const items = d.items || [];
      sel.innerHTML = "";
      const ph = document.createElement("option");
      ph.value = "";
      ph.textContent = items.length ? "레시피 선택…" : (dhr ? "DHR 전용 레시피가 없습니다" : "레시피가 없습니다");
      sel.appendChild(ph);
      items.forEach((r) => {
        const o = document.createElement("option");
        o.value = String(r.id);
        o.textContent = r.product_name;
        sel.appendChild(o);
      });
    } catch (e) {
      sel.innerHTML = '<option value="">로드 실패</option>';
    }
  }

  function addBulkRow() {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td><input class="input bulk-date" type="date" value="${todayISO()}" /></td>` +
      `<td class="num"><input class="input bulk-total" type="number" step="0.1" min="0" /></td>` +
      `<td><button class="btn btn-sm bulk-del" type="button">삭제</button></td>`;
    tr.querySelector(".bulk-del").addEventListener("click", () => tr.remove());
    $("bulk-body").appendChild(tr);
  }

  async function createBulk() {
    const err = $("bulk-error");
    err.hidden = true;
    const recipe_id = Number($("bulk-recipe").value);
    const worker = $("bulk-worker").value.trim();
    if (!recipe_id) { err.textContent = "레시피를 선택하세요."; err.hidden = false; return; }
    if (!worker) { err.textContent = "작업자를 입력하세요."; err.hidden = false; return; }
    if (!(await ensureWorker(worker))) return;
    const entries = [];
    $("bulk-body").querySelectorAll("tr").forEach((tr) => {
      const d = tr.querySelector(".bulk-date").value;
      const t = Number(tr.querySelector(".bulk-total").value);
      if (d && t > 0) entries.push({ work_date: d, total_amount: t });
    });
    if (!entries.length) { err.textContent = "유효한 작업일·총량 행을 입력하세요."; err.hidden = false; return; }
    try {
      const res = await request("/blend/records/bulk", {
        method: "POST",
        body: { recipe_id, worker, scale: $("bulk-scale").value.trim() || null,
                deduct_stock: $("bulk-deduct").checked, entries },
      });
      notify(`${res.created}건 일괄 생성 완료 — 배합 기록으로 이동합니다.`, "success");
      $("bulk-body").innerHTML = "";
      addBulkRow();
      setTimeout(() => window.location.assign("/status"), 800);
    } catch (e) { err.textContent = e.message; err.hidden = false; }
  }

  // ── 배합 입력 ──────────────────────────────────────────────
  async function loadRecipes() {
    const data = await request("/blend/recipes");
    state.recipes = data.items || [];
    const sel = $("blend-recipe");
    sel.innerHTML = '<option value="">레시피 선택…</option>';
    state.recipes.forEach((r) => {
      const o = document.createElement("option");
      o.value = String(r.id);
      o.textContent = r.product_name;
      sel.appendChild(o);
    });
  }

  async function onRecipeChange() {
    const id = $("blend-recipe").value;
    if (!id) {
      state.current = null;
      state.items = [];
      renderMatRows();
      return;
    }
    const totalRaw = Number($("blend-total").value);
    const query = totalRaw > 0 ? { total: totalRaw } : {};
    const data = await request(`/blend/recipes/${id}`, { query });
    state.current = data;
    state.items = data.items.map((it) => ({ ...it, actual_amount: "", material_lot: "" }));
    // 총 배합량은 비워 둠(입력해야 하는 값임을 인지). 비어 있으면 이론량은 "-"로.
    if (!(totalRaw > 0)) state.items.forEach((it) => { it.theory_amount = null; });
    // 자재별 보유 LOT 추천 로드 (있으면 datalist 제공)
    state.lotMap = {};
    const ids = state.items.map((it) => it.material_id).filter(Boolean);
    if (ids.length) {
      try {
        const res = await request("/blend/material-lots", { query: { material_ids: ids.join(",") } });
        state.lotMap = res.map || {};
      } catch (_e) { /* optional */ }
    }
    renderMatRows();
    updateLotPreview();
    updateInputGuide();
  }

  function recomputeTheory() {
    const total = Number($("blend-total").value) || 0;
    state.items.forEach((it) => {
      it.theory_amount = Math.round((it.ratio / 100) * total * 1000) / 1000;
    });
  }

  // 순차 입력 안내: 총 배합량(공백) 강조 → 입력되면 작업자 강조
  function updateInputGuide() {
    const total = $("blend-total");
    const worker = $("blend-worker");
    const totalReady = Number(total.value) > 0;
    total.classList.toggle("needs-input", !totalReady);
    worker.classList.toggle("needs-input", totalReady && !worker.value.trim());
  }

  function renderMatRows() {
    const body = $("blend-mat-body");
    body.innerHTML = "";
    if (!state.items.length) {
      body.innerHTML = '<tr><td colspan="7" class="muted">레시피를 선택하세요.</td></tr>';
      updateTotals();
      return;
    }
    state.items.forEach((it, idx) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${idx + 1}</td>` +
        `<td>${it.material_name}</td>` +
        `<td class="num">${fmt(it.ratio, 2)}</td>` +
        `<td class="num blend-theory">${fmt(it.theory_amount)}</td>` +
        `<td class="num"><input class="input blend-actual" data-idx="${idx}" type="number" step="0.1" min="0" value="${it.actual_amount}" placeholder="${it.theory_amount == null ? "" : fmt(it.theory_amount)}" /></td>` +
        `<td><input class="input blend-lot" data-idx="${idx}" value="${it.material_lot}" placeholder="LOT" list="lots-${idx}" />${lotDatalist(idx, it)}</td>` +
        `<td class="num blend-var" data-idx="${idx}">-</td>`;
      body.appendChild(tr);
    });
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("input", () => {
        const i = Number(el.dataset.idx);
        state.items[i].actual_amount = el.value;
        updateRowVar(i);
        updateTotals();
      })
    );
    // 편차는 발생하면 안 됨 — 실제량 입력 완료(blur) 시 이론량과 다르면 경고
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("change", () => warnIfVariance(Number(el.dataset.idx)))
    );
    body.querySelectorAll(".blend-lot").forEach((el) =>
      el.addEventListener("input", () => { state.items[Number(el.dataset.idx)].material_lot = el.value; })
    );
    // 키보드 흐름: 실제량 Enter → 같은 행 LOT, LOT Enter → 다음 품목 실제량(마지막이면 저장)
    const focusField = (selector) => {
      const t = body.querySelector(selector);
      if (!t) return false;
      t.focus();
      if (typeof t.select === "function") {
        try { t.select(); } catch (_e) { /* number input select 미지원 무시 */ }
      }
      return true;
    };
    body.querySelectorAll(".blend-actual").forEach((el) =>
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        focusField(`.blend-lot[data-idx="${el.dataset.idx}"]`);
      })
    );
    body.querySelectorAll(".blend-lot").forEach((el) =>
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter" || e.isComposing) return;
        e.preventDefault();
        const next = Number(el.dataset.idx) + 1;
        if (!focusField(`.blend-actual[data-idx="${next}"]`)) {
          const save = document.getElementById("blend-save");
          if (save) save.focus();
        }
      })
    );
    updateTotals();
  }

  function lotDatalist(idx, item) {
    const lots = (state.lotMap && state.lotMap[item.material_id]) || [];
    if (!lots.length) return "";
    const opts = lots.map((l) =>
      `<option value="${l.lot_no}">잔량 ${fmt(l.remaining_quantity)}${l.expiry_date ? " · ~" + l.expiry_date : ""}</option>`
    ).join("");
    return `<datalist id="lots-${idx}">${opts}</datalist>`;
  }

  function updateRowVar(i) {
    const it = state.items[i];
    const cell = document.querySelector(`.blend-var[data-idx="${i}"]`);
    if (!cell) return;
    const actual = it.actual_amount === "" ? null : Number(it.actual_amount);
    if (actual === null || it.theory_amount === null) { cell.textContent = "-"; cell.className = "num blend-var"; return; }
    const v = Math.round((actual - it.theory_amount) * 1000) / 1000;
    cell.textContent = (v > 0 ? "+" : "") + fmt(v, 2);
    cell.className = "num blend-var " + (Math.abs(v) < 1e-9 ? "" : v > 0 ? "var-up" : "var-down");
  }

  function rowVariance(it) {
    if (!it || it.actual_amount === "" || it.theory_amount == null) return 0;
    return Math.round((Number(it.actual_amount) - it.theory_amount) * 1000) / 1000;
  }

  function warnIfVariance(i) {
    const it = state.items[i];
    const v = rowVariance(it);
    if (Math.abs(v) > 1e-9) {
      notify(
        `편차 발생: ${it.material_name} — 이론 ${fmt(it.theory_amount)} ≠ 실제 ${fmt(it.actual_amount)} `
        + `(편차 ${v > 0 ? "+" : ""}${fmt(v, 2)}). 잘못된 값입니다.`,
        "error",
      );
      return true;
    }
    return false;
  }

  function updateTotals() {
    const theory = state.items.reduce((s, it) => s + (it.theory_amount || 0), 0);
    const actual = state.items.reduce((s, it) => s + (it.actual_amount === "" ? 0 : Number(it.actual_amount) || 0), 0);
    $("blend-theory-total").textContent = state.items.length ? fmt(theory) : "-";
    $("blend-actual-total").textContent = state.items.length ? fmt(actual) : "-";
    const net = actual - theory;
    const nv = $("blend-net-var");
    nv.textContent = state.items.length ? (net > 0 ? "+" : "") + fmt(net, 2) : "-";
  }

  function updateLotPreview() {
    if (!state.current) { $("blend-lot-preview").textContent = "-"; return; }
    const date = ($("blend-date").value || todayISO()).replace(/-/g, "");
    const yymmdd = date.slice(2, 8);
    $("blend-lot-preview").textContent = `${state.current.recipe.product_name}${yymmdd}NN`;
  }

  async function saveBlend() {
    const err = $("blend-error");
    err.hidden = true;
    if (!state.current) { err.textContent = "레시피를 선택하세요."; err.hidden = false; return; }
    const worker = $("blend-worker").value.trim();
    const total = Number($("blend-total").value);
    if (!worker) { err.textContent = "작업자를 입력하세요."; err.hidden = false; return; }
    if (!(total > 0)) { err.textContent = "총 배합량을 입력하세요."; err.hidden = false; return; }
    // 편차는 발생하면 안 됨 — 실제량 ≠ 이론량인 품목이 있으면 저장 차단(잘못된 값)
    const bad = state.items.filter((it) => Math.abs(rowVariance(it)) > 1e-9);
    if (bad.length) {
      const names = bad.map((it) => `${it.material_name}(${rowVariance(it) > 0 ? "+" : ""}${fmt(rowVariance(it), 2)})`).join(", ");
      err.textContent = `편차가 있어 저장할 수 없습니다(잘못된 값): ${names}. 실제량을 이론량과 같게 입력하세요.`;
      err.hidden = false;
      notify("편차 발생 — 저장할 수 없습니다. 실제량을 이론량과 일치시키세요.", "error");
      return;
    }
    if (!(await ensureWorker(worker))) return;
    const body = {
      recipe_id: state.current.recipe.id,
      product_name: state.current.recipe.product_name,
      ink_name: state.current.recipe.ink_name,
      position: state.current.recipe.position,
      worker,
      work_date: $("blend-date").value || todayISO(),
      work_time: $("blend-time").value || nowTime(),
      total_amount: total,
      scale: $("blend-scale").value.trim() || null,
      note: $("blend-note").value.trim() || null,
      worker_sign: state.workerPad ? state.workerPad.dataUrl() : null,
      details: state.items.map((it, idx) => ({
        material_id: it.material_id,
        material_name: it.material_name,
        material_code: it.material_code,
        ratio: it.ratio,
        theory_amount: it.theory_amount,
        actual_amount: it.actual_amount === "" ? null : Number(it.actual_amount),
        material_lot: it.material_lot || null,
        sequence_order: idx + 1,
      })),
    };
    try {
      const rec = await request("/blend/records", { method: "POST", body });
      notify(`배합 실적 저장: ${rec.product_lot}`, "success");
      // 실제량/LOT 초기화 (연속 작업 편의)
      state.items.forEach((it) => { it.actual_amount = ""; it.material_lot = ""; });
      if (state.workerPad) state.workerPad.clear();
      renderMatRows();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  // ── 기록 조회 ──────────────────────────────────────────────
  async function loadWorkers() {
    try {
      const data = await request("/blend/workers");
      const sel = $("rec-worker");
      sel.innerHTML = '<option value="">전체</option>';
      (data.items || []).forEach((w) => {
        const o = document.createElement("option");
        o.value = w; o.textContent = w; sel.appendChild(o);
      });
    } catch (_e) { /* ignore */ }
  }

  async function loadRecords() {
    const query = {
      start_date: $("rec-from").value || undefined,
      end_date: $("rec-to").value || undefined,
      worker: $("rec-worker").value || undefined,
      search: $("rec-search").value.trim() || undefined,
    };
    const data = await request("/blend/records", { query });
    const body = $("rec-body");
    body.innerHTML = "";
    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted">기록이 없습니다.</td></tr>';
      return;
    }
    items.forEach((r) => {
      const tr = document.createElement("tr");
      tr.className = "blend-row";
      tr.innerHTML =
        `<td>${r.work_date}</td><td>${r.product_lot}</td>` +
        `<td>${r.product_name}</td>` +
        `<td>${r.worker}</td><td class="num">${fmt(r.total_amount)}</td><td>${r.scale || "-"}</td>`;
      tr.addEventListener("click", () => openDetail(r.id));
      body.appendChild(tr);
    });
  }

  async function openDetail(id) {
    const rec = await request(`/blend/records/${id}`);
    state.detailId = id;
    $("blend-detail-title").textContent = `배합 실적서 — ${rec.product_lot}`;
    const v = rec.variance || {};
    const rows = (rec.details || []).map((d, i) =>
      `<tr>
        <td>${i + 1}</td><td>${d.material_name}</td>
        <td class="num">${fmt(d.ratio, 2)}</td>
        <td class="num">${fmt(d.theory_amount)}</td>
        <td class="num">${fmt(d.actual_amount)}</td>
        <td class="num ${d.variance > 0 ? "var-up" : d.variance < 0 ? "var-down" : ""}">${d.variance === null ? "-" : (d.variance > 0 ? "+" : "") + fmt(d.variance, 2)}</td>
        <td>${d.material_lot || "-"}</td>
      </tr>`).join("");
    $("blend-detail-body").innerHTML =
      `<div class="dhr-head">
        <div><span class="dhr-k">제품 LOT</span><b>${rec.product_lot}</b></div>
        <div><span class="dhr-k">제품</span><b>${rec.product_name}</b></div>
        <div><span class="dhr-k">작업자</span><b>${rec.worker}</b></div>
        <div><span class="dhr-k">작업일시</span><b>${rec.work_date} ${rec.work_time || ""}</b></div>
        <div><span class="dhr-k">총 배합량</span><b>${fmt(rec.total_amount)} g</b></div>
        <div><span class="dhr-k">저울</span><b>${rec.scale || "-"}</b></div>
      </div>
      <div class="table-wrap"><table class="blend-table">
        <thead><tr><th>#</th><th>품목</th><th class="num">비율(%)</th><th class="num">이론(g)</th><th class="num">실제(g)</th><th class="num">편차(g)</th><th>자재 LOT</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr><td colspan="3">합계</td><td class="num">${fmt(v.theory_total)}</td><td class="num">${fmt(v.actual_total)}</td><td class="num">${(v.net_variance > 0 ? "+" : "") + fmt(v.net_variance, 2)}</td><td></td></tr></tfoot>
      </table></div>
      ${rec.note ? `<p class="dhr-note">비고: ${rec.note}</p>` : ""}
      ${renderApprovalSection(rec)}
      ${renderViscositySection(rec)}`;
    bindApprovalSection(id);
    bindViscositySection(id);
    $("blend-detail-modal").hidden = false;
  }

  function approvalCell(label, name, at, sign) {
    const img = sign ? `<img class="dhr-sign-img" src="${sign}" alt="서명" />` : "";
    return `<div class="dhr-sign">
      <div class="dhr-sign-role">${label}</div>
      ${img}
      <div class="dhr-sign-name">${name || ""}</div>
      <div class="dhr-sign-at">${at ? at.slice(0, 16).replace("T", " ") : ""}</div>
    </div>`;
  }

  function renderApprovalSection(rec) {
    return `<div class="dhr-approvals">
        ${approvalCell("작성", rec.created_by, rec.created_at, rec.worker_sign)}
        ${approvalCell("검토", rec.reviewed_by, rec.reviewed_at, rec.reviewed_sign)}
        ${approvalCell("승인", rec.approved_by, rec.approved_at, rec.approved_sign)}
      </div>
      <div class="blend-approve-bar no-print">
        <input class="input" id="blend-approve-name" placeholder="결재자 이름" />
        <div class="blend-sign-field">
          <span class="filter-label">서명 (선택)</span>
          <canvas id="blend-approve-sign" class="blend-sign-pad" width="240" height="70"></canvas>
          <button class="btn btn-sm" id="blend-approve-sign-clear" type="button">지우기</button>
        </div>
        <button class="btn btn-sm" id="blend-review-btn" type="button">검토 기록</button>
        <button class="btn btn-sm accent" id="blend-approve-btn" type="button">승인 기록</button>
      </div>`;
  }

  function bindApprovalSection(recordId) {
    const pad = attachSignaturePad($("blend-approve-sign"));
    const clr = $("blend-approve-sign-clear");
    if (clr && pad) clr.addEventListener("click", () => pad.clear());
    const doApprove = async (role) => {
      const name = ($("blend-approve-name").value || "").trim();
      if (!name) { notify("결재자 이름을 입력하세요.", "warn"); return; }
      try {
        await request(`/blend/records/${recordId}/approve`, {
          method: "POST",
          body: { role, name, signature: pad ? pad.dataUrl() : null },
        });
        notify(role === "review" ? "검토 기록됨" : "승인 기록됨", "success");
        openDetail(recordId);
      } catch (e) { notify(e.message, "error"); }
    };
    const rb = $("blend-review-btn"), ab = $("blend-approve-btn");
    if (rb) rb.addEventListener("click", () => doApprove("review"));
    if (ab) ab.addEventListener("click", () => doApprove("approve"));
  }

  function renderViscositySection(rec) {
    const linked = rec.viscosity || [];
    const list = linked.length
      ? `<ul class="blend-visc-list">${linked.map((v) =>
          `<li><b>${v.product_code}</b> ${fmt(v.viscosity)} <span class="muted small">${v.measured_date || ""}${v.created_by ? " · " + v.created_by : ""}</span></li>`
        ).join("")}</ul>`
      : '<p class="muted small">측정된 점도가 없습니다.</p>';
    return `<div class="blend-visc-block no-print">
      <h4 class="panel-title">점도 측정</h4>
      ${list}
      <div class="blend-visc-form">
        <input class="input" id="blend-visc-value" type="number" step="0.1" min="0" placeholder="점도값" />
        <input class="input" id="blend-visc-memo" placeholder="메모(선택)" />
        <button class="btn btn-sm accent" id="blend-visc-add" type="button">점도 기록</button>
      </div>
      <p class="login-error" id="blend-visc-error" hidden></p>
    </div>`;
  }

  function bindViscositySection(recordId) {
    const btn = $("blend-visc-add");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      const err = $("blend-visc-error");
      err.hidden = true;
      const viscosity = Number($("blend-visc-value").value);
      if (!(viscosity > 0)) { err.textContent = "점도값을 입력하세요."; err.hidden = false; return; }
      try {
        await request(`/blend/records/${recordId}/viscosity`, {
          method: "POST",
          body: { viscosity, memo: $("blend-visc-memo").value.trim() || null },
        });
        notify("점도를 기록했습니다.", "success");
        openDetail(recordId);
      } catch (e) { err.textContent = e.message; err.hidden = false; }
    });
  }

  async function cancelDetail() {
    if (!state.detailId) return;
    if (!window.confirm("이 배합 기록을 취소(삭제 표시)할까요?")) return;
    try {
      await request(`/blend/records/${state.detailId}`, { method: "DELETE" });
      notify("기록이 취소되었습니다.", "success");
      $("blend-detail-modal").hidden = true;
      loadRecords();
    } catch (e) { notify(`취소 실패: ${e.message}`, "error"); }
  }

  function bind() {
    $("blend-recipe").addEventListener("change", () => onRecipeChange().catch((e) => notify(e.message, "error")));
    $("blend-total").addEventListener("input", () => {
      recomputeTheory();
      state.items.forEach((_, i) => updateRowVar(i));
      // 이론량 셀 + 실제량 입력칸 안내값 갱신
      document.querySelectorAll("#blend-mat-body tr").forEach((tr, i) => {
        const cell = tr.querySelector(".blend-theory");
        if (cell && state.items[i]) cell.textContent = fmt(state.items[i].theory_amount);
        const act = tr.querySelector(".blend-actual");
        if (act && state.items[i]) {
          act.placeholder = state.items[i].theory_amount == null ? "" : fmt(state.items[i].theory_amount);
        }
      });
      updateTotals();
      updateLotPreview();
      updateInputGuide();
    });
    $("blend-worker").addEventListener("input", updateInputGuide);
    const extraToggle = $("blend-extra-toggle");
    if (extraToggle) {
      extraToggle.addEventListener("click", () => {
        const box = $("blend-extra");
        const open = box.hidden;
        box.hidden = !open;
        extraToggle.setAttribute("aria-expanded", String(open));
        extraToggle.textContent = (open ? "▾" : "▸") + " 작업시간 · 저울 변경";
      });
    }
    $("blend-date").addEventListener("change", updateLotPreview);
    $("blend-save").addEventListener("click", () => saveBlend());
    state.workerPad = attachSignaturePad($("blend-worker-sign"));
    const wclr = $("blend-worker-sign-clear");
    if (wclr && state.workerPad) wclr.addEventListener("click", () => state.workerPad.clear());
    $("rec-apply").addEventListener("click", () => loadRecords().catch((e) => notify(e.message, "error")));
    $("bulk-add-row").addEventListener("click", addBulkRow);
    $("bulk-create").addEventListener("click", createBulk);
    $("rec-export-all").addEventListener("click", () => {
      const q = new URLSearchParams();
      if ($("rec-from").value) q.set("start_date", $("rec-from").value);
      if ($("rec-to").value) q.set("end_date", $("rec-to").value);
      if ($("rec-worker").value) q.set("worker", $("rec-worker").value);
      if ($("rec-search").value.trim()) q.set("search", $("rec-search").value.trim());
      window.location.assign(`/api/blend/records/export-all?${q.toString()}`);
    });
    $("blend-detail-close").addEventListener("click", () => { $("blend-detail-modal").hidden = true; });
    $("blend-detail-cancel").addEventListener("click", cancelDetail);
    $("blend-pdf").addEventListener("click", () => {
      if (state.detailId) window.open(`/api/blend/records/${state.detailId}/pdf`, "_blank");
    });
    $("blend-print").addEventListener("click", () => window.print());
    $("blend-excel").addEventListener("click", () => {
      if (state.detailId) window.location.assign(`/api/blend/records/${state.detailId}/export`);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) { console.error("IRMS core not loaded"); return; }
    $("blend-date").value = todayISO();
    $("blend-time").value = nowTime();
    bind();
    // 경로로 모드 결정: /blend/bulk = 일괄 생성, 그 외 = 배합 입력
    setMode(location.pathname.replace(/\/+$/, "").endsWith("/bulk") ? "bulk" : "entry");
    updateInputGuide();
    loadRecipes().catch((e) => notify(`레시피 로드 실패: ${e.message}`, "error"));
    loadWorkers();
    loadWorkerNames();
    request("/viscosity/products")
      .then((d) => { state.viscProducts = (d.items || []).filter((p) => p.is_active); })
      .catch(() => {});
  });
})();
