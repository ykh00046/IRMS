/**
 * version-compare module — '버전 비교' 탭 렌더러(모달 없는 한 화면, 2026-08-06 재설계).
 *
 * 좌측: 버전 타임라인(체크박스) + 우측: 선택한 버전들의 자재 구성 나란히 비교표.
 * 반제품 선택 시 개정 체인 전체를 불러 타임라인을 채우고, 비교표는 선택한 버전만 나란히.
 * 기본 선택 = 현재판 + 그 직전판(있으면). 서버 신규 라우트 없이:
 *   GET /api/recipes/{id}/history  — 체인 전체(버전 라벨·상태·is_current·항목수)
 *   GET /api/recipes/history/compare?ids=<체인 전체>  — 자재 행렬(value_weight·change_status)
 *
 * Factory: IRMS.management.createVersionCompare(ctx)
 * Returns: { loadVersionsForProduct, openVersionCompareTab, rerenderCompare }
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createVersionCompare = function (ctx) {
    const { dom, state } = ctx;
    // 현재 로드된 버전 비교 데이터(체크박스 변경 시 재렌더에 재사용 — 재조회 없음).
    let cache = null; // { historyItems, compareData }

    // 숫자 포맷 — null/빈은 '-'.
    function num(v, d) {
      const n = Number(v);
      if (!Number.isFinite(n)) return "-";
      return n.toFixed(d === undefined ? 2 : d);
    }

    // ── 반제품(또는 특정 레시피 id)의 체인 전체를 로드 → 타임라인+비교표 렌더 ──
    // recipeId: 체인의 아무 버전 id(현황 [버전 이력] 버튼이 넘김) 또는 제품명에서 찾은 id.
    async function loadVersionsForProduct(recipeId) {
      if (!recipeId) return;
      const layout = document.getElementById("vc-layout");
      const timeline = document.getElementById("vc-timeline");
      if (!layout || !timeline) return;
      // 빈 상태 안내("반제품명을 선택하면…")는 결과가 나오면 치운다 — 종전엔 결과 위에
      // 그대로 남아 화면이 두 말을 했다(2026-08-07 검증). 반제품 칩은 다른 반제품으로
      // 바로 갈아탈 수 있게 남겨 둔다.
      const emptyHint = document.querySelector("#lookup-result .empty-state");
      if (emptyHint) emptyHint.hidden = true;
      try {
        // 1) history — 체인 전체 버전 메타(version_label·is_current·status·item_count).
        const hres = await fetch(`/api/recipes/${recipeId}/history`, { credentials: "same-origin" });
        if (!hres.ok) throw new Error(`HTTP ${hres.status}`);
        const history = await hres.json();
        const historyItems = history.items || [];
        if (!historyItems.length) {
          layout.hidden = false;
          timeline.innerHTML = '<p class="empty-state">이 반제품의 버전 이력이 없습니다.</p>';
          document.getElementById("vc-compare").innerHTML = "";
          cache = null;
          return;
        }
        // 2) compare — 체인 전체 id 로 자재 행렬을 한 번에(같은 체인만 허용되므로 전 버전 가능).
        const ids = historyItems.map((it) => it.id).join(",");
        let compareData = { versions: [], materials: [] };
        try {
          const cres = await fetch(`/api/recipes/history/compare?ids=${ids}`, { credentials: "same-origin" });
          if (cres.ok) compareData = await cres.json();
        } catch (_e) { /* 비교 행렬 조회 실패 — 타임라인만이라도 보여준다 */ }
        cache = { historyItems, compareData };
        layout.hidden = false;
        // 기본 선택 = 현재판 + 그 직전판(있으면). history 의 정렬 방향에 기대지 않는다 —
        // 실제 응답은 옛→최신 순이라 인덱스+1 로 집으면 배열 끝을 넘어가 현재판 하나만
        // 선택됐다(비교 탭인데 첫 화면이 단일 표시 — 2026-08-07 검증). 등록일 기준으로
        // 정렬해 현재판의 바로 앞 판을 고른다.
        const byNewest = [...historyItems].sort(
          (a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")),
        );
        const current = historyItems.find((it) => it.is_current) || byNewest[0];
        const defaultIds = new Set();
        if (current) {
          defaultIds.add(current.id);
          const pos = byNewest.findIndex((it) => it.id === current.id);
          const prev = pos >= 0 ? byNewest[pos + 1] : byNewest[1];
          if (prev) defaultIds.add(prev.id);
        }
        renderTimeline(historyItems, defaultIds);
        rerenderCompare();
      } catch (error) {
        layout.hidden = false;
        timeline.innerHTML = `<p class="empty-state">버전 이력 조회 실패: ${IRMS.escapeHtml(error.message)}</p>`;
        document.getElementById("vc-compare").innerHTML = "";
        cache = null;
      }
    }

    // ── 타임라인 렌더 — 세로 목록, 각 행 = 체크박스 + v번호 + 등록일시 + 등록자 + 항목수 + 상태칩 ──
    function renderTimeline(items, selectedIds) {
      const timeline = document.getElementById("vc-timeline");
      if (!timeline) return;
      const statusChip = (it) => {
        if (it.status === "canceled") {
          return `<span class="status-chip ${IRMS.statusClass(it.status)}">${IRMS.statusLabel(it.status)}</span>`;
        }
        return it.is_current
          ? '<span class="status-chip status-completed">사용중</span>'
          : '<span class="status-chip">이전 버전</span>';
      };
      timeline.innerHTML = items
        .map((it) => {
          const checked = selectedIds.has(it.id) ? " checked" : "";
          return `<label class="vc-version-row${it.is_current ? " is-current" : ""}${it.status === "canceled" ? " is-canceled" : ""}" data-recipe-id="${it.id}">`
            + `<input type="checkbox" class="vc-version-check" value="${it.id}"${checked} />`
            + `<span class="vc-version-main">`
            + `<span class="vc-version-label"><b>${IRMS.escapeHtml(it.version_label)}</b>${it.is_current ? ' <span class="status-chip status-completed">현재</span>' : ""}</span>`
            + `<span class="vc-version-meta muted">${IRMS.formatDateTime(it.created_at)} · ${IRMS.escapeHtml(it.created_by || "-")} · 항목 ${it.item_count}</span>`
            + `<span class="vc-version-status">${statusChip(it)}</span>`
            + `</span>`
            + `</label>`;
        })
        .join("");
      timeline.querySelectorAll(".vc-version-check").forEach((cb) => {
        cb.addEventListener("change", () => {
          // 체크박스 변경 시 비교표만 재렌더(재조회 없음).
          rerenderCompare();
        });
      });
    }

    // ── 비교표 재렌더 — 캐시(cache) 의 선택된 버전만 ──
    function rerenderCompare() {
      const wrap = document.getElementById("vc-compare");
      if (!wrap || !cache) return;
      const selectedIds = getSelectedIds();
      const { historyItems, compareData } = cache;
      // history 최신순을 비교표 열 순서로(최신이 첫 열).
      const versions = historyItems.filter((it) => selectedIds.has(it.id));
      if (!versions.length) {
        wrap.innerHTML = '<p class="empty-state">왼쪽에서 버전을 2개 이상 고르세요.</p>';
        return;
      }
      if (versions.length === 1) {
        wrap.innerHTML = renderSingleVersion(versions[0], compareData);
        return;
      }
      wrap.innerHTML = renderCompareTable(versions, compareData);
    }

    function getSelectedIds() {
      const timeline = document.getElementById("vc-timeline");
      if (!timeline) return new Set();
      return new Set(
        Array.from(timeline.querySelectorAll(".vc-version-check:checked")).map((cb) => Number(cb.value)),
      );
    }

    // ── 단일 버전 보기(비교 아님 명시) ──
    function renderSingleVersion(version, compareData) {
      const vMap = {}; // material_id → {display, value_weight}
      const totals = {}; // version_id → sum
      (compareData.materials || []).forEach((mat) => {
        const v = (mat.values || []).find((x) => x.version_id === version.id);
        if (v && v.value_weight != null) {
          vMap[mat.material_id] = v;
          totals[version.id] = (totals[version.id] || 0) + Number(v.value_weight);
        }
      });
      const total = totals[version.id] || 0;
      // 현재판 순서 재정렬 — compareData.materials 가 알파벳 순이므로 historyItems 의 현재판
      // item 순서를 알 수 없으면 그대로 둔다(서버가 material_id 만 주므로 순서 보존 안 됨).
      const rows = (compareData.materials || [])
        .filter((mat) => vMap[mat.material_id])
        .map((mat) => {
          const v = vMap[mat.material_id];
          const w = Number(v.value_weight) || 0;
          const pct = total > 0 ? (w / total) * 100 : 0;
          return `<tr><td class="vc-mat-cell">${IRMS.escapeHtml(mat.material_name)}</td>`
            + `<td class="num">${num(w)} <span class="muted small">(${num(pct, 1)}%)</span></td></tr>`;
        }).join("");
      return `<p class="vc-single-note">단일 버전 표시 — 비교하려면 왼쪽에서 버전을 하나 더 선택하세요.</p>`
        + `<div class="compare-scroll"><table class="compare-table vc-compare-table">`
        + `<thead><tr><th class="compare-sticky">${IRMS.escapeHtml(version.version_label)} 자재</th><th>배합량 (g · %)</th></tr></thead>`
        + `<tbody>${rows || '<tr><td colspan="2"><span class="muted">자재가 없습니다.</span></td></tr>'}</tbody>`
        + `<tfoot><tr class="vc-total-row"><td class="compare-sticky">총량</td><td class="num">${num(total)}</td></tr></tfoot>`
        + `</table></div>`;
    }

    // ── 나란히 비교표 ──
    function renderCompareTable(versions, compareData) {
      // 각 버전 총량(value_weight 합).
      const totals = {};
      (compareData.materials || []).forEach((mat) => {
        (mat.values || []).forEach((v) => {
          if (v.value_weight != null) totals[v.version_id] = (totals[v.version_id] || 0) + Number(v.value_weight);
        });
      });
      // 자재 순서: '현재판' 버전(versions 중 is_current)에 있는 자재 순서 우선, 사라진 자재는 뒤.
      // compareData.materials 는 알파벳 순이므로, 현재판의 자재 순서를 기준으로 재정렬한다.
      const currentVer = versions.find((v) => {
        const h = (cache.historyItems || []).find((it) => it.id === v.id);
        return h && h.is_current;
      }) || versions[0];
      const currentOrder = []; // 현재판에 있는 material_id 순서
      (compareData.materials || []).forEach((mat) => {
        const v = (mat.values || []).find((x) => x.version_id === currentVer.id);
        if (v && v.value_weight != null) currentOrder.push(mat.material_id);
      });
      const orderedMats = [];
      const seen = new Set();
      // 1) 현재판 순서
      currentOrder.forEach((mid) => {
        const m = (compareData.materials || []).find((x) => x.material_id === mid);
        if (m) { orderedMats.push(m); seen.add(mid); }
      });
      // 2) 나머지(사라졌거나 현재판엔 없는 자재) — 알파벳 순 유지
      (compareData.materials || []).forEach((mat) => {
        if (!seen.has(mat.material_id)) orderedMats.push(mat);
      });

      // 직전 버전(reference): 선택된 버전 중 현재판의 바로 직전(역사적).
      // 셀 단위 증감은 "같은 자재의 직전 버전 값 대비"로 한다 — 직전 = 선택 열 중에서 더 옛인 것.
      const sortedByTime = [...versions].sort((a, b) => {
        const ha = (cache.historyItems || []).find((it) => it.id === a.id);
        const hb = (cache.historyItems || []).find((it) => it.id === b.id);
        return String((hb || {}).created_at || "").localeCompare(String((ha || {}).created_at || ""));
      });

      // 행 상태(추가/제거/수정/동일) — 선택된 전체 버전 기준.
      function rowStatus(mat) {
        const presentIn = new Set();
        (mat.values || []).forEach((v) => {
          if (v.value_weight != null && versions.some((ver) => ver.id === v.version_id)) {
            presentIn.add(v.version_id);
          }
        });
        if (presentIn.size === 0) return "removed";
        if (presentIn.size < versions.length) return "partial";
        // 전 버전에 다 있 — 값이 바뀌었는지.
        const weights = (mat.values || [])
          .filter((v) => versions.some((ver) => ver.id === v.version_id))
          .map((v) => v.value_weight);
        const distinct = new Set(weights.map((w) => String(w)));
        if (distinct.size === 1) return "same";
        return "modified";
      }
      const statusLabel = { same: "동일", modified: "수정", partial: "추가/제거", removed: "제거" };

      // 헤더 — 자재명(sticky) + 버전열들 + 상태열.
      const headerCells = [
        '<th class="compare-sticky vc-mat-head">자재</th>',
        ...sortedByTime.map((ver) => {
          const h = (cache.historyItems || []).find((it) => it.id === ver.id);
          const cur = h && h.is_current ? ' <span class="status-chip status-completed">현재</span>' : "";
          return `<th>${IRMS.escapeHtml(ver.version_label)}${cur}<br><span class="muted">${IRMS.formatDateTime(ver.created_at || (h || {}).created_at)}</span></th>`;
        }),
        "<th>상태</th>",
      ].join("");

      // 자재 행.
      const bodyRows = orderedMats.map((mat) => {
        const st = rowStatus(mat);
        const cells = sortedByTime.map((ver, colIdx) => {
          const v = (mat.values || []).find((x) => x.version_id === ver.id);
          if (!v || v.value_weight == null) {
            return '<td class="num muted">-</td>';
          }
          const w = Number(v.value_weight) || 0;
          const t = totals[ver.id] || 0;
          const pct = t > 0 ? (w / t) * 100 : 0;
          // 증감: 같은 자재의 직전 선택 열(더 옛 버전) 대비.
          let deltaHtml = "";
          if (colIdx < sortedByTime.length - 1) {
            const prevVer = sortedByTime[colIdx + 1];
            const pv = (mat.values || []).find((x) => x.version_id === prevVer.id);
            if (pv && pv.value_weight != null) {
              const diff = w - Number(pv.value_weight);
              if (Math.abs(diff) > 1e-9) {
                const sign = diff > 0 ? "+" : "";
                deltaHtml = ` <span class="vc-delta ${diff > 0 ? "up" : "down"}">${sign}${num(diff, 2)}</span>`;
              }
            }
          }
          const changed = deltaHtml ? " vc-changed" : "";
          return `<td class="num${changed}">${num(w)} <span class="muted small">(${num(pct, 1)}%)</span>${deltaHtml}</td>`;
        }).join("");
        return `<tr class="compare-${st}">`
          + `<td class="compare-sticky vc-mat-cell">${IRMS.escapeHtml(mat.material_name)}</td>`
          + `${cells}`
          + `<td class="vc-status-cell">${statusLabel[st] || st}</td>`
          + `</tr>`;
      }).join("");

      // 총량 행(맨 아래).
      const totalCells = sortedByTime.map((ver) => {
        const t = totals[ver.id] || 0;
        return `<td class="num">${num(t)}</td>`;
      }).join("");
      const totalRow = `<tr class="vc-total-row">`
        + `<td class="compare-sticky">총량 (g)</td>${totalCells}<td></td></tr>`;

      return `<div class="compare-scroll">`
        + `<table class="compare-table vc-compare-table">`
        + `<thead><tr>${headerCells}</tr></thead>`
        + `<tbody>${bodyRows || '<tr><td colspan="' + (sortedByTime.length + 2) + '"><span class="muted">자재가 없습니다.</span></td></tr>'}</tbody>`
        + `<tfoot>${totalRow}</tfoot>`
        + `</table>`
        + `<p class="vc-legend muted small">`
        + `<span class="vc-legend-item"><span class="vc-dot vc-dot-modified"></span>수정</span>`
        + `<span class="vc-legend-item"><span class="vc-dot vc-dot-partial"></span>추가/제거</span>`
        + `<span class="vc-legend-item"><span class="vc-delta up">+증감</span> 직전 버전 대비</span>`
        + `</p>`
        + `</div>`;
    }

    // ── 현황 [버전 이력] 버튼 → 이 탭으로 전환 + 반제품 자동 선택 ──
    // management.js 의 switchToLookupTab(recipeId) 가 이 함수를 호출해 데이터를 채운다.
    async function openVersionCompareTab(recipeId) {
      // 탭 전환은 호출부(switchToLookupTab)가 담당. 여기선 데이터 로드만.
      // 검색창에 제품명을 채우고 loadVersionsForProduct 로 타임라인+비교표를 그린다.
      try {
        // recipeId 의 제품명을 history 에서 얻어 검색창에 반영(칩/검색 일치).
        const hres = await fetch(`/api/recipes/${recipeId}/history`, { credentials: "same-origin" });
        if (hres.ok) {
          const h = await hres.json();
          const first = (h.items || [])[0];
          if (first && first.product_name && dom.lookupProduct) {
            dom.lookupProduct.value = first.product_name;
          }
        }
      } catch (_e) { /* 무시 */ }
      await loadVersionsForProduct(recipeId);
    }

    return {
      loadVersionsForProduct,
      openVersionCompareTab,
      rerenderCompare,
    };
  };
})();
