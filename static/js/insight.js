document.addEventListener("DOMContentLoaded", () => {
  const fromInput = document.getElementById("insight-from");
  const toInput = document.getElementById("insight-to");
  const colorSelect = document.getElementById("insight-color");
  const categorySelect = document.getElementById("insight-category");
  const queryBtn = document.getElementById("insight-query");
  const exportBtn = document.getElementById("insight-export");
  const rangeWeekBtn = document.getElementById("insight-range-week");
  const rangeMonthBtn = document.getElementById("insight-range-month");
  const resetBtn = document.getElementById("insight-reset");
  const filterSummary = document.getElementById("insight-filter-summary");

  const metricRecipes = document.getElementById("metric-recipes");
  const metricWeight = document.getElementById("metric-weight");
  const metricCount = document.getElementById("metric-count");
  const metricMaterials = document.getElementById("metric-materials");
  const weightBody = document.getElementById("weight-body");
  const countBody = document.getElementById("count-body");
  const barStack = document.getElementById("bar-stack");

  let snapshot = null;
  const preferenceKeys = {
    from: "irms_insight_from",
    to: "irms_insight_to",
    color: "irms_insight_color",
    category: "irms_insight_category",
  };

  function applyDateRange(days) {
    const today = new Date();
    const startDate = new Date(today.getTime() - 1000 * 60 * 60 * 24 * days);
    fromInput.value = IRMS.toDateOnly(startDate.toISOString());
    toInput.value = IRMS.toDateOnly(today.toISOString());
  }

  function persistFilters() {
    IRMS.savePreference(preferenceKeys.from, fromInput.value);
    IRMS.savePreference(preferenceKeys.to, toInput.value);
    IRMS.savePreference(preferenceKeys.color, colorSelect.value);
    IRMS.savePreference(preferenceKeys.category, categorySelect.value);
  }

  function updateFilterSummary() {
    if (!filterSummary) {
      return;
    }

    const parts = [`기간 ${fromInput.value || "시작 미지정"} ~ ${toInput.value || "종료 미지정"}`];
    if (colorSelect.value) {
      parts.push(`색상 ${colorSelect.value}`);
    } else {
      parts.push("색상 전체");
    }
    if (categorySelect.value) {
      parts.push(`카테고리 ${categorySelect.value}`);
    } else {
      parts.push("카테고리 전체");
    }

    filterSummary.textContent = `${parts.join(" · ")} 기준으로 소비 통계를 표시 중입니다.`;
  }

  function restoreFilters() {
    fromInput.value = IRMS.loadPreference(preferenceKeys.from, "");
    toInput.value = IRMS.loadPreference(preferenceKeys.to, "");
    colorSelect.value = IRMS.loadPreference(preferenceKeys.color, "");
  }

  function resetFilters() {
    applyDateRange(7);
    colorSelect.value = "";
    categorySelect.value = "";
    persistFilters();
    updateFilterSummary();
    render();
  }

  async function populateFilterOptions() {
    const materials = await IRMS.getMaterials();
    const categories = new Set();
    materials.forEach((material) => {
      if (material.category) {
        categories.add(material.category);
      }
    });
    categorySelect.innerHTML =
      '<option value="">전체</option>' +
      Array.from(categories)
        .sort()
        .map((category) => `<option value="${IRMS.escapeHtml(category)}">${IRMS.escapeHtml(category)}</option>`)
        .join("");
    categorySelect.value = IRMS.loadPreference(preferenceKeys.category, "");
  }

  function renderMetrics(data) {
    const summary = data.summary || {};
    metricRecipes.textContent = `${summary.completedRecipes || 0}건`;
    metricWeight.textContent = `${IRMS.formatValue(summary.totalWeight || 0)} g`;
    metricCount.textContent = `${IRMS.formatValue(summary.totalCount || 0)} 회`;
    metricMaterials.textContent = `${summary.activeMaterials || 0}종`;
  }

  function renderWeightTable(data) {
    const rows = (data.items || []).filter((row) => row.unitType === "weight");
    if (!rows.length) {
      weightBody.innerHTML =
        '<tr><td colspan="6"><div class="empty-state">집계할 중량 데이터가 없습니다.</div></td></tr>';
      return;
    }

    weightBody.innerHTML = rows
      .map(
        (row) => `
          <tr>
            <td>${IRMS.escapeHtml(row.materialName)}</td>
            <td>${IRMS.escapeHtml(row.colorGroup)}</td>
            <td>${IRMS.escapeHtml(row.unit)}</td>
            <td class="material-value">${IRMS.formatValue(row.totalWeight)}</td>
            <td>${row.recipeCount}</td>
          </tr>
        `
      )
      .join("");
  }

  function renderCountTable(data) {
    const rows = (data.items || []).filter((row) => row.unitType === "count");
    if (!rows.length) {
      countBody.innerHTML =
        '<tr><td colspan="6"><div class="empty-state">집계할 count 데이터가 없습니다.</div></td></tr>';
      return;
    }

    countBody.innerHTML = rows
      .map(
        (row) => `
          <tr>
            <td>${IRMS.escapeHtml(row.materialName)}</td>
            <td>${IRMS.escapeHtml(row.colorGroup)}</td>
            <td>${IRMS.escapeHtml(row.unit)}</td>
            <td class="material-value">${IRMS.formatValue(row.totalCount)}</td>
            <td>${row.recipeCount}</td>
          </tr>
        `
      )
      .join("");
  }

  function renderBars(data) {
    const topRows = (data.items || [])
      .map((row) => ({
        label: row.materialName,
        value: row.unitType === "weight" ? row.totalWeight : row.totalCount,
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 6);

    if (!topRows.length) {
      barStack.innerHTML = '<div class="empty-state">시각화할 데이터가 없습니다.</div>';
      return;
    }

    const max = topRows[0].value || 1;
    barStack.innerHTML = topRows
      .map((row) => {
        const width = Math.max(8, Math.round((row.value / max) * 100));
        return `
          <div class="bar-row">
            <strong>${IRMS.escapeHtml(row.label)}</strong>
            <div class="bar-track">
              <div class="bar-fill" style="width:${width}%"></div>
            </div>
            <span class="bar-value">${IRMS.formatValue(row.value)}</span>
          </div>
        `;
      })
      .join("");
  }

  async function render() {
    persistFilters();
    updateFilterSummary();
    try {
      snapshot = await IRMS.getStats({
        dateFrom: fromInput.value,
        dateTo: toInput.value,
        colorGroup: colorSelect.value || undefined,
        category: categorySelect.value || undefined,
      });
      renderMetrics(snapshot);
      renderWeightTable(snapshot);
      renderCountTable(snapshot);
      renderBars(snapshot);
    } catch (error) {
      IRMS.notify(`통계 조회 실패: ${error.message}`, "error");
    }
  }

  function exportCsv() {
    if (!snapshot) {
      return;
    }
    IRMS.exportStatsCsv({
      dateFrom: fromInput.value,
      dateTo: toInput.value,
      colorGroup: colorSelect.value || undefined,
      category: categorySelect.value || undefined,
    });
  }

  queryBtn.addEventListener("click", render);
  exportBtn.addEventListener("click", exportCsv);
  fromInput.addEventListener("change", render);
  toInput.addEventListener("change", render);
  colorSelect.addEventListener("change", render);
  categorySelect.addEventListener("change", render);
  if (rangeWeekBtn) {
    rangeWeekBtn.addEventListener("click", () => {
      applyDateRange(7);
      persistFilters();
      updateFilterSummary();
      render();
    });
  }
  if (rangeMonthBtn) {
    rangeMonthBtn.addEventListener("click", () => {
      applyDateRange(30);
      persistFilters();
      updateFilterSummary();
      render();
    });
  }
  if (resetBtn) {
    resetBtn.addEventListener("click", resetFilters);
  }

  (async () => {
    restoreFilters();
    if (!fromInput.value || !toInput.value) {
      applyDateRange(7);
    }
    await populateFilterOptions();
    updateFilterSummary();
    await render();
  })();
});
