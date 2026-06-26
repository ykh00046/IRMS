/**
 * insight.js — 배합 자재 분석 (/insight).
 * 완료된 배합 기록(blend_records/blend_details)에서 자재별 실제 사용량·비중·건수를 집계.
 */
(function () {
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

  function isoDaysAgo(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
  }

  async function load() {
    const start = $("insight-from").value || undefined;
    const end = $("insight-to").value || undefined;
    const body = $("insight-body");
    body.innerHTML = '<tr><td colspan="6" class="muted">불러오는 중…</td></tr>';
    try {
      const d = await request("/blend/material-usage", { query: { start_date: start, end_date: end } });
      $("metric-records").innerHTML = `${fmt(d.record_count, 0)}<span class="metric-unit">건</span>`;
      $("metric-weight").innerHTML = `${fmt(d.total_weight)}<span class="metric-unit">g</span>`;
      $("metric-materials").innerHTML = `${fmt(d.material_count, 0)}<span class="metric-unit">종</span>`;
      $("insight-filter-summary").textContent =
        `${start || "전체"} ~ ${end || "전체"} · 배합 ${fmt(d.record_count, 0)}건 · 자재 ${fmt(d.material_count, 0)}종`;
      const items = d.items || [];
      const totalW = d.total_weight || 0;
      if (!items.length) {
        body.innerHTML = '<tr><td colspan="6" class="muted">기록이 없습니다.</td></tr>';
        return;
      }
      body.innerHTML = items
        .map((it, i) => {
          const pct = totalW > 0 ? (it.total_actual / totalW) * 100 : 0;
          return (
            `<tr><td>${i + 1}</td><td>${esc(it.material_name)}</td>` +
            `<td class="num">${fmt(it.total_actual)}</td>` +
            `<td class="num">${fmt(pct, 1)}</td>` +
            `<td class="num">${fmt(it.total_theory)}</td>` +
            `<td class="num">${fmt(it.usage_count, 0)}</td></tr>`
          );
        })
        .join("");
    } catch (e) {
      body.innerHTML = `<tr><td colspan="6" class="muted">불러오기 실패: ${esc(e.message || e)}</td></tr>`;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) {
      console.error("IRMS core not loaded");
      return;
    }
    $("insight-range-month").addEventListener("click", () => {
      $("insight-from").value = isoDaysAgo(30);
      $("insight-to").value = "";
      load();
    });
    $("insight-range-90").addEventListener("click", () => {
      $("insight-from").value = isoDaysAgo(90);
      $("insight-to").value = "";
      load();
    });
    $("insight-range-all").addEventListener("click", () => {
      $("insight-from").value = "";
      $("insight-to").value = "";
      load();
    });
    $("insight-query").addEventListener("click", load);
    $("insight-from").value = isoDaysAgo(30);
    load();
  });
})();
