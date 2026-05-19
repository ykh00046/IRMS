/**
 * version-compare module — recipe version history modal + version
 * comparison modal.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createVersionCompare(ctx)
 * Returns: { handleLookupHistory, renderHistoryModal, getSelectedVersionIds,
 *            updateCompareButtonState, handleCompareVersions, renderCompareModal }
 *
 * ctx dependencies:
 *   dom:   historyModal, historyModalTitle, historyModalSubtitle,
 *          versionHistoryBody, historyCompareBtn, compareModal,
 *          compareModalTitle, compareThead, compareTbody
 *   state: selectedRecipeId, currentHistoryChain
 *   other: ctx.onClone (.history-row-clone button)
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createVersionCompare = function (ctx) {
    const { dom, state } = ctx;

    async function handleLookupHistory() {
      if (!state.selectedRecipeId) return;
      try {
        const res = await fetch(`/api/recipes/${state.selectedRecipeId}/history`, { credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        state.currentHistoryChain = data;
        renderHistoryModal(data);
        if (dom.historyModal) dom.historyModal.hidden = false;
      } catch (error) {
        IRMS.notify(`버전 이력 조회 실패: ${error.message}`, "error");
      }
    }

    function renderHistoryModal(data) {
      const items = data.items || [];
      if (dom.historyModalSubtitle && items.length) {
        const first = items[0];
        dom.historyModalSubtitle.textContent = `${first.product_name || ""} / ${first.position || "-"} / ${first.ink_name || ""}`;
      }
      if (dom.historyModalTitle) {
        dom.historyModalTitle.textContent = `버전 이력 (${items.length}개)`;
      }
      if (!items.length) {
        dom.versionHistoryBody.innerHTML = '<tr><td colspan="7"><div class="empty-state">이력이 없습니다.</div></td></tr>';
        return;
      }
      dom.versionHistoryBody.innerHTML = items
        .map(
          (it) => `
            <tr data-recipe-id="${it.id}">
              <td><input type="checkbox" class="version-check" value="${it.id}" /></td>
              <td><strong>${IRMS.escapeHtml(it.version_label)}</strong>${it.is_current ? ' <span class="status-chip status-completed">현재 사용</span>' : ""}</td>
              <td>${IRMS.formatDateTime(it.created_at)}</td>
              <td>${IRMS.escapeHtml(it.created_by || "-")}</td>
              <td class="num">${it.item_count}</td>
              <td><span class="status-chip ${IRMS.statusClass(it.status)}">${IRMS.statusLabel(it.status)}</span></td>
              <td><button class="btn btn-sm history-row-clone" data-recipe-id="${it.id}" type="button">이 버전 복제</button></td>
            </tr>
          `,
        )
        .join("");

      dom.versionHistoryBody.querySelectorAll(".version-check").forEach((cb) => {
        cb.addEventListener("change", updateCompareButtonState);
      });
      dom.versionHistoryBody.querySelectorAll(".history-row-clone").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const rid = Number(btn.dataset.recipeId);
          state.selectedRecipeId = rid;
          if (dom.historyModal) dom.historyModal.hidden = true;
          ctx.onClone();
        });
      });
      updateCompareButtonState();
    }

    function getSelectedVersionIds() {
      if (!dom.versionHistoryBody) return [];
      return Array.from(dom.versionHistoryBody.querySelectorAll(".version-check:checked")).map((cb) => Number(cb.value));
    }

    function updateCompareButtonState() {
      if (!dom.historyCompareBtn) return;
      dom.historyCompareBtn.disabled = getSelectedVersionIds().length < 2;
    }

    async function handleCompareVersions() {
      const ids = getSelectedVersionIds();
      if (ids.length < 2) return;
      try {
        const res = await fetch(`/api/recipes/history/compare?ids=${ids.join(",")}`, { credentials: "same-origin" });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        renderCompareModal(data);
        if (dom.compareModal) dom.compareModal.hidden = false;
      } catch (error) {
        IRMS.notify(`버전 비교 실패: ${error.message}`, "error");
      }
    }

    function renderCompareModal(data) {
      const versions = data.versions || [];
      const materials = data.materials || [];
      if (dom.compareModalTitle) {
        const labels = versions.map((v) => v.version_label).join(", ");
        dom.compareModalTitle.textContent = `버전 비교 (${labels})`;
      }
      const statusLabel = { same: "동일", modified: "수정", partial: "추가/제거" };
      const headerCells = [
        '<th class="compare-sticky">원재료</th>',
        ...versions.map((v) => `<th>${IRMS.escapeHtml(v.version_label)}<br><span class="muted">${IRMS.formatDateTime(v.created_at)}</span></th>`),
        "<th>상태</th>",
      ].join("");
      dom.compareThead.innerHTML = `<tr>${headerCells}</tr>`;

      if (!materials.length) {
        dom.compareTbody.innerHTML = `<tr><td colspan="${versions.length + 2}"><div class="empty-state">비교할 재료가 없습니다.</div></td></tr>`;
        return;
      }
      dom.compareTbody.innerHTML = materials
        .map((mat) => {
          const valueMap = {};
          for (const v of mat.values) {
            valueMap[v.version_id] = v;
          }
          const cells = versions
            .map((ver) => {
              const entry = valueMap[ver.id];
              if (!entry) return '<td class="value-cell muted">-</td>';
              const display = entry.value_text != null && entry.value_text !== ""
                ? entry.value_text
                : (entry.value_weight != null ? String(entry.value_weight) : "-");
              return `<td class="value-cell">${IRMS.escapeHtml(display)}</td>`;
            })
            .join("");
          return `<tr class="compare-${mat.change_status}">
            <td class="compare-sticky">${IRMS.escapeHtml(mat.material_name)}</td>
            ${cells}
            <td>${statusLabel[mat.change_status] || mat.change_status}</td>
          </tr>`;
        })
        .join("");
    }

    return {
      handleLookupHistory,
      renderHistoryModal,
      getSelectedVersionIds,
      updateCompareButtonState,
      handleCompareVersions,
      renderCompareModal,
    };
  };
})();
