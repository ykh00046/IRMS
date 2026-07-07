/**
 * status.js — 배합 기록 · DHR 관리 (/status).
 * 배합 기록 목록(필터) + 클릭 시 제조이력서(DHR) 조회 + 인쇄/Excel.
 * 결재·점도 등록 등 쓰기 작업은 배합 화면에서 수행한다(여기는 조회·출력 중심).
 */
document.addEventListener("DOMContentLoaded", () => {
  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const $ = (id) => document.getElementById(id);
  // 기본 소수 2자리 — 저울(XP 0.01g) 해상도에 맞춤
  const fmt = (v, d = 2) =>
    v === null || v === undefined || v === ""
      ? "-"
      : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d });
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  let detailId = null;
  let currentRecord = null;

  async function deleteRecord(recordId) {
    await request(`/blend/records/${recordId}`, {
      method: "DELETE",
      query: { hard: 1 },
    });
  }

  async function loadWorkers() {
    try {
      const data = await request("/blend/workers");
      const sel = $("status-rec-worker");
      (data.items || []).forEach((w) => {
        const o = document.createElement("option");
        o.value = w;
        o.textContent = w;
        sel.appendChild(o);
      });
    } catch (_e) {
      /* 작업자 목록 실패는 조회에 영향 없음 */
    }
  }

  async function loadRecords() {
    const body = $("status-rec-body");
    const query = {
      start_date: $("status-rec-from").value || undefined,
      end_date: $("status-rec-to").value || undefined,
      worker: $("status-rec-worker").value || undefined,
      search: $("status-rec-search").value.trim() || undefined,
    };
    try {
      const data = await request("/blend/records", { query });
      const items = data.items || [];
      if (!items.length) {
        body.innerHTML = '<tr><td colspan="7" class="muted">기록이 없습니다.</td></tr>';
        return;
      }
      body.innerHTML = "";
      const allChk = $("status-rec-all");
      if (allChk) allChk.checked = false;
      items.forEach((r) => {
        const tr = document.createElement("tr");
        tr.className = "blend-row";
        tr.innerHTML =
          `<td class="chk-col"><input type="checkbox" class="rec-chk" value="${r.id}" /></td>` +
          `<td>${esc(r.work_date)}</td><td>${esc(r.product_lot)}</td>` +
          `<td>${esc(r.product_name)}</td>` +
          `<td>${esc(r.worker)}</td><td class="num">${fmt(r.total_amount)}</td><td>${esc(r.scale || "-")}</td>`;
        tr.addEventListener("click", () => openDetail(r.id));
        tr.querySelector(".rec-chk").addEventListener("click", (e) => e.stopPropagation());
        body.appendChild(tr);
      });
    } catch (e) {
      body.innerHTML = `<tr><td colspan="7" class="muted">불러오기 실패: ${esc(e.message || e)}</td></tr>`;
    }
  }

  function approvalCell(label, name, at, sign) {
    const img = sign ? `<img class="dhr-sign-img" src="${sign}" alt="서명" />` : "";
    return `<div class="dhr-sign">
      <div class="dhr-sign-role">${label}</div>${img}
      <div class="dhr-sign-name">${esc(name || "")}</div>
      <div class="dhr-sign-at">${at ? esc(at.slice(0, 16).replace("T", " ")) : ""}</div>
    </div>`;
  }

  async function openDetail(id) {
    const rec = await request(`/blend/records/${id}`);
    detailId = id;
    currentRecord = rec;
    setEditChromeHidden(false);
    $("status-detail-title").textContent = `배합 실적서 — ${rec.product_lot}`;
    const v = rec.variance || {};
    const rows = (rec.details || [])
      .map(
        (d, i) =>
          `<tr><td>${i + 1}</td><td>${esc(d.material_name)}</td>` +
          `<td class="num">${fmt(d.ratio, 2)}</td><td class="num">${fmt(d.theory_amount)}</td>` +
          `<td class="num">${fmt(d.actual_amount)}</td>` +
          `<td class="num ${d.variance > 0 ? "var-up" : d.variance < 0 ? "var-down" : ""}">${d.variance == null ? "-" : (d.variance > 0 ? "+" : "") + fmt(d.variance, 2)}</td>` +
          `<td>${esc(d.material_lot || "-")}</td></tr>`,
      )
      .join("");
    const linkedVisc = (rec.viscosity || []).length
      ? `<ul class="blend-visc-list">${rec.viscosity
          .map(
            (x) =>
              `<li><b>${esc(x.product_code)}</b> ${fmt(x.viscosity)} <span class="muted small">${esc(x.measured_date || "")}${x.created_by ? " · " + esc(x.created_by) : ""}</span></li>`,
          )
          .join("")}</ul>`
      : '<p class="muted small">측정된 점도가 없습니다. (등록은 점도 관리 화면에서)</p>';
    // 점도 등록은 '점도 관리' 화면 한 곳으로 통일 — 여기선 측정값을 읽기전용으로만 표시.
    const visc = `<div class="blend-visc-block"><b>점도 측정</b>${linkedVisc}</div>`;
    $("status-detail-body").innerHTML =
      `<div class="dhr-head">
        <div><span class="dhr-k">제품 LOT</span><b>${esc(rec.product_lot)}</b></div>
        <div><span class="dhr-k">제품</span><b>${esc(rec.product_name)}</b></div>
        <div><span class="dhr-k">작업자</span><b>${esc(rec.worker)}</b></div>
        <div><span class="dhr-k">작업일시</span><b>${esc(rec.work_date)} ${esc(rec.work_time || "")}</b></div>
        <div><span class="dhr-k">총 배합량</span><b>${fmt(rec.total_amount)} g</b></div>
        <div><span class="dhr-k">저울</span><b>${esc(rec.scale || "-")}</b></div>
      </div>
      <div class="table-wrap"><table class="blend-table">
        <thead><tr><th>#</th><th>품목</th><th class="num">비율(%)</th><th class="num">이론(g)</th><th class="num">실제(g)</th><th class="num">편차(g)</th><th>자재 LOT</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr><td colspan="3">합계</td><td class="num">${fmt(v.theory_total)}</td><td class="num">${fmt(v.actual_total)}</td><td class="num">${(v.net_variance > 0 ? "+" : "") + fmt(v.net_variance, 2)}</td><td></td></tr></tfoot>
      </table></div>
      ${rec.note ? `<p class="dhr-note">비고: ${esc(rec.note)}</p>` : ""}
      <div class="dhr-foot-row">
        <div class="dhr-approvals dhr-approvals-single">${approvalCell("작성", rec.created_by, rec.created_at, rec.worker_sign)}</div>
        ${visc}
      </div>`;
    $("status-detail-modal").hidden = false;
  }

  // ── 전체 수정(책임자 전용) ────────────────────────────────────
  // 헤더 액션(PDF/인쇄/Excel/수정/삭제 + 서명 체크)을 편집 중에는 숨긴다.
  const EDIT_HIDE_IDS = ["status-pdf", "status-excel", "status-edit", "status-delete"];

  function setEditChromeHidden(hidden) {
    EDIT_HIDE_IDS.forEach((id) => { const el = $(id); if (el) el.style.display = hidden ? "none" : ""; });
    const signWrap = $("status-detail-sign");
    if (signWrap && signWrap.closest("label")) signWrap.closest("label").style.display = hidden ? "none" : "";
  }

  function editRow(d) {
    return `<tr class="edit-row">
      <td><input class="input e-name" value="${esc(d.material_name || "")}" placeholder="자재명" /></td>
      <td><input class="input num e-ratio" type="number" step="0.01" min="0" value="${d.ratio ?? ""}" /></td>
      <td><input class="input num e-theory" type="number" step="0.01" min="0" value="${d.theory_amount ?? ""}" /></td>
      <td><input class="input num e-actual" type="number" step="0.01" min="0" value="${d.actual_amount ?? ""}" /></td>
      <td><input class="input e-lot" value="${esc(d.material_lot || "")}" placeholder="LOT(선택)" /></td>
      <td><button class="btn btn-sm danger e-del" type="button" title="행 삭제">×</button></td>
    </tr>`;
  }

  function renderEditForm(rec) {
    setEditChromeHidden(true);
    $("status-detail-title").textContent = `배합 실적서 수정 — ${rec.product_lot}`;
    const rows = (rec.details || []).map(editRow).join("");
    $("status-detail-body").innerHTML =
      `<div class="edit-head-grid">
        <label class="edit-f"><span>제품명</span><input class="input" id="e-product" value="${esc(rec.product_name || "")}" /></label>
        <label class="edit-f"><span>작업자</span><input class="input" id="e-worker" value="${esc(rec.worker || "")}" /></label>
        <label class="edit-f"><span>작업일</span><input class="input" id="e-date" type="date" value="${esc(rec.work_date || "")}" /></label>
        <label class="edit-f"><span>작업시간</span><input class="input" id="e-time" value="${esc(rec.work_time || "")}" placeholder="HH:MM" /></label>
        <label class="edit-f"><span>총 배합량(g)</span><input class="input num" id="e-total" type="number" step="0.01" min="0" value="${rec.total_amount ?? ""}" /></label>
        <label class="edit-f"><span>저울</span><input class="input" id="e-scale" value="${esc(rec.scale || "")}" /></label>
        <label class="edit-f"><span>반응기(1~4, 선택)</span><input class="input num" id="e-reactor" type="number" min="1" max="4" value="${rec.reactor ?? ""}" /></label>
        <label class="edit-f edit-f-wide"><span>비고</span><input class="input" id="e-note" value="${esc(rec.note || "")}" /></label>
      </div>
      <div class="table-wrap"><table class="blend-table edit-table">
        <thead><tr><th>품목</th><th class="num">비율(%)</th><th class="num">이론(g)</th><th class="num">실제(g)</th><th>자재 LOT</th><th></th></tr></thead>
        <tbody id="e-rows">${rows}</tbody>
      </table></div>
      <div class="edit-actions">
        <button class="btn btn-sm" id="e-add-row" type="button">＋ 행 추가</button>
        <span class="edit-spacer"></span>
        <button class="btn btn-sm" id="e-cancel" type="button">취소</button>
        <button class="btn btn-sm accent" id="e-save" type="button">저장</button>
      </div>
      <p class="login-error" id="e-error" hidden></p>
      <p class="muted small">제품 LOT·서명·생성 정보는 그대로 유지됩니다. 자재별 편차는 ±0.05g 이내여야 저장됩니다.</p>`;

    $("e-rows").addEventListener("click", (ev) => {
      const del = ev.target.closest(".e-del");
      if (del) del.closest("tr").remove();
    });
    $("e-add-row").addEventListener("click", () => {
      $("e-rows").insertAdjacentHTML("beforeend", editRow({}));
    });
    $("e-cancel").addEventListener("click", () => openDetail(rec.id));
    $("e-save").addEventListener("click", () => saveEdit(rec.id));
  }

  function collectEdit() {
    const details = [...document.querySelectorAll("#e-rows tr")].map((tr) => {
      const name = tr.querySelector(".e-name").value.trim();
      if (!name) return null;
      const numOrNull = (sel) => {
        const v = tr.querySelector(sel).value;
        return v === "" ? null : Number(v);
      };
      return {
        material_name: name,
        ratio: numOrNull(".e-ratio"),
        theory_amount: numOrNull(".e-theory"),
        actual_amount: numOrNull(".e-actual"),
        material_lot: tr.querySelector(".e-lot").value.trim() || null,
      };
    }).filter(Boolean);
    const reactorRaw = $("e-reactor").value;
    return {
      product_name: $("e-product").value.trim(),
      worker: $("e-worker").value.trim(),
      work_date: $("e-date").value,
      work_time: $("e-time").value.trim() || null,
      total_amount: Number($("e-total").value),
      scale: $("e-scale").value.trim() || null,
      note: $("e-note").value.trim() || null,
      reactor: reactorRaw === "" ? null : Number(reactorRaw),
      details,
    };
  }

  async function saveEdit(id) {
    const err = $("e-error");
    err.hidden = true;
    const body = collectEdit();
    if (!body.product_name) { err.textContent = "제품명을 입력하세요."; err.hidden = false; return; }
    if (!body.worker) { err.textContent = "작업자를 입력하세요."; err.hidden = false; return; }
    if (!body.work_date) { err.textContent = "작업일을 입력하세요."; err.hidden = false; return; }
    if (!(body.total_amount > 0)) { err.textContent = "총 배합량을 입력하세요."; err.hidden = false; return; }
    if (!body.details.length) { err.textContent = "자재를 1개 이상 입력하세요."; err.hidden = false; return; }
    const btn = $("e-save");
    IRMS.btnLoading && IRMS.btnLoading(btn, true);
    try {
      await request(`/blend/records/${id}`, { method: "PUT", body });
      IRMS.notify("배합 기록을 수정했습니다.", "success");
      await openDetail(id);
      await loadRecords();
    } catch (e) {
      err.textContent = e.message || String(e);
      err.hidden = false;
    } finally {
      IRMS.btnLoading && IRMS.btnLoading(btn, false);
    }
  }

  if ($("status-edit")) {
    $("status-edit").addEventListener("click", () => {
      if (currentRecord) renderEditForm(currentRecord);
    });
  }

  $("status-rec-apply").addEventListener("click", loadRecords);
  $("status-rec-all").addEventListener("change", (e) => {
    document.querySelectorAll("#status-rec-body .rec-chk").forEach((c) => { c.checked = e.target.checked; });
  });
  $("status-rec-dhr-batch").addEventListener("click", () => {
    const ids = [...document.querySelectorAll("#status-rec-body .rec-chk:checked")].map((c) => c.value);
    if (!ids.length) { IRMS.notify("기록을 선택하세요(전체 선택 가능).", "warn"); return; }
    if (ids.length > 200) IRMS.notify("한 번에 최대 200건까지 출력합니다.", "warn");
    const sign = $("status-sign") && $("status-sign").checked ? "&sign=1" : "";
    window.open(`/api/blend/records/dhr-batch?ids=${ids.slice(0, 200).join(",")}${sign}`, "_blank");
  });
  $("status-rec-delete-selected").addEventListener("click", async () => {
    const ids = [...document.querySelectorAll("#status-rec-body .rec-chk:checked")].map((c) => Number(c.value));
    if (!ids.length) { IRMS.notify("삭제할 기록을 선택하세요.", "warn"); return; }
    if (!window.confirm(`선택한 배합 기록 ${ids.length}건을 완전히 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`)) return;
    try {
      for (const id of ids) {
        await deleteRecord(id);
      }
      IRMS.notify(`${ids.length}건을 삭제했습니다.`, "success");
      await loadRecords();
    } catch (e) {
      IRMS.notify(`삭제 실패: ${e.message || e}`, "error");
    }
  });
  $("status-rec-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadRecords();
  });
  $("status-detail-close").addEventListener("click", () => {
    $("status-detail-modal").hidden = true;
  });
  $("status-pdf").addEventListener("click", () => {
    if (!detailId) return;
    const sign = $("status-detail-sign") && $("status-detail-sign").checked ? "?sign=1" : "";
    window.open(`/api/blend/records/${detailId}/pdf${sign}`, "_blank");
  });
  $("status-excel").addEventListener("click", () => {
    if (detailId) window.location.assign(`/api/blend/records/${detailId}/export`);
  });
  $("status-delete").addEventListener("click", async () => {
    if (!detailId) return;
    if (!window.confirm("이 배합 기록을 완전히 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")) return;
    try {
      await deleteRecord(detailId);
      $("status-detail-modal").hidden = true;
      detailId = null;
      IRMS.notify("배합 기록을 삭제했습니다.", "success");
      await loadRecords();
    } catch (e) {
      IRMS.notify(`삭제 실패: ${e.message || e}`, "error");
    }
  });
  $("status-rec-export-all").addEventListener("click", () => {
    const q = new URLSearchParams();
    const map = {
      start_date: $("status-rec-from").value,
      end_date: $("status-rec-to").value,
      worker: $("status-rec-worker").value,
      search: $("status-rec-search").value.trim(),
    };
    Object.entries(map).forEach(([k, val]) => {
      if (val) q.set(k, val);
    });
    window.location.assign(`/api/blend/records/export-all?${q.toString()}`);
  });

  loadWorkers();
  loadRecords();
});
