/**
 * blend_lot_audit.js — 미해소 LOT 대사 · 총 배합량 이상 (/lot-audit, 책임자 전용).
 *
 * 데이터(둘 다 조회 전용, 책임자 전용):
 *   GET /api/blend/lot-audit/unresolved       미해소 LOT (경과 일수 + 확인 여부)
 *   GET /api/blend/lot-audit/total-anomalies  총량 이상(25kg 초과 / 증량 우회 의심)
 *
 * 이 화면에는 쓰기 동작이 없다. 대사는 "1차 completed 기록이 생겼는가"로 자기
 * 치유되므로 '해소 처리' 버튼을 두지 않는다 — 버튼을 두면 기록이 없는데도 눌러서
 * 지워버릴 수 있고, 그러면 통제가 다시 형해화된다.
 *
 * 각 행의 '기록 보기'는 /status?search=<제품LOT> 딥링크(기존 관례)로 연결한다.
 */
(function () {
  "use strict";

  const win = typeof window !== "undefined" ? window : {};
  const IRMS = (win.IRMS = win.IRMS || {});

  // ── 순수 헬퍼(테스트 대상) ─────────────────────────────────────────────
  // 경과 일수 구간. 오래됐는데 아직 1차 기록이 없다 = 오타/부존재 가능성이 높다.
  //   unknown : created_at 파싱 불가(null)
  //   old     : 14일 이상 — 정당한 '1차 저장이 늦었을 뿐'으로 보기 어렵다
  //   warn    : 3일 이상
  //   ""      : 그 외(막 저장된 건 — 곧 해소될 수 있다)
  function ageBucket(days) {
    if (days === null || days === undefined) return "unknown";
    const n = Number(days);
    if (!Number.isFinite(n)) return "unknown";
    if (n >= 14) return "old";
    if (n >= 3) return "warn";
    return "";
  }

  function fmtG(value) {
    if (value === null || value === undefined || value === "") return "-";
    const n = Number(value);
    if (!Number.isFinite(n)) return "-";
    return n.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
  }

  // 총량 이상 행의 '초과' 셀 텍스트. 두 플래그가 함께 켜질 수 있으므로 배열로 낸다.
  function excessLabels(item) {
    const out = [];
    if (item && item.oversize_total && item.over_limit_g != null) {
      out.push("+" + fmtG(item.over_limit_g) + " g");
    }
    if (item && item.total_bypass_suspect && item.excess_pct != null) {
      out.push("+" + Number(item.excess_pct).toFixed(1) + "%");
    }
    return out;
  }

  IRMS.lotAudit = { ageBucket, fmtG, excessLabels };

  if (typeof document === "undefined" || !document.addEventListener) return;

  document.addEventListener("DOMContentLoaded", () => {
    const request = IRMS._core && IRMS._core.request;
    const notify = IRMS.notify || (() => {});
    const $ = (id) => document.getElementById(id);

    const esc = (s) =>
      String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
      }[c]));

    const ageCell = (days) => {
      const bucket = ageBucket(days);
      if (bucket === "unknown") return '<span class="lau-reason">-</span>';
      const cls = bucket === "old" ? "lau-age-old" : bucket === "warn" ? "lau-age-warn" : "";
      return `<span class="lau-num ${cls}">${esc(days)}일</span>`;
    };

    const recordLink = (lot) =>
      `<a class="btn btn-sm" href="/status?search=${encodeURIComponent(lot || "")}">기록 보기</a>`;

    function renderUnresolved(data) {
      const items = (data && data.items) || [];
      $("lau-card-unresolved").textContent = String(data.total || 0);
      $("lau-card-unack").textContent = String(data.unacknowledged || 0);
      $("lau-card-resolved").textContent = String(data.resolved || 0);
      $("lau-unresolved-loading").hidden = true;
      if (!items.length) {
        $("lau-unresolved-wrap").hidden = true;
        $("lau-unresolved-empty").hidden = false;
        return;
      }
      $("lau-unresolved-empty").hidden = true;
      $("lau-unresolved-wrap").hidden = false;
      $("lau-unresolved-body").innerHTML = items
        .map((it) => {
          // acknowledged=0 = 확인 창조차 못 본 경로(조회 실패 fail-open·초안 복구).
          // 오타 여부와 별개의 신호라 행 전체를 강조하고 칩으로 구분한다.
          const rowCls = it.acknowledged ? "" : ' class="lau-row-unack"';
          const ackChip = it.acknowledged
            ? '<span class="status-chip lau-chip-ack">확인함</span>'
            : '<span class="status-chip lau-chip-unack" title="작업자가 확인 창을 보지 못한 경로로 저장됐습니다">확인 없음</span>';
          return (
            `<tr${rowCls}>` +
            `<td class="num">${ageCell(it.age_days)}</td>` +
            `<td>${ackChip}</td>` +
            `<td>${esc(it.material_name)}</td>` +
            `<td><strong>${esc(it.material_lot)}</strong></td>` +
            `<td>${esc(it.product_lot)}<br /><span class="lau-reason">${esc(it.product_name)}</span></td>` +
            `<td>${esc(it.work_date)}<br /><span class="lau-reason">${esc(it.worker)}</span></td>` +
            `<td class="lau-reason">${esc(it.reason) || "-"}</td>` +
            `<td>${recordLink(it.product_lot)}</td>` +
            "</tr>"
          );
        })
        .join("");
    }

    function renderAnomalies(data) {
      const items = (data && data.items) || [];
      $("lau-card-anomaly").textContent = String(data.total || 0);
      $("lau-card-anomaly-note").textContent =
        `상한 초과 ${data.oversize || 0} · 우회 의심 ${data.bypass_suspect || 0}`;
      if (data.limit_g != null) $("lau-limit").textContent = fmtG(data.limit_g);
      $("lau-anomaly-loading").hidden = true;
      if (!items.length) {
        $("lau-anomaly-wrap").hidden = true;
        $("lau-anomaly-empty").hidden = false;
        return;
      }
      $("lau-anomaly-empty").hidden = true;
      $("lau-anomaly-wrap").hidden = false;
      $("lau-anomaly-body").innerHTML = items
        .map((it) => {
          const chips = [];
          if (it.oversize_total) {
            chips.push('<span class="status-chip lau-chip-over">상한 초과</span>');
          }
          if (it.total_bypass_suspect) {
            chips.push('<span class="status-chip lau-chip-bypass">우회 의심</span>');
          }
          return (
            "<tr>" +
            `<td>${chips.join(" ")}</td>` +
            `<td><strong>${esc(it.product_lot)}</strong></td>` +
            `<td>${esc(it.product_name)}</td>` +
            `<td>${esc(it.work_date)}<br /><span class="lau-reason">${esc(it.worker)}</span></td>` +
            `<td class="num lau-num">${fmtG(it.total_amount)}</td>` +
            `<td class="num lau-num">${fmtG(it.base_total)}</td>` +
            `<td class="num lau-num">${excessLabels(it).join(" · ") || "-"}</td>` +
            `<td>${recordLink(it.product_lot)}</td>` +
            "</tr>"
          );
        })
        .join("");
    }

    async function load() {
      if (!request) return;
      try {
        const data = await request("/blend/lot-audit/unresolved");
        renderUnresolved(data || {});
      } catch (err) {
        $("lau-unresolved-loading").hidden = true;
        $("lau-unresolved-empty").hidden = false;
        $("lau-unresolved-empty").textContent = "미해소 LOT 목록을 불러오지 못했습니다.";
        notify("미해소 LOT 목록을 불러오지 못했습니다.", "error");
      }
      try {
        const data = await request("/blend/lot-audit/total-anomalies");
        renderAnomalies(data || {});
      } catch (err) {
        $("lau-anomaly-loading").hidden = true;
        $("lau-anomaly-empty").hidden = false;
        $("lau-anomaly-empty").textContent = "총량 이상 목록을 불러오지 못했습니다.";
        notify("총량 이상 목록을 불러오지 못했습니다.", "error");
      }
    }

    load();
  });
})();
