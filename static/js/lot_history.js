/**
 * lot_history.js — 자재 LOT 이력 (/lot-history).
 *
 * "이 레시피의 이 자재는 언제 어떤 LOT 으로 바뀌었나"를 답하는 화면. 기록을 하나씩 열어
 * 비교하던 일을 대신한다.
 *
 * 데이터(전부 조회 전용):
 *   GET /api/blend/lot-history/families   레시피 가족(개정 계보 묶음) + 자재 선택지
 *   GET /api/blend/lot-history            타임라인(구간) + 교체 사건 — family 또는 material 필수
 *   GET /api/blend/lot-history/export     같은 내용 Excel
 *   GET /api/blend/material-lot-trace     역추적(배합 분석에서 이동, API 는 그대로)
 *
 * 타임라인 막대는 결과 전체가 같은 시간축을 쓴다 — 자재끼리 교체 시점이 세로로 맞아야
 * "그날 여러 자재가 같이 바뀌었다"가 보인다. 구간 i 는 [first_i, first_{i+1}) 를 차지하고
 * 마지막 구간은 축 끝까지 이어진다(현재 쓰는 LOT).
 */
(function () {
  "use strict";

  const win = typeof window !== "undefined" ? window : {};
  const IRMS = (win.IRMS = win.IRMS || {});

  // ── 순수 헬퍼(테스트 대상) ─────────────────────────────────────────────
  function dayNum(iso) {
    const t = Date.parse(String(iso || "").slice(0, 10) + "T00:00:00Z");
    return Number.isFinite(t) ? Math.round(t / 86400000) : null;
  }

  // 결과 행 전체에서 축의 양 끝 날짜. 조건에 기간이 있으면 그것을 우선한다.
  function axisRange(rows, startDate, endDate) {
    let lo = null;
    let hi = null;
    (rows || []).forEach((row) => {
      (row.segments || []).forEach((seg) => {
        const a = dayNum(seg.first_date);
        const b = dayNum(seg.last_date);
        if (a !== null && (lo === null || a < lo)) lo = a;
        if (b !== null && (hi === null || b > hi)) hi = b;
      });
    });
    const s = dayNum(startDate);
    const e = dayNum(endDate);
    if (s !== null && lo !== null) lo = Math.max(lo, s);
    if (e !== null && hi !== null) hi = Math.min(hi, e);
    if (lo === null || hi === null) return null;
    if (hi <= lo) hi = lo + 1;
    return { lo, hi };
  }

  // 구간별 left/width(%) — 마지막 구간은 축 끝까지. 축 밖은 잘라 낸다.
  function layoutSegments(segments, axis) {
    const span = axis.hi - axis.lo;
    return (segments || []).map((seg, i, all) => {
      const start = dayNum(seg.first_date);
      const next = i + 1 < all.length ? dayNum(all[i + 1].first_date) : axis.hi;
      const a = Math.max(axis.lo, Math.min(axis.hi, start === null ? axis.lo : start));
      const b = Math.max(a, Math.min(axis.hi, next === null ? axis.hi : next));
      return {
        left: ((a - axis.lo) / span) * 100,
        width: Math.max(((b - a) / span) * 100, 0),
      };
    });
  }

  IRMS.lotHistory = { dayNum, axisRange, layoutSegments };

  if (typeof document === "undefined" || !document.addEventListener) return;

  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  const num = (v, d = 0) =>
    v === null || v === undefined || v === ""
      ? "-"
      : Number(v).toLocaleString("ko-KR", {
        minimumFractionDigits: d, maximumFractionDigits: d,
      });
  const emptyRow = (cols, text) => `<tr><td colspan="${cols}" class="muted">${esc(text)}</td></tr>`;
  const lotLink = (lot) =>
    `<a class="lh-lot-link" href="/status?search=${encodeURIComponent(lot)}">${esc(lot)}</a>`;

  function localISO(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  function daysAgo(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return localISO(d);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const request = IRMS._core && IRMS._core.request;
    const notify = IRMS.notify || (() => {});
    if (!request) {
      console.error("IRMS core not loaded");
      return;
    }

    let families = [];        // /families 응답 items
    let allMaterials = [];    // /families 응답 materials
    let state = null;         // 마지막 /lot-history 응답

    // ── 선택지 ──────────────────────────────────────────────────────────
    function fillSelect(select, items, placeholder, selected) {
      const keep = selected != null ? selected : select.value;
      select.innerHTML = `<option value="">${esc(placeholder)}</option>` + items.map((it) =>
        `<option value="${esc(it.key)}">${esc(it.label)}</option>`).join("");
      // 이전 선택이 새 목록에 없으면 전체로 돌아간다.
      select.value = items.some((it) => it.key === keep) ? keep : "";
    }

    function refreshMaterials(selectedKey) {
      const famKey = $("lh-family").value;
      const fam = families.find((f) => f.key === famKey);
      const items = (fam ? fam.materials : allMaterials).map((m) => ({ key: m.key, label: m.name }));
      fillSelect($("lh-material"), items, "(전체 자재)", selectedKey);
    }

    async function loadFamilies(initial) {
      const d = await request("/blend/lot-history/families");
      families = d.items || [];
      allMaterials = d.materials || [];
      fillSelect($("lh-family"), families.map((f) => ({
        key: f.key, label: `${f.label} (${num(f.record_count)}건)`,
      })), "(전체 · 자재로 조회)", initial.family);
      refreshMaterials(initial.material);
    }

    // ── 조회 ────────────────────────────────────────────────────────────
    function currentQuery() {
      return {
        family: $("lh-family").value || undefined,
        material: $("lh-material").value || undefined,
        start_date: $("lh-from").value || undefined,
        end_date: $("lh-to").value || undefined,
      };
    }

    function syncUrl(q) {
      const params = new URLSearchParams();
      Object.entries(q).forEach(([k, v]) => { if (v) params.set(k, v); });
      const qs = params.toString();
      history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
    }

    async function load() {
      const q = currentQuery();
      if (!q.family && !q.material) {
        $("lh-summary").textContent = "레시피나 자재 중 하나는 골라야 합니다.";
        $("lh-timeline").innerHTML = `<p class="empty-state">레시피 또는 자재를 고르세요.</p>`;
        $("lh-changes-body").innerHTML = emptyRow(7, "레시피 또는 자재를 고르세요.");
        $("lh-changes-summary").textContent = "";
        return;
      }
      const btn = $("lh-query");
      IRMS.btnLoading && IRMS.btnLoading(btn, true);
      try {
        state = await request("/blend/lot-history", { query: q });
        syncUrl(q);
        renderSummary();
        renderTimeline();
        renderChanges();
      } catch (e) {
        notify(`LOT 이력 조회 실패: ${e.message || e}`, "error");
      } finally {
        IRMS.btnLoading && IRMS.btnLoading(btn, false);
      }
    }

    function renderSummary() {
      const parts = [];
      if (state.family) parts.push(`레시피 ${state.family.label}`);
      if (state.material) {
        const m = allMaterials.find((x) => x.key === state.material);
        parts.push(`자재 ${m ? m.name : state.material}`);
      }
      parts.push(`${state.start_date || "처음"} ~ ${state.end_date || "지금"}`);
      parts.push(`배합 ${num(state.record_count)}건 · 교체 ${num(state.change_count)}회`);
      $("lh-summary").textContent = parts.join(" · ");
      $("lh-changes-summary").textContent = state.change_count ? `${num(state.change_count)}회` : "";
    }

    // 자재만 골랐을 때(레시피 전체)는 행 이름이 레시피가 된다 — 같은 원재료가 여러
    // 레시피에서 언제 바뀌었는지 교차로 보는 용도.
    function rowTitle(row) {
      if (state.family) return { name: row.material_name, sub: row.material_code || "" };
      return { name: row.family_label, sub: row.material_name };
    }

    function renderTimeline() {
      const box = $("lh-timeline");
      const rows = state.rows || [];
      if (!rows.length) {
        box.innerHTML = `<p class="empty-state">조건에 맞는 배합 기록이 없습니다.</p>`;
        return;
      }
      const axis = axisRange(rows, state.start_date, state.end_date);
      const fmtDay = (n) => new Date(n * 86400000).toISOString().slice(0, 10);
      const html = [];
      if (axis) {
        html.push(`<div class="lh-axis"><span>${esc(fmtDay(axis.lo))}</span><span>${esc(fmtDay(axis.hi))}</span></div>`);
      }
      rows.forEach((row) => {
        const t = rowTitle(row);
        const segs = row.segments || [];
        const layout = axis ? layoutSegments(segs, axis) : [];
        let colorIdx = 0;
        let prevKey = null;
        const bar = segs.map((seg, i) => {
          const blank = !seg.lot_key;
          const isChange = !blank && prevKey !== null && seg.lot_key !== prevKey;
          if (!blank) prevKey = seg.lot_key;
          const cls = blank ? "blank" : `c${(colorIdx++) % 5}`;
          const title = `${seg.lot || "(미입력)"} · ${seg.first_date} ~ ${seg.last_date} · ${seg.record_count}건`;
          const pos = layout[i] || { left: 0, width: 0 };
          return `<span class="lh-seg ${cls}${isChange ? " change" : ""}" style="left:${pos.left.toFixed(2)}%;width:${pos.width.toFixed(2)}%" title="${esc(title)}"></span>`;
        }).join("");
        prevKey = null;
        const chips = segs.map((seg, i) => {
          const blank = !seg.lot_key;
          const isChange = !blank && prevKey !== null && seg.lot_key !== prevKey;
          if (!blank) prevKey = seg.lot_key;
          const span = seg.first_date === seg.last_date
            ? seg.first_date.slice(5)
            : `${seg.first_date.slice(5)}→${seg.last_date.slice(5)}`;
          const chip = `<span class="lh-chip${blank ? " blank" : ""}" title="첫 배합 ${esc(seg.first_product_lot)} · 마지막 ${esc(seg.last_product_lot)}">`
            + `<span class="lot">${esc(seg.lot || "미입력")}</span>`
            + `<span class="span">${esc(span)} · ${num(seg.record_count)}건</span></span>`;
          return (i > 0 ? `<span class="lh-chip-arrow" aria-hidden="true">${isChange ? "▸" : "·"}</span>` : "") + chip;
        }).join("");
        html.push(`
          <div class="lh-row">
            <div class="lh-row-head">
              <span class="lh-row-name">${esc(t.name)}</span>
              ${t.sub ? `<span class="lh-row-sub">${esc(t.sub)}</span>` : ""}
              <span class="lh-row-changes">교체 <b>${num(row.change_count)}</b>회 · 배합 ${num(row.record_count)}건</span>
            </div>
            <div class="lh-row-body">
              <div class="lh-bar">${bar}</div>
              <div class="lh-chips">${chips}</div>
            </div>
          </div>`);
      });
      box.innerHTML = html.join("");
    }

    function renderChanges() {
      const body = $("lh-changes-body");
      const items = state.changes || [];
      // 레시피를 골랐으면 레시피 열은 전부 같은 값 — 숨겨서 표를 좁힌다.
      $("lh-changes-table").classList.toggle("lh-hide-family", !!state.family);
      body.innerHTML = items.length
        ? items.map((c) => `
          <tr>
            <td>${esc(c.work_date)}</td>
            <td class="lh-family-cell">${esc(c.family_label)}</td>
            <td>${lotLink(c.product_lot)}</td>
            <td>${esc(c.material_name)}</td>
            <td>${esc(c.prev_lot)}</td>
            <td><b>${esc(c.new_lot)}</b></td>
            <td>${esc(c.worker)}</td>
          </tr>`).join("")
        : emptyRow(7, "조건 안에서 LOT 교체가 없습니다.");
    }

    // ── 역추적(배합 분석에서 이동) ────────────────────────────────────────
    const STATUS_LABEL = { completed: "완료", canceled: "취소" };

    async function traceMaterialLot() {
      const lot = $("lh-trace-lot").value.trim();
      const body = $("lh-trace-body");
      const summary = $("lh-trace-summary");
      const note = $("lh-trace-note");
      if (!lot) {
        body.innerHTML = emptyRow(8, "자재 LOT을 입력하고 추적하세요.");
        summary.textContent = "";
        note.hidden = true;
        return;
      }
      try {
        const d = await request("/blend/material-lot-trace", { query: { lot } });
        const items = d.items || [];
        summary.textContent = items.length ? `배합 ${num(d.record_count)}건 · 자재 행 ${num(d.total)}건` : "";
        // 서버 상한 도달 — 조용히 자르지 않고 알린다.
        if (d.truncated) {
          note.textContent = `표시 상한 ${num(d.limit || d.total)}행에 도달 · 일부가 잘렸을 수 있습니다. LOT을 더 정확히 입력해 좁히세요.`;
          note.hidden = false;
        } else {
          note.hidden = true;
        }
        body.innerHTML = items.length
          ? items.map((it) => `
            <tr>
              <td>${esc(it.work_date)}</td>
              <td>${lotLink(it.product_lot)}</td>
              <td>${esc(it.product_name)}</td>
              <td>${esc(it.material_name)}</td>
              <td>${esc(it.material_lot)}</td>
              <td class="num">${num(it.actual_amount, 2)}</td>
              <td>${esc(it.worker)}</td>
              <td>${esc(STATUS_LABEL[it.status] || it.status)}</td>
            </tr>`).join("")
          : emptyRow(8, `'${lot}' 이 투입된 배합 기록이 없습니다.`);
      } catch (e) {
        body.innerHTML = emptyRow(8, `추적 실패: ${e.message || e}`);
        summary.textContent = "";
        note.hidden = true;
      }
    }

    // ── 탭 ──────────────────────────────────────────────────────────────
    const TAB_TITLES = { timeline: "타임라인", changes: "교체 이력", trace: "역추적" };
    const tabBtns = document.querySelectorAll(".lh-tabs .mgmt-tab");
    function activateTab(tab) {
      tabBtns.forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${tab}`));
      const heading = document.querySelector(".topbar-heading");
      if (heading && TAB_TITLES[tab]) heading.textContent = `LOT 이력 · ${TAB_TITLES[tab]}`;
    }
    tabBtns.forEach((btn) => btn.addEventListener("click", () => activateTab(btn.dataset.tab)));

    // ── 기간 프리셋 ──────────────────────────────────────────────────────
    const RANGE_BTNS = ["lh-range-all", "lh-range-year", "lh-range-90"];
    function markRange(activeId) {
      RANGE_BTNS.forEach((id) => $(id).classList.toggle("active", id === activeId));
    }
    function applyRange(activeId, from, to) {
      markRange(activeId);
      $("lh-from").value = from;
      $("lh-to").value = to;
      load();
    }
    $("lh-range-all").addEventListener("click", () => applyRange("lh-range-all", "", ""));
    $("lh-range-year").addEventListener("click",
      () => applyRange("lh-range-year", `${new Date().getFullYear()}-01-01`, ""));
    $("lh-range-90").addEventListener("click", () => applyRange("lh-range-90", daysAgo(89), ""));
    $("lh-from").addEventListener("change", () => markRange(null));
    $("lh-to").addEventListener("change", () => markRange(null));

    $("lh-family").addEventListener("change", () => { refreshMaterials(); load(); });
    $("lh-material").addEventListener("change", load);
    $("lh-query").addEventListener("click", load);
    $("lh-export").addEventListener("click", () => {
      const q = currentQuery();
      if (!q.family && !q.material) {
        notify("레시피나 자재 중 하나는 골라야 합니다.", "error");
        return;
      }
      const params = new URLSearchParams();
      Object.entries(q).forEach(([k, v]) => { if (v) params.set(k, v); });
      window.location.href = `/api/blend/lot-history/export?${params.toString()}`;
    });

    $("lh-trace-btn").addEventListener("click", traceMaterialLot);
    $("lh-trace-lot").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) traceMaterialLot();
    });

    // ── 초기화 — URL 딥링크(?family=&material=&start_date=&end_date=&tab=&lot=) ──
    const url = new URLSearchParams(location.search);
    const initial = {
      family: url.get("family") || "",
      material: url.get("material") || "",
    };
    if (url.get("start_date") || url.get("end_date")) {
      $("lh-from").value = url.get("start_date") || "";
      $("lh-to").value = url.get("end_date") || "";
      markRange(null);
    }
    if (url.get("tab") && TAB_TITLES[url.get("tab")]) activateTab(url.get("tab"));
    if (url.get("lot")) {
      $("lh-trace-lot").value = url.get("lot");
      activateTab("trace");
      traceMaterialLot();
    }

    loadFamilies(initial)
      .then(() => {
        if ($("lh-family").value || $("lh-material").value) load();
      })
      .catch((e) => notify(`선택지 불러오기 실패: ${e.message || e}`, "error"));
  });
})();
