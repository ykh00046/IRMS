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
 *   esc, TOLERANCE_G, ANCHOR_BADGE, fmt, toleranceDecimals, todayISO, nowTime, rowVariance,
 *   baseTotalValues, materialRowHtml, baseTotalLinksHtml, bulkRowHtml,
 *   computeTotals, computeTheoryAmount,
 *   varianceDisplay(it, toleranceG?), varianceWarnMessage(it, v, toleranceG?),
 *   badVarianceNames(bad), varianceBlockMessage(names, toleranceG?),
 *   option, stepRowsHtml, lotFallbackText,
 *   findAnchorIndex, computeAnchorTheory, theoryFromWeights,
 *   BATCH_LIMIT_G, requiredTotalForRow, rescalePlan, exceedsBatchLimit,
 *   IDLE_LOGOUT_MINUTES, createIdleLogout
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

  // 계량값 표시 소수 자릿수 — 레시피 허용 편차(tolerance_g)의 소수 자릿수를 따른다.
  // 허용 편차가 큰 레시피는 저울 해상도(0.1g 등)나 대용량 배치 때문이라, 저울이 못 찍는
  // 소수(4775.72)를 목표로 보여주는 게 부자연스럽다는 현장 판단(2026-07-24).
  //   null/undefined/NaN → 2 (기본값 0.05 와 동일 — 기존 동작 보존)
  //   0.05 → 2, 0.1/0.5 → 1, 1 이상 정수 → 0, 2.5 → 1
  // 표시 전용 — 계산·검증·저장은 이 값과 무관하게 서버 기준(2자리 이론)을 유지한다.
  function toleranceDecimals(tolG) {
    const n = Number(tolG);
    if (tolG === null || tolG === undefined || !Number.isFinite(n)) return 2;
    // 최소 표기(String(n))의 소수 자릿수를 센다: "0.05"→2, "0.1"→1, "1"→0, "2.5"→1.
    const s = String(n);
    const dot = s.indexOf(".");
    return dot < 0 ? 0 : s.length - dot - 1;
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

  // 1개면 '기준 N 적용', 여러 개면 '기준' 라벨 + 압축 표기 값 버튼들
  function baseTotalLinksHtml(values) {
    if (!values.length) return "";
    const short = (v) => String(Number(v));  // 2000.00 → 2000 (라벨 줄 한 줄 유지)
    const label = values.length === 1 ? "" : '<span class="blend-base-label">기준</span>';
    return label + values.map((v) =>
      `<button class="blend-base-link" type="button" data-value="${v}" ` +
      `title="총 배합량에 ${fmt(v)} g 을 채웁니다">` +
      `${values.length === 1 ? `기준 ${short(v)} 적용` : short(v)}</button>`
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

  // 총량 입력에 따른 이론량 재계산(순수) — value_weight 비례 방식.
  // 서버(blend_service.scale_theory)와 동일 산술: theory_i = value_weight_i / base_sum × total.
  // 이 경로는 서버가 내려준 반올림된 ratio(4자리) 대신 원값(value_weight) 으로
  // 계산해 57.99 같은 꼬리를 없앤다. 반환값은 3자리 반올림(증량 rescalePlan 과 동일 단위).
  //
  // items: [{value_weight}], total: 총 배합량.
  // 반환: 각 항목의 round(value_weight/base_sum×total, 2) 배열.
  //   - total 이 유효 숫자가 아니거나 0 이하 → 전체 null 배열(호출부 ratio 방식 폴백).
  //   - 어느 한 항목이라도 value_weight 이 null/undefined → 전체 null 배열(옛 데이터 호환 폴백).
  //     (<=0 인 항목은 base_sum 에서 0 기여할 뿐 폴백 유발은 아님.)
  //   - base_sum(Σ value_weight>0) 이 0 이하 → 전체 null 배열.
  // null 배열을 받은 호출부는 기존 computeTheoryAmount(ratio, total) 로 폴백하면 된다.
  function theoryFromWeights(items, total) {
    const list = Array.isArray(items) ? items : [];
    const out = new Array(list.length).fill(null);
    const t = Number(total);
    if (!Number.isFinite(t) || !(t > 0)) return out;
    for (let i = 0; i < list.length; i++) {
      const it = list[i] || {};
      if (it.value_weight === null || it.value_weight === undefined) return out;
    }
    let baseSum = 0;
    for (let i = 0; i < list.length; i++) {
      const w = Number(list[i] && list[i].value_weight);
      if (w > 0) baseSum += w;
    }
    if (!(baseSum > 0)) return out;
    for (let i = 0; i < list.length; i++) {
      const w = Number(list[i].value_weight);
      // 저울 해상도(2자리)에 맞춰 반올림 — 3자리 목표는 저울로 맞출 수 없다.
      out[i] = Math.round((w / baseSum) * t * 100) / 100;
    }
    return out;
  }

  // 초과 계량 증량(rescale) 상한 — 1회 배합 허용 최대 총량(g).
  // 초과 시 현장 폐기 권장. 서버(blend_service) 총량 제약과 무관한 UI 전용 상수.
  const BATCH_LIMIT_G = 25000;

  // 단일 자재의 초과 계량 시 도출 필요 총량.
  // required_i = actual × 100 / ratio — ratio_i 비율로 actual_i 만 넣었다면
  // 배합 전체 총량이 이 값이어야 한다는 뜻. ratio<=0 또는 actual 이 유효 숫자가
  // 아니면(null/빈문자/음수/0) null 반환(증량 계산에서 제외).
  function requiredTotalForRow(ratio, actual) {
    const r = Number(ratio);
    const a = Number(actual);
    if (!(r > 0) || !Number.isFinite(a) || a <= 0) return null;
    return a * 100 / r;
  }

  // 초과 계량 증량 계획 수립(순수). items: [{ratio, actual_amount, theory_amount}],
  // currentTotal: 현재 배합 총량, toleranceG: 허용 편차.
  // 반환: { newTotal, changed, rows: [{idx, newTheory, addNeeded}] }
  //   - newTotal = max(currentTotal, '목표를 허용 편차 이상 초과한' 계량 행의 required)
  //     ※ 목표(=비율×현재총량) 대비 tol 이내의 편차는 총량을 바꾸지 않는다 — 편차는
  //       정해진 총량 안에서 흡수된다("편차가 있다고 총 배합량이 바뀌면 안 된다").
  //       이 게이팅이 없으면 '실제>목표'(0.001g만 넘어도) 이면 required>현재총량 이 되어
  //       미세 편차가 ×100/비율 로 증폭돼 총량을 밀어 올리고, 작은 비율 자재를 먼저
  //       계량하면 다른 행 목표가 흔들린다(계량 순서 의존 버그). tol 게이팅으로 이를 없앤다.
  //   - changed = newTotal 이 currentTotal 보다 유의미하게 큰가(> 1e-9)
  //   - 모든 반올림은 저울 해상도(2자리)에 맞춘다 — 3자리 목표는 저울로 맞출 수 없다.
  //   - rows: 모든 행의 newTheory(round(ratio×newTotal/100, 2)) + 계량 행 addNeeded(max(0,newTheory−actual))
  //     미계량 행은 addNeeded=null.
  function rescalePlan(items, currentTotal, toleranceG) {
    const list = Array.isArray(items) ? items : [];
    const base = Number(currentTotal);
    const baseTotal = Number.isFinite(base) && base >= 0 ? base : 0;
    const tol = Number.isFinite(Number(toleranceG)) && Number(toleranceG) > 0
      ? Number(toleranceG) : TOLERANCE_G;
    let newTotal = baseTotal;
    for (let i = 0; i < list.length; i++) {
      const it = list[i] || {};
      if (it.actual_amount === "" || it.actual_amount === null || it.actual_amount === undefined) continue;
      const r = Number(it.ratio);
      if (!(r > 0)) continue;
      const a = Number(it.actual_amount);
      if (!Number.isFinite(a) || a <= 0) continue;
      // 이 행의 현재 목표(=비율×현재총량). 저장된 theory_amount 가 있으면 그것을 쓴다
      // (작업자가 보는 목표와 동일 기준으로 편차를 판정).
      const th = Number(it.theory_amount);
      const currentTheory = Number.isFinite(th) ? th : (r / 100) * baseTotal;
      // 목표를 허용 편차 이내로만 넘었으면 총량 불변(편차 흡수). 그 이상 초과해야 증량.
      if (a - currentTheory <= tol + 1e-9) continue;
      const required = a * 100 / r;
      if (required > newTotal) newTotal = required;
    }
    newTotal = Math.round(newTotal * 100) / 100;  // 저울 해상도(2자리)
    const changed = newTotal - baseTotal > 1e-9;
    const rows = list.map((it, idx) => {
      const item = it || {};
      const r = Number(item.ratio);
      const newTheory = r > 0 ? Math.round((r / 100) * newTotal * 100) / 100 : null;
      let addNeeded = null;
      const actualRaw = item.actual_amount;
      if (actualRaw !== "" && actualRaw !== null && actualRaw !== undefined) {
        const a = Number(actualRaw);
        if (Number.isFinite(a)) {
          addNeeded = newTheory !== null ? Math.max(0, Math.round((newTheory - a) * 100) / 100) : 0;
        }
      }
      return { idx, newTheory, addNeeded };
    });
    return { newTotal, changed, rows };
  }

  // 배합 총량이 1회 허용 상한(25,000g)을 초과하는가 — 증량 후 폐기 권장 모달 판정용.
  function exceedsBatchLimit(total) {
    return Number(total) > BATCH_LIMIT_G;
  }

  // 저울 PRINT 가 들어갈 행 우선순위 — 값이 다른 품목으로 새지 않게 하는 규칙.
  //
  // 이 판정이 틀리면 사람이 저울로 잰 값이 엉뚱한 자재에 기록된다. 같은 사고가
  // 두 번 났다(2026-07-22 인라인 추가칸, 2026-08-03 부족 보충 2회차) — 두 번 다
  // "그 행은 이미 채워져 있으니 폴백이 다음 빈 행을 골랐다"가 원인이라, 규칙만
  // 순수 함수로 떼어 테스트로 잠근다.
  //
  // 우선순위(위가 강함):
  //   addModeIdx   — 합산 입력이 실제로 켜져 있는 행
  //   addWeighIdx  — 추가 계량/나눠 담기 모달이 열려 있는 행. applyAddAmount 가 매번
  //                  addModeIdx 를 null 로 되돌리므로, 이게 없으면 2회차 PRINT 부터 샌다.
  //   shortageIdx  — 부족 모달이 떠 있는 행. 아직 '추가로 채우기'를 안 눌러 합산 모드가
  //                  아니지만, 모달이 "저울 PRINT 가 합산된다"고 약속한 상태다.
  //   stickyIdx    — 작업자가 지정한 행(유효할 때만) / focusedIdx — 커서가 놓인 행
  // 전부 해당 없으면 null → 호출부가 '첫 미입력 행' 폴백을 쓴다.
  function pickScaleRow(ctx) {
    const c = ctx || {};
    if (c.addModeIdx != null) return c.addModeIdx;
    if (c.addWeighIdx != null) return c.addWeighIdx;
    if (c.shortageIdx != null) return c.shortageIdx;
    if (c.stickyIdx != null && c.stickyValid) return c.stickyIdx;
    if (c.focusedIdx != null) return c.focusedIdx;
    return null;
  }

  // 이 행의 PRINT 를 기존 값에 합산해야 하는가(덮어쓰기 금지).
  // 모달이 열려 있는 동안은 addModeIdx 가 회차마다 꺼지므로 addWeighIdx 도 함께 본다.
  function isAddModeRow(idx, addModeIdx, addWeighIdx) {
    return addModeIdx === idx || (addWeighIdx != null && addWeighIdx === idx);
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

  // 자재 LOT 누락 판정(순수) — 실제량(actual) 이 들어있는데 material_lot 가
  // 비어있는 행을 찾는다. 미등록 LOT '사유 적고 진행'(override) 은 별도 상태이므로
  // 여기서는 다루지 않는다(사유가 있으면 lot 가 채워진 것으로 친다).
  // rows: [{material_name, actual_amount, material_lot}] — lot 가 빈 행의 material_name 반환.
  function missingLotNames(rows) {
    const list = Array.isArray(rows) ? rows : [];
    const missing = [];
    for (const it of list) {
      const actual = it.actual_amount;
      const hasActual = actual !== "" && actual !== null && actual !== undefined && Number(actual) > 0;
      if (!hasActual) continue;
      const lot = String(it.material_lot || "").trim();
      if (lot === "") missing.push(String(it.material_name || "").trim() || "(이름 없음)");
    }
    return missing;
  }

  // 자재 LOT 누락 알림 문구(순수). names 가 비어있지 않으면 차단 메시지 반환.
  function missingLotBlockMessage(names) {
    if (!names || !names.length) return "";
    const shown = names.slice(0, 6);
    const suffix = names.length > 6 ? " …" : "";
    return `자재 LOT 를 입력하세요: ${shown.join(", ")}${suffix} — 실제량을 넣은 자재는 LOT 도 반드시 입력하세요.`;
  }

  // 증량 적용 요약 행(순수 HTML 문자열). plan 은 rescalePlan 반환값.
  // 각 행: "자재명 +XXX.Xg (목표 YYY.Yg)" — addNeeded>0 인 행만.
  function appliedRescaleRowHtml(name, item) {
    const add = Number(item.addNeeded);
    const goal = Number(item.newTheory);
    const addTxt = (add > 0 ? "+" : "") + fmt(add, 1);
    return `<span class="rescale-applied-item">${esc(name)} ${esc(addTxt)}g (목표 ${fmt(goal, 1)}g)</span>`;
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

  // ── 활동 기반 유휴 자동 로그아웃 ────────────────────────────────
  // 배합/다중 계량 화면에서 마우스·키보드·터치 등 활동이 이 시간(분) 동안
  // 전혀 없으면 자동 로그아웃한다. 공용 PC 보안 조치.
  //   ※ 60분 값을 바꾸려면 이 상수만 고치면 된다.
  const IDLE_LOGOUT_MINUTES = 60;

  // 활동 기반 유휴 로그아웃 컨트롤러(순수 팩토리). 클로저 상태를 자체 보관하되
  // 화면별 동작(초안 저장·로그아웃 요청·이동)은 opts 콜백으로 주입받는다.
  // 서버 요청 기반 8h 유휴(blend_session.py)·저장 후 5분 로그아웃과는 독립적으로
  // 동작한다 — 셋 중 먼저 만료되는 쪽이 이긴다.
  //
  // opts:
  //   isActive()   : () => boolean  — 작업자 세션 활성(로그인) 여부. arm/만료 게이트.
  //   saveDraft()  : () => void     — 만료 직전 최종 초안 저장(동기 flush 권장).
  //   request(path, init) : IRMS._core.request — 로그아웃 POST 용.
  //   notify(msg, level)  : IRMS.notify — 사전 경고 토스트.
  //   redirectTo   : 만료 후 이동 경로(기본 "/").
  //   logoutPath   : 로그아웃 엔드포인트(기본 "/blend/session/logout").
  //   minutes      : 유휴 임계(분, 기본 IDLE_LOGOUT_MINUTES).
  //   warnBeforeMs : 만료 몇 ms 전에 경고할지(기본 60000 = 1분).
  //   checkIntervalMs : 유휴 점검 주기(기본 25000).
  //   throttleMs   : 활동 기록 스로틀(기본 3000 — mousemove 폭주 방지).
  //   warnMessage  : 경고 문구.
  // 반환: { arm, disarm, reset, note }.
  function createIdleLogout(opts) {
    const o = opts || {};
    const minutes = Number.isFinite(Number(o.minutes)) && Number(o.minutes) > 0
      ? Number(o.minutes) : IDLE_LOGOUT_MINUTES;
    const idleMs = minutes * 60 * 1000;
    const warnBeforeMs = Number.isFinite(Number(o.warnBeforeMs)) ? Number(o.warnBeforeMs) : 60 * 1000;
    const checkIntervalMs = Number.isFinite(Number(o.checkIntervalMs)) && Number(o.checkIntervalMs) > 0
      ? Number(o.checkIntervalMs) : 25 * 1000;
    const throttleMs = Number.isFinite(Number(o.throttleMs)) && Number(o.throttleMs) >= 0
      ? Number(o.throttleMs) : 3 * 1000;
    const redirectTo = o.redirectTo || "/";
    const logoutPath = o.logoutPath || "/blend/session/logout";
    const warnMessage = o.warnMessage
      || "약 1분간 활동이 없으면 자동 로그아웃됩니다 — 화면을 움직이면 유지됩니다.";
    const isActive = typeof o.isActive === "function" ? o.isActive : () => true;
    const saveDraft = typeof o.saveDraft === "function" ? o.saveDraft : () => {};
    const request = typeof o.request === "function" ? o.request : null;
    const notify = typeof o.notify === "function" ? o.notify : () => {};

    // passive 로 붙여도 되는 이벤트(스크롤 성능): 어느 것도 preventDefault 하지 않는다.
    const ACTIVITY_EVENTS = ["mousemove", "mousedown", "keydown", "touchstart", "wheel", "input"];

    let lastActivity = Date.now();
    let warned = false;
    let armed = false;
    let expired = false;
    let intervalId = null;
    let onActivity = null;

    function reset() {
      lastActivity = Date.now();
      warned = false;
    }

    function tick() {
      if (expired) return;
      if (!isActive()) return;  // 로그인 화면 등 세션 없음 — 자동 로그아웃하지 않음
      const idle = Date.now() - lastActivity;
      if (idle >= idleMs) {
        expire();
      } else if (idle >= idleMs - warnBeforeMs && !warned) {
        warned = true;  // 접근당 1회만 — 활동이 있으면 reset 에서 다시 false 로 풀린다
        try { notify(warnMessage, "warn"); } catch (_e) { /* 무시 */ }
      }
    }

    async function expire() {
      if (expired) return;
      expired = true;
      disarm();
      try { saveDraft(); } catch (_e) { /* 초안 저장 실패해도 로그아웃은 진행 */ }
      if (request) {
        try { await request(logoutPath, { method: "POST" }); } catch (_e) { /* 만료 등 무시 */ }
      }
      window.location.href = redirectTo;
    }

    function arm() {
      if (armed) return;          // 중복 arm 방지
      if (!isActive()) return;    // 세션 없으면 무장하지 않음
      armed = true;
      expired = false;
      reset();
      // 스로틀된 활동 기록 — mousemove 가 lastActivity 를 매 프레임 갱신하지 않도록.
      onActivity = function () {
        const now = Date.now();
        if (now - lastActivity >= throttleMs) {
          lastActivity = now;
        }
        warned = false;  // 활동이 있으면 다음 접근 때 경고를 다시 띄운다
      };
      ACTIVITY_EVENTS.forEach((ev) => {
        document.addEventListener(ev, onActivity, { passive: true, capture: true });
      });
      intervalId = setInterval(tick, checkIntervalMs);
    }

    function disarm() {
      if (intervalId) { clearInterval(intervalId); intervalId = null; }
      if (onActivity) {
        ACTIVITY_EVENTS.forEach((ev) => {
          document.removeEventListener(ev, onActivity, { capture: true });
        });
        onActivity = null;
      }
      armed = false;
    }

    return { arm, disarm, reset };
  }

  IRMS.blendLib = {
    esc,
    TOLERANCE_G,
    ANCHOR_BADGE,
    IDLE_LOGOUT_MINUTES,
    createIdleLogout,
    fmt,
    toleranceDecimals,
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
    missingLotNames,
    missingLotBlockMessage,
    appliedRescaleRowHtml,
    option,
    stepRowsHtml,
    lotFallbackText,
    recipeOptionsHtml,
    loadFailOptionHtml,
    findAnchorIndex,
    computeAnchorTheory,
    theoryFromWeights,
    BATCH_LIMIT_G,
    requiredTotalForRow,
    rescalePlan,
    exceedsBatchLimit,
    pickScaleRow,
    isAddModeRow,
  };
})();
