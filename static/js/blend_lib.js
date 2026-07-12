/**
 * blend_lib.js — 배합 화면 순수 헬퍼 라이브러리.
 *
 * blend.js 컨트롤러에서 분리된 포맷터·HTML 문자열 빌더·수치 계산 헬퍼.
 * 모든 멤버는 클로저 바인딩(state, $, request, notify, DOM 조회, 타이머,
 * 저울 에이전트 연결 변수)을 참조하지 않는다 — 인자를 받아 값을 반환한다.
 * document.createElement 로 새 노드를 만드는 element-factory 만 DOM 접근을
 * 허용하며, 조회(document.getElementById/querySelector)는 하지 않는다.
 *
 * Exports (window.IRMS.blendLib):
 *   esc, TOLERANCE_G, fmt, todayISO, nowTime, rowVariance,
 *   baseTotalValues, materialRowHtml, baseTotalLinksHtml, bulkRowHtml,
 *   computeTotals, computeTheoryAmount, varianceDisplay,
 *   varianceWarnMessage, badVarianceNames, varianceBlockMessage,
 *   option, stepRowsHtml, lotFallbackText
 *
 * Side effects: none (window.IRMS.blendLib 에 부착만).
 * Dependencies: window.IRMS namespace (common/core.js 초기화).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};

  const esc = IRMS.escapeHtml || function (value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

  // 자재별 계량 허용 편차(g). 저울 실측 연동 기준 — 서버(blend_service)와 동일 값.
  const TOLERANCE_G = 0.05;

  function fmt(v, d) {
    if (v === null || v === undefined || v === "") return "-";
    // 기본 소수 2자리 — 저울(XP 0.01g) 해상도에 맞춤
    return Number(v).toFixed(d === undefined ? 2 : d);
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

  function rowVariance(it) {
    if (!it || it.actual_amount === "" || it.theory_amount == null) return 0;
    return Math.round((Number(it.actual_amount) - it.theory_amount) * 1000) / 1000;
  }

  function baseTotalValues(current) {
    if (!current) return [];
    const list = Array.isArray(current.default_totals) ? current.default_totals : [];
    return list.filter((v) => Number(v) > 0);
  }

  function materialRowHtml(idx, it) {
    return `<td>${idx + 1}</td>` +
      `<td>${esc(it.material_name)}</td>` +
      `<td class="num">${fmt(it.ratio, 2)}</td>` +
      `<td class="num blend-theory" data-idx="${idx}">${fmt(it.theory_amount)}</td>` +
      `<td><input class="input blend-lot" data-idx="${idx}" value="${esc(it.material_lot)}" placeholder="LOT" /></td>` +
      `<td class="num"><input class="input blend-actual" data-idx="${idx}" type="number" step="any" min="0" value="${esc(it.actual_amount)}" placeholder="${it.theory_amount == null ? "" : fmt(it.theory_amount)}" /></td>` +
      `<td class="num blend-var" data-idx="${idx}">-</td>`;
  }

  // 1개면 '기준량 N 적용', 여러 개면 '기준량' 라벨 + 압축 표기 값 버튼들
  function baseTotalLinksHtml(values) {
    if (!values.length) return "";
    const short = (v) => String(Number(v));  // 2000.00 → 2000 (라벨 줄 한 줄 유지)
    const label = values.length === 1 ? "" : '<span class="blend-base-label">기준량</span>';
    return label + values.map((v) =>
      `<button class="blend-base-link" type="button" data-value="${v}" ` +
      `title="총 배합량에 ${fmt(v)} g 을 채웁니다">` +
      `${values.length === 1 ? `기준량 ${short(v)} 적용` : short(v)}</button>`
    ).join("");
  }

  function bulkRowHtml() {
    return `<td><input class="input bulk-date" type="date" value="${todayISO()}" /></td>` +
      `<td class="num"><input class="input bulk-total" type="number" step="0.1" min="0" /></td>` +
      `<td><button class="btn btn-sm bulk-del" type="button">삭제</button></td>`;
  }

  function computeTotals(items) {
    const theory = items.reduce((s, it) => s + (it.theory_amount || 0), 0);
    const actual = items.reduce((s, it) => s + (it.actual_amount === "" ? 0 : Number(it.actual_amount) || 0), 0);
    return { theory, actual, net: actual - theory };
  }

  function computeTheoryAmount(ratio, total) {
    // 이론량을 저울/표시 단위(0.01g)로 반올림. 표시값=내부값이라 표시된 이론값을
    // 그대로 계량하면 편차 0. 허용 편차(±0.05g) 판정과도 같은 눈금.
    return Math.round((ratio / 100) * total * 100) / 100;
  }

  function varianceDisplay(it) {
    const actual = it.actual_amount === "" ? null : Number(it.actual_amount);
    if (actual === null || it.theory_amount === null) {
      return { text: "-", className: "num blend-var" };
    }
    const v = Math.round((actual - it.theory_amount) * 1000) / 1000;
    return {
      text: (v > 0 ? "+" : "") + fmt(v, 2),
      // 허용 편차(±0.05g) 이내면 정상 표시, 초과 시에만 색으로 경고
      className: "num blend-var " + (Math.abs(v) <= TOLERANCE_G + 1e-9 ? "" : v > 0 ? "var-up" : "var-down"),
    };
  }

  function varianceWarnMessage(it, v) {
    return `허용 편차 초과: ${it.material_name} — 이론 ${fmt(it.theory_amount)} / 실제 ${fmt(it.actual_amount)} `
      + `(편차 ${v > 0 ? "+" : ""}${fmt(v, 2)}g > ±${TOLERANCE_G}g). 다시 계량하세요.`;
  }

  function badVarianceNames(bad) {
    return bad.map((it) => {
      const v = rowVariance(it);
      return `${it.material_name}(${v > 0 ? "+" : ""}${fmt(v, 2)}g)`;
    }).join(", ");
  }

  function varianceBlockMessage(names) {
    return `허용 편차(±${TOLERANCE_G}g)를 초과해 저장할 수 없습니다: ${names}. 해당 자재를 다시 계량하세요.`;
  }

  function option(value, label) {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = label;
    return item;
  }

  function stepRowsHtml(steps, position) {
    return steps
      .filter((st) => st.position === position)
      .map((st) => `<tr class="blend-step-row"><td colspan="7">▸ ${esc(st.note)}</td></tr>`)
      .join("");
  }

  function lotFallbackText(product, date) {
    return `${product}${date.replace(/-/g, "").slice(2, 8)}`;
  }

  // 일괄 생성 레시피 <select> 의 option HTML 을 조립. DHR 전용 토글 여부로
  // 빈 목록 안내 문구가 달라진다. 동일 items/dhr 에 대해 동일 HTML 반환.
  function recipeOptionsHtml(items, dhr) {
    const ph = items.length ? "레시피 선택…" : (dhr ? "DHR 전용 레시피가 없습니다" : "레시피가 없습니다");
    const opts = items.map((r) =>
      `<option value="${esc(r.id)}">${esc(r.product_name)}</option>`
    ).join("");
    return `<option value="">${ph}</option>${opts}`;
  }

  function loadFailOptionHtml() {
    return '<option value="">로드 실패</option>';
  }

  IRMS.blendLib = {
    esc,
    TOLERANCE_G,
    fmt,
    todayISO,
    nowTime,
    rowVariance,
    baseTotalValues,
    materialRowHtml,
    baseTotalLinksHtml,
    bulkRowHtml,
    computeTotals,
    computeTheoryAmount,
    varianceDisplay,
    varianceWarnMessage,
    badVarianceNames,
    varianceBlockMessage,
    option,
    stepRowsHtml,
    lotFallbackText,
    recipeOptionsHtml,
    loadFailOptionHtml,
  };
})();
