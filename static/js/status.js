/**
 * status.js — 배합 기록 · DHR 관리 (/status).
 * 배합 기록 목록(필터) + 클릭 시 제조이력서(DHR) 조회 + 인쇄/Excel.
 * 결재·점도 등록 등 쓰기 작업은 배합 화면에서 수행한다(여기는 조회·출력 중심).
 */
document.addEventListener("DOMContentLoaded", () => {
  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const $ = (id) => document.getElementById(id);
  const fmt = (v, d = 1) =>
    v === null || v === undefined || v === ""
      ? "-"
      : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d });
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  let detailId = null;

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
        body.innerHTML = '<tr><td colspan="6" class="muted">기록이 없습니다.</td></tr>';
        return;
      }
      body.innerHTML = "";
      items.forEach((r) => {
        const tr = document.createElement("tr");
        tr.className = "blend-row";
        tr.innerHTML =
          `<td>${esc(r.work_date)}</td><td>${esc(r.product_lot)}</td>` +
          `<td>${esc(r.product_name)}${r.ink_name ? " / " + esc(r.ink_name) : ""}</td>` +
          `<td>${esc(r.worker)}</td><td class="num">${fmt(r.total_amount)}</td><td>${esc(r.scale || "-")}</td>`;
        tr.addEventListener("click", () => openDetail(r.id));
        body.appendChild(tr);
      });
    } catch (e) {
      body.innerHTML = `<tr><td colspan="6" class="muted">불러오기 실패: ${esc(e.message || e)}</td></tr>`;
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
    const visc = (rec.viscosity || []).length
      ? `<div class="blend-visc-block"><b>연계 점도</b><ul class="blend-visc-list">${rec.viscosity
          .map(
            (x) =>
              `<li><b>${esc(x.product_code)}</b> ${fmt(x.viscosity)} <span class="muted small">${esc(x.measured_date || "")}${x.created_by ? " · " + esc(x.created_by) : ""}</span></li>`,
          )
          .join("")}</ul></div>`
      : "";
    $("status-detail-body").innerHTML =
      `<div class="dhr-head">
        <div><span class="dhr-k">제품 LOT</span><b>${esc(rec.product_lot)}</b></div>
        <div><span class="dhr-k">제품</span><b>${esc(rec.product_name)}${rec.ink_name ? " / " + esc(rec.ink_name) : ""}</b></div>
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
      <div class="dhr-approvals">${approvalCell("작성", rec.created_by, rec.created_at, rec.worker_sign)}${approvalCell("검토", rec.reviewed_by, rec.reviewed_at, rec.reviewed_sign)}${approvalCell("승인", rec.approved_by, rec.approved_at, rec.approved_sign)}</div>
      ${visc}`;
    $("status-detail-modal").hidden = false;
  }

  $("status-rec-apply").addEventListener("click", loadRecords);
  $("status-rec-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadRecords();
  });
  $("status-detail-close").addEventListener("click", () => {
    $("status-detail-modal").hidden = true;
  });
  $("status-pdf").addEventListener("click", () => {
    if (detailId) window.open(`/api/blend/records/${detailId}/pdf`, "_blank");
  });
  $("status-print").addEventListener("click", () => window.print());
  $("status-excel").addEventListener("click", () => {
    if (detailId) window.location.assign(`/api/blend/records/${detailId}/export`);
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
