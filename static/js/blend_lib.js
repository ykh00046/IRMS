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
 *   esc, TOLERANCE_G, ANCHOR_BADGE, fmt, todayISO, nowTime, rowVariance,
 *   baseTotalValues, materialRowHtml, baseTotalLinksHtml, bulkRowHtml,
 *   computeTotals, computeTheoryAmount,
 *   varianceDisplay(it, toleranceG?), varianceWarnMessage(it, v, toleranceG?),
 *   badVarianceNames(bad), varianceBlockMessage(names, toleranceG?),
 *   option, stepRowsHtml, lotFallbackText,
 *   findAnchorIndex, computeAnchorTheory
 *
 * variance* 헬퍼는 레시피별 허용 편차(toleranceG) 를 인자로 받는다. 미지정 시
 * 기본값 TOLERANCE_G(0.05) 로 폴백 — 레시피 편차가 없는 기존 동작 보존.
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

  // 기준 자재 행에 붙는 안내 배지 문구 — 배합 시 이 자재를 먼저 계량함을 표시.
  const ANCHOR_BADGE = "기준 · 먼저 계량";

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

  function materialRowHtml(idx, it, opts) {
    // opts (선택):
    //   anchor         (bool) 이 행이 기준 자재 — 이름 옆에 안내 배지 표시
    //   disableActual  (bool) 기준 자재 실측값 입력 전 — 이 행 실제량 입력 비활성화
    const o = opts || {};
    const nameCell = o.anchor
      ? `<td>${esc(it.material_name)} <span class="blend-anchor-badge">${esc(ANCHOR_BADGE)}</span></td>`
      : `<td>${esc(it.material_name)}</td>`;
    const actualAttr = o.disableActual ? " disabled" : "";
    return `<td>${idx + 1}</td>` +
      nameCell +
      `<td class="num">${fmt(it.ratio, 2)}</td>` +
      `<td class="num blend-theory" data-idx="${idx}">${fmt(it.theory_amount)}</td>` +
      `<td><input class="input blend-lot" data-idx="${idx}" value="${esc(it.material_lot)}" placeholder="LOT" /></td>` +
      `<td class="num"><input class="input blend-actual" data-idx="${idx}" type="number" step="any" min="0" value="${esc(it.actual_amount)}" placeholder="${it.theory_amount == null ? "" : fmt(it.theory_amount)}"${actualAttr} /></td>` +
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

  // 기준 자재(anchor) 행 인덱스. is_anchor 가 true 인 첫 행, 없으면 -1.
  // 순수 함수 — items 배열만 받아 인덱스(정수)를 반환한다.
  function findAnchorIndex(items) {
    if (!Array.isArray(items)) return -1;
    const i = items.findIndex((it) => it && it.is_anchor);
    return i >= 0 ? i : -1;
  }

  // 기준 자재 우선 계량 모드의 이론량·총량 재계산(순수).
  // 각 비기준 자재의 이론량 = round(anchorActual * (해당 value_weight / 기준 value_weight) * 100) / 100,
  // 기준 자재 이론량 = anchorActual(실측값이 곧 이론값),
  // 도출 총량 = round(모든 행 이론량 합계, 2).
  // anchorActual 이 0 이하이거나 기준 자재 value_weight 이 0 이하면 빈 결과(null) 반환 —
  // 이 경우 blend.js 는 이론량을 모두 null(표시 '-')로 둔다.
  // 반환: { theoryAmounts: (number|null)[] , total: number } — total 은 도출 총량.
  function computeAnchorTheory(items, anchorIndex, anchorActual) {
    const n = Array.isArray(items) ? items.length : 0;
    const out = new Array(n).fill(null);
    if (anchorIndex < 0 || anchorIndex >= n) return { theoryAmounts: out, total: 0 };
    const a = Number(anchorActual);
    if (!(a > 0)) return { theoryAmounts: out, total: 0 };
    const anchorW = Number(items[anchorIndex].value_weight);
    if (!(anchorW > 0)) return { theoryAmounts: out, total: 0 };
    let total = 0;
    for (let i = 0; i < n; i++) {
      if (i === anchorIndex) {
        out[i] = Math.round(a * 100) / 100;
      } else {
        const w = Number(items[i] && items[i].value_weight);
        out[i] = Math.round((a * (w / anchorW)) * 100) / 100;
      }
      total += (out[i] || 0);
    }
    return { theoryAmounts: out, total: Math.round(total * 100) / 100 };
  }

  function varianceDisplay(it, toleranceG) {
    // 기준 자재 행은 편차 계량에서 제외 — 항상 '-' 표시(이론=실측이므로 편차 무의미).
    if (it && it.is_anchor) {
      return { text: "-", className: "num blend-var" };
    }
    const actual = it.actual_amount === "" ? null : Number(it.actual_amount);
    if (actual === null || it.theory_amount === null) {
      return { text: "-", className: "num blend-var" };
    }
    const tol = Number.isFinite(Number(toleranceG)) && Number(toleranceG) > 0
      ? Number(toleranceG) : TOLERANCE_G;
    const v = Math.round((actual - it.theory_amount) * 1000) / 1000;
    return {
      text: (v > 0 ? "+" : "") + fmt(v, 2),
      // 허용 편차(±tol g) 이내면 정상 표시, 초과 시에만 색으로 경고
      className: "num blend-var " + (Math.abs(v) <= tol + 1e-9 ? "" : v > 0 ? "var-up" : "var-down"),
    };
  }

  function varianceWarnMessage(it, v, toleranceG) {
    const tol = Number.isFinite(Number(toleranceG)) && Number(toleranceG) > 0
      ? Number(toleranceG) : TOLERANCE_G;
    return `허용 편차 초과: ${it.material_name} — 이론 ${fmt(it.theory_amount)} / 실제 ${fmt(it.actual_amount)} `
      + `(편차 ${v > 0 ? "+" : ""}${fmt(v, 2)}g > ±${tol}g). 다시 계량하세요.`;
  }

  function badVarianceNames(bad) {
    return bad.map((it) => {
      const v = rowVariance(it);
      return `${it.material_name}(${v > 0 ? "+" : ""}${fmt(v, 2)}g)`;
    }).join(", ");
  }

  function varianceBlockMessage(names, toleranceG) {
    const tol = Number.isFinite(Number(toleranceG)) && Number(toleranceG) > 0
      ? Number(toleranceG) : TOLERANCE_G;
    return `허용 편차(±${tol}g)를 초과해 저장할 수 없습니다: ${names}. 해당 자재를 다시 계량하세요.`;
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
    ANCHOR_BADGE,
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
    findAnchorIndex,
    computeAnchorTheory,
  };
})();
