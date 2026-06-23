/**
 * blend.js — 배합 실적(잉크 계량 재구축) 컨트롤러.
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

  const state = { recipes: [], current: null, items: [], detailId: null, viscProducts: [] };

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
    $("blend-entry-mode").hidden = mode !== "entry";
    $("blend-records-mode").hidden = mode !== "records";
    $("blend-tabs").querySelectorAll("button").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === mode)
    );
    if (mode === "records") loadRecords();
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
      o.textContent = `${r.product_name}${r.ink_name ? " / " + r.ink_name : ""} (${r.item_count}종, ${fmt(r.total_weight)}g)`;
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
    if (!(totalRaw > 0)) $("blend-total").value = data.total_amount;
    renderMatRows();
    updateLotPreview();
  }

  function recomputeTheory() {
    const total = Number($("blend-total").value) || 0;
    state.items.forEach((it) => {
      it.theory_amount = Math.round((it.ratio / 100) * total * 1000) / 1000;
    });
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
        `<td>${it.material_name}${it.material_code ? ` <span class="muted small">${it.material_code}</span>` : ""}</td>` +
        `<td class="num">${fmt(it.ratio, 2)}</td>` +
        `<td class="num blend-theory">${fmt(it.theory_amount)}</td>` +
        `<td class="num"><input class="input blend-actual" data-idx="${idx}" type="number" step="0.1" min="0" value="${it.actual_amount}" /></td>` +
        `<td><input class="input blend-lot" data-idx="${idx}" value="${it.material_lot}" placeholder="LOT" /></td>` +
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
    body.querySelectorAll(".blend-lot").forEach((el) =>
      el.addEventListener("input", () => { state.items[Number(el.dataset.idx)].material_lot = el.value; })
    );
    updateTotals();
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
        `<td>${r.product_name}${r.ink_name ? " / " + r.ink_name : ""}</td>` +
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
        <div><span class="dhr-k">제품</span><b>${rec.product_name}${rec.ink_name ? " / " + rec.ink_name : ""}</b></div>
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
      ${renderViscositySection(rec)}`;
    bindViscositySection(id);
    $("blend-detail-modal").hidden = false;
  }

  function renderViscositySection(rec) {
    const linked = rec.viscosity || [];
    const list = linked.length
      ? `<ul class="blend-visc-list">${linked.map((v) =>
          `<li><b>${v.product_code}</b> ${fmt(v.viscosity)} <span class="muted small">${v.measured_date || ""}${v.created_by ? " · " + v.created_by : ""}</span></li>`
        ).join("")}</ul>`
      : '<p class="muted small">연계된 점도 측정이 없습니다.</p>';
    const opts = state.viscProducts.map((p) =>
      `<option value="${p.id}">${p.code}</option>`).join("");
    return `<div class="blend-visc-block no-print">
      <h4 class="panel-title">점도 연계</h4>
      ${list}
      <div class="blend-visc-form">
        <select class="input" id="blend-visc-product">${opts}</select>
        <input class="input" id="blend-visc-value" type="number" step="0.1" min="0" placeholder="점도" />
        <input class="input" id="blend-visc-memo" placeholder="메모(선택)" />
        <button class="btn btn-sm accent" id="blend-visc-add" type="button">점도 등록</button>
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
      const product_id = Number($("blend-visc-product").value);
      const viscosity = Number($("blend-visc-value").value);
      if (!product_id) { err.textContent = "점도 제품을 선택하세요."; err.hidden = false; return; }
      if (!(viscosity > 0)) { err.textContent = "점도 값을 입력하세요."; err.hidden = false; return; }
      try {
        await request(`/blend/records/${recordId}/viscosity`, {
          method: "POST",
          body: { product_id, viscosity, memo: $("blend-visc-memo").value.trim() || null },
        });
        notify("점도가 연계 등록되었습니다.", "success");
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
    $("blend-tabs").querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => setMode(b.dataset.mode))
    );
    $("blend-recipe").addEventListener("change", () => onRecipeChange().catch((e) => notify(e.message, "error")));
    $("blend-total").addEventListener("input", () => {
      recomputeTheory();
      state.items.forEach((_, i) => updateRowVar(i));
      // 이론량 셀 갱신
      document.querySelectorAll("#blend-mat-body tr").forEach((tr, i) => {
        const cell = tr.querySelector(".blend-theory");
        if (cell && state.items[i]) cell.textContent = fmt(state.items[i].theory_amount);
      });
      updateTotals();
      updateLotPreview();
    });
    $("blend-date").addEventListener("change", updateLotPreview);
    $("blend-save").addEventListener("click", () => saveBlend());
    $("rec-apply").addEventListener("click", () => loadRecords().catch((e) => notify(e.message, "error")));
    $("blend-detail-close").addEventListener("click", () => { $("blend-detail-modal").hidden = true; });
    $("blend-detail-cancel").addEventListener("click", cancelDetail);
    $("blend-print").addEventListener("click", () => window.print());
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) { console.error("IRMS core not loaded"); return; }
    $("blend-date").value = todayISO();
    $("blend-time").value = nowTime();
    bind();
    loadRecipes().catch((e) => notify(`레시피 로드 실패: ${e.message}`, "error"));
    loadWorkers();
    request("/viscosity/products")
      .then((d) => { state.viscProducts = (d.items || []).filter((p) => p.is_active); })
      .catch(() => {});
  });
})();
