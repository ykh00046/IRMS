/**
 * recipe-history module — 이력 tab: filters persistence + history table
 * with accordion detail rows.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createRecipeHistory(ctx)
 * Returns: { persistHistoryFilters, updateHistorySummary,
 *            restoreHistoryFilters, resetHistoryFilters, renderHistory }
 *
 * ctx dependencies:
 *   dom:   historyBody, historyStatus, historySearch, historyFrom,
 *          historyTo, historySummary
 *   const: preferenceKeys
 *   state: selectedRecipeId
 *   other: ctx.copyToClipboard (.history-copy-btn)
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createRecipeHistory = function (ctx) {
    const { dom, state } = ctx;
    const { preferenceKeys } = ctx.const;

    function persistHistoryFilters() {
      IRMS.savePreference(preferenceKeys.status, dom.historyStatus.value);
      IRMS.savePreference(preferenceKeys.search, dom.historySearch.value.trim());
      IRMS.savePreference(preferenceKeys.from, dom.historyFrom.value);
      IRMS.savePreference(preferenceKeys.to, dom.historyTo.value);
    }

    function updateHistorySummary() {
      if (!dom.historySummary) {
        return;
      }

      const parts = [`상태 ${dom.historyStatus.value || "전체"}`];
      const search = dom.historySearch.value.trim();
      const from = dom.historyFrom.value;
      const to = dom.historyTo.value;

      if (search) {
        parts.push(`검색어 "${search}"`);
      }
      if (from || to) {
        parts.push(`기간 ${from || "시작 미지정"} ~ ${to || "종료 미지정"}`);
      }

      dom.historySummary.textContent = `${parts.join(" · ")} 기준으로 레시피 현황을 표시 중입니다.`;
    }

    function restoreHistoryFilters() {
      dom.historyStatus.value = IRMS.loadPreference(preferenceKeys.status, "");
      dom.historySearch.value = IRMS.loadPreference(preferenceKeys.search, "");
      dom.historyFrom.value = IRMS.loadPreference(preferenceKeys.from, "");
      dom.historyTo.value = IRMS.loadPreference(preferenceKeys.to, "");
    }

    function resetHistoryFilters() {
      dom.historyStatus.value = "";
      dom.historySearch.value = "";
      dom.historyFrom.value = "";
      dom.historyTo.value = "";
      IRMS.clearPreference(preferenceKeys.status);
      IRMS.clearPreference(preferenceKeys.search);
      IRMS.clearPreference(preferenceKeys.from);
      IRMS.clearPreference(preferenceKeys.to);
      updateHistorySummary();
      renderHistory();
    }

    async function renderHistory() {
      persistHistoryFilters();
      updateHistorySummary();
      try {
        const rows = await IRMS.getRecipes({
          status: dom.historyStatus.value || undefined,
          search: dom.historySearch.value.trim() || undefined,
          dateFrom: dom.historyFrom.value || undefined,
          dateTo: dom.historyTo.value || undefined,
        });

        if (!rows.length) {
          dom.historyBody.innerHTML =
            '<tr><td colspan="11"><div class="empty-state">조건에 맞는 레시피가 없습니다.</div></td></tr>';
          return;
        }

        // 분류 셀 — 책임자는 목록에서 바로 바꾸는 드롭다운(변경 즉시 저장), 그 외는 텍스트.
        const CATS = ["약품", "합성", "잉크", "용수"];
        const categoryCell = (recipe) => {
          const cat = recipe.category || "";
          if (!ctx.canManage) {
            return `<td>${cat ? IRMS.escapeHtml(cat) : '<span class="muted">미분류</span>'}</td>`;
          }
          const opts = `<option value=""${cat === "" ? " selected" : ""}>미분류</option>`
            + CATS.map((c) => `<option value="${c}"${c === cat ? " selected" : ""}>${c}</option>`).join("");
          return `<td><select class="input recipe-cat-select" data-recipe-id="${recipe.id}">${opts}</select></td>`;
        };

        // 품목코드 셀 — 표시 전용. 인라인 편집은 레시피 등록·수정 탭으로 이관
        // (code-edit-relocate §1). 분류 드롭다운은 이 셀과 무관하게 유지.
        const productCodeCell = (recipe) => {
          const code = recipe.productCode || "";
          return `<td class="recipe-code-cell">${code ? IRMS.escapeHtml(code) : '<span class="muted">-</span>'}</td>`;
        };

        // 반응기 셀 — 책임자는 체크박스로 바로 토글(변경 즉시 저장), 그 외는 읽기 전용 텍스트.
        // 분류 셀 편집 패턴과 동일 — PUT /api/recipes/{id}/use-reactor.
        const reactorCell = (recipe) => {
          const on = !!recipe.useReactor;
          if (!ctx.canManage) {
            return `<td>${on ? "사용" : '<span class="muted">-</span>'}</td>`;
          }
          return `<td><input type="checkbox" class="recipe-reactor-toggle" data-recipe-id="${recipe.id}"${on ? " checked" : ""} title="반응기 진행 여부" /></td>`;
        };

        // 파생 셀 — 반응기 셀과 동일 패턴, PUT /api/recipes/{id}/derived. 파생=이월 사용 레시피.
        const derivedCell = (recipe) => {
          const on = !!recipe.isDerived;
          if (!ctx.canManage) {
            return `<td>${on ? "파생" : '<span class="muted">-</span>'}</td>`;
          }
          return `<td><input type="checkbox" class="recipe-derived-toggle" data-recipe-id="${recipe.id}"${on ? " checked" : ""} title="파생(이전 총량 이월) 여부" /></td>`;
        };

        // 1차 셀 — 책임자는 드롭다운으로 개정 없이 이 레시피(2차)의 1차를 바로 지정
        // (PUT /api/recipes/{id}/stage1). 그 외는 연결된 1차명 텍스트. 옵션은 포커스 시
        // 채운다(행마다 전체 목록을 미리 그리면 N² DOM 이 되므로 지연 로드).
        const stage1Cell = (recipe) => {
          if (!ctx.canManage) {
            return `<td>${recipe.stage1ProductName ? IRMS.escapeHtml(recipe.stage1ProductName) : '<span class="muted">-</span>'}</td>`;
          }
          const cur = recipe.stage1RecipeId != null ? String(recipe.stage1RecipeId) : "";
          const label = cur ? IRMS.escapeHtml(recipe.stage1ProductName || cur) : "없음";
          return `<td><select class="input recipe-stage1-select" data-recipe-id="${recipe.id}" data-cur="${cur}" title="이 레시피(2차)의 1차 레시피 — 개정 없이 바로 지정"><option value="${cur}">${label}</option></select></td>`;
        };

        // 한 레시피 행 — stagePin('1차'/'2차') 이 있으면 가족 멤버로 표시.
        const rowHtml = (recipe, stagePin) => {
          const pin = stagePin
            ? `<span class="stage-pin ${stagePin === "1차" ? "one" : "two"}">${stagePin}</span> `
            : "";
          return `
              <tr class="history-row${stagePin ? " family-member" : ""}" data-recipe-id="${recipe.id}">
                <td>${recipe.id}</td>
                <td class="product-cell">${pin}${IRMS.escapeHtml(recipe.productName)}${recipe.isDhr ? ' <span class="chip-dhr">DHR 전용</span>' : ''}</td>
                ${productCodeCell(recipe)}
                ${categoryCell(recipe)}
                ${reactorCell(recipe)}
                ${derivedCell(recipe)}
                ${stage1Cell(recipe)}
                <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
                <td>${IRMS.escapeHtml(recipe.createdBy || "-")}</td>
                <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
                <td>${(recipe.items || []).length}</td>
              </tr>`;
        };

        // 1차/2차 가족 묶음 — 1차 하나 아래 그 1차를 쓰는 2차를 **전부** 묶어 표시한다.
        // 가족은 먼저 등장한 멤버 위치에 나타나고(최신순 반영), 다른 멤버는 그 자리로 끌어온다.
        //
        // 종전엔 1차의 자식을 rows.find 로 **하나만** 찾고, 이미 그린 1차인지 확인하지 않아
        // 1차를 여러 2차가 공유하면 같은 1차 행이 2차 수만큼 복제됐다(SBCT-1 이 'SBCT-A
        // 가족'과 'SBCT-B 가족'에 각각 한 번씩 — 2026-08-06 화면 확인). 가족 이름표도 2차
        // 이름을 써서 한 1차가 여러 '가족'으로 쪼개졌다. 이제 1차 기준으로 한 번만 묶는다.
        const byId = new Map(rows.map((r) => [r.id, r]));
        const childrenOf = new Map();   // 1차 id → [2차...]
        rows.forEach((r) => {
          if (!r.stage1RecipeId || !byId.has(r.stage1RecipeId)) return;
          const list = childrenOf.get(r.stage1RecipeId) || [];
          list.push(r);
          childrenOf.set(r.stage1RecipeId, list);
        });
        const emitted = new Set();
        const COLSPAN = 11;
        const parts = [];
        rows.forEach((r) => {
          if (emitted.has(r.id)) return;
          // r 이 1차면 자기 id, 2차면 자기 1차의 id — 어느 쪽으로 만나든 같은 가족을 그린다.
          const oneId = childrenOf.has(r.id)
            ? r.id
            : (r.stage1RecipeId && byId.has(r.stage1RecipeId) ? r.stage1RecipeId : null);
          if (oneId == null) {
            parts.push(rowHtml(r));
            emitted.add(r.id);
            return;
          }
          if (emitted.has(oneId)) return;   // 이 가족은 이미 그렸다(공유 1차의 두 번째 2차)
          const one = byId.get(oneId);
          const kids = childrenOf.get(oneId) || [];
          const kidNames = kids.map((k) => k.productName).filter(Boolean).join(", ");
          // 1차 하나를 2차 여럿이 쓰는 경우를 눈에 띄게 한다 — 이 1차를 고치거나
          // 취소하면 아래 2차가 전부 영향을 받는다는 뜻이다.
          const sharedChip = kids.length > 1
            ? '<span class="family-shared-chip">공유 1차</span> '
            : "";
          // 저장된 링크가 옛 1차 버전을 가리키는 멤버 — 서버가 현재 버전으로 이어 줬다.
          const staleCount = kids.filter((k) => k.stage1Superseded).length;
          const staleNote = staleCount
            ? `<span class="family-stale-note"> · 2차 ${staleCount}종은 옛 1차 버전에 연결돼 있어 현재 버전으로 이어 표시합니다</span>`
            : "";
          parts.push(
            `<tr class="family-head-row"><td colspan="${COLSPAN}">`
            + sharedChip
            + `◆ ${IRMS.escapeHtml(one.productName)} · 2단 제조 가족`
            + `<span class="muted"> — 2차 ${kids.length}종${kidNames ? `: ${IRMS.escapeHtml(kidNames)}` : ""}</span>`
            + staleNote
            + `</td></tr>`,
          );
          parts.push(rowHtml(one, "1차"));
          emitted.add(one.id);
          kids.forEach((k) => {
            parts.push(rowHtml(k, "2차"));
            emitted.add(k.id);
          });
        });
        dom.historyBody.innerHTML = parts.join("");

        // 분류 드롭다운 — 변경 즉시 PUT /api/recipes/{id}/category. 클릭이 행 확장으로
        // 번지지 않게 막는다(행 클릭 = 상세 펼침). x-csrftoken 헤더 직접 부착.
        dom.historyBody.querySelectorAll(".recipe-cat-select").forEach((sel) => {
          sel.addEventListener("click", (e) => e.stopPropagation());
          sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const rid = Number(sel.dataset.recipeId);
            const category = sel.value ? sel.value : null;
            try {
              const headers = { "Content-Type": "application/json" };
              const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
              if (token) headers["x-csrftoken"] = token;
              const resp = await fetch(`/api/recipes/${rid}/category`, {
                method: "PUT",
                credentials: "same-origin",
                headers,
                body: JSON.stringify({ category }),
              });
              if (!resp.ok) {
                let msg = `Request failed (${resp.status})`;
                try { const p = await resp.json(); if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail); } catch (_e) { /* noop */ }
                throw new Error(msg);
              }
              await resp.json();
              IRMS.notify(category ? `분류를 '${category}'(으)로 지정했습니다.` : "분류를 미분류로 되돌렸습니다.", "success");
            } catch (err) {
              IRMS.notify(`분류 저장 실패: ${err.message}`, "error");
            }
          });
        });

        // 반응기 토글 — 변경 즉시 PUT /api/recipes/{id}/use-reactor. 분류 드롭다운과 동일한
        // CSRF 부착 패턴. 클릭이 행 확장으로 번지지 않게 막는다(행 클릭 = 상세 펼침).
        dom.historyBody.querySelectorAll(".recipe-reactor-toggle").forEach((cb) => {
          cb.addEventListener("click", (e) => e.stopPropagation());
          cb.addEventListener("change", async (e) => {
            e.stopPropagation();
            const rid = Number(cb.dataset.recipeId);
            const useReactor = !!cb.checked;
            try {
              const headers = { "Content-Type": "application/json" };
              const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
              if (token) headers["x-csrftoken"] = token;
              const resp = await fetch(`/api/recipes/${rid}/use-reactor`, {
                method: "PUT",
                credentials: "same-origin",
                headers,
                body: JSON.stringify({ use_reactor: useReactor }),
              });
              if (!resp.ok) {
                let msg = `Request failed (${resp.status})`;
                try { const p = await resp.json(); if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail); } catch (_e) { /* noop */ }
                throw new Error(msg);
              }
              await resp.json();
              IRMS.notify(useReactor ? "반응기 진행으로 지정했습니다." : "반응기 진행을 해제했습니다.", "success");
            } catch (err) {
              // 저장 실패 시 체크박스를 이전 상태로 되돌려 표시와 서버를 맞춘다.
              cb.checked = !useReactor;
              IRMS.notify(`반응기 저장 실패: ${err.message}`, "error");
            }
          });
        });

        // 파생 토글 — 변경 즉시 PUT /api/recipes/{id}/derived. 반응기 토글과 동일 패턴.
        dom.historyBody.querySelectorAll(".recipe-derived-toggle").forEach((cb) => {
          cb.addEventListener("click", (e) => e.stopPropagation());
          cb.addEventListener("change", async (e) => {
            e.stopPropagation();
            const rid = Number(cb.dataset.recipeId);
            const isDerived = !!cb.checked;
            try {
              const headers = { "Content-Type": "application/json" };
              const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
              if (token) headers["x-csrftoken"] = token;
              const resp = await fetch(`/api/recipes/${rid}/derived`, {
                method: "PUT",
                credentials: "same-origin",
                headers,
                body: JSON.stringify({ is_derived: isDerived }),
              });
              if (!resp.ok) {
                let msg = `Request failed (${resp.status})`;
                try { const p = await resp.json(); if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail); } catch (_e) { /* noop */ }
                throw new Error(msg);
              }
              await resp.json();
              IRMS.notify(isDerived ? "파생 레시피로 지정했습니다." : "파생 지정을 해제했습니다.", "success");
            } catch (err) {
              // 저장 실패 시 체크박스를 이전 상태로 되돌려 표시와 서버를 맞춘다.
              cb.checked = !isDerived;
              IRMS.notify(`파생 저장 실패: ${err.message}`, "error");
            }
          });
        });

        // 1차 지정 드롭다운 — 포커스 시 후보 채움(지연), 변경 즉시 PUT /api/recipes/{id}/stage1.
        // 개정을 만들지 않고 바로 연결/해제하고 가족 묶음을 다시 그린다. 클릭이 행 확장으로
        // 번지지 않게 막는다. 반응기/파생 토글과 동일한 CSRF 부착 패턴.
        dom.historyBody.querySelectorAll(".recipe-stage1-select").forEach((sel) => {
          sel.addEventListener("click", (e) => e.stopPropagation());
          sel.addEventListener("focus", () => {
            if (sel.dataset.filled) return;
            const rid = Number(sel.dataset.recipeId);
            const cur = sel.dataset.cur || "";
            sel.innerHTML = `<option value=""${cur === "" ? " selected" : ""}>없음</option>`
              + rows.filter((o) => o.id !== rid)
                  .map((o) => `<option value="${o.id}"${String(o.id) === cur ? " selected" : ""}>${IRMS.escapeHtml(o.productName)}</option>`)
                  .join("");
            sel.dataset.filled = "1";
          });
          sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const rid = Number(sel.dataset.recipeId);
            const val = sel.value ? Number(sel.value) : null;
            try {
              const headers = { "Content-Type": "application/json" };
              const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
              if (token) headers["x-csrftoken"] = token;
              const resp = await fetch(`/api/recipes/${rid}/stage1`, {
                method: "PUT",
                credentials: "same-origin",
                headers,
                body: JSON.stringify({ stage1_recipe_id: val }),
              });
              if (!resp.ok) {
                let msg = `Request failed (${resp.status})`;
                try { const p = await resp.json(); if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail); } catch (_e) { /* noop */ }
                throw new Error(msg);
              }
              await resp.json();
              IRMS.notify(val ? "1차 레시피를 연결했습니다." : "1차 연결을 해제했습니다.", "success");
              renderHistory();  // 가족 묶음 즉시 반영
            } catch (err) {
              IRMS.notify(`1차 연결 실패: ${err.message}`, "error");
              renderHistory();  // 실패 시 서버 값으로 다시 그림
            }
          });
        });

        // Accordion: row click to expand detail
        dom.historyBody.querySelectorAll(".history-row").forEach((row) => {
          row.style.cursor = "pointer";
          row.addEventListener("click", async () => {
            const recipeId = Number(row.dataset.recipeId);
            const existing = row.nextElementSibling;
            if (existing && existing.classList.contains("history-detail-row")) {
              existing.remove();
              row.classList.remove("selected");
              return;
            }
            // Close any other open detail
            dom.historyBody.querySelectorAll(".history-detail-row").forEach((r) => r.remove());
            dom.historyBody.querySelectorAll(".history-row.selected").forEach((r) => r.classList.remove("selected"));

            row.classList.add("selected");
            try {
              const detail = await IRMS.getRecipeDetail(recipeId);
              const items = detail.items || [];
              const itemsHtml = items.length
                ? items.map((it) =>
                    `<span class="detail-chip">${IRMS.escapeHtml(it.material_name)}: ${IRMS.escapeHtml(String(it.value))}</span>`
                  ).join("")
                : '<span class="muted">재료 없음</span>';

              const detailRow = document.createElement("tr");
              detailRow.classList.add("history-detail-row");
              const dhrActionLabel = detail.is_dhr ? "DHR 전용 해제" : "DHR 전용 지정";
              detailRow.innerHTML = `<td colspan="11">
                <div class="history-detail-content">
                  <div class="detail-items">${itemsHtml}</div>
                  <div class="history-attrs" data-attrs-for="${recipeId}"></div>
                  <div class="detail-actions">
                    <button class="btn btn-sm history-copy-btn" data-recipe-id="${recipeId}">엑셀로 복사</button>
                    <button class="btn btn-sm accent history-edit-btn" data-recipe-id="${recipeId}">수정 등록</button>
                    <button class="btn btn-sm history-version-btn" data-recipe-id="${recipeId}">버전 이력</button>
                    <button class="btn btn-sm history-dhr-btn" data-recipe-id="${recipeId}">${dhrActionLabel}</button>
                    ${detail.status !== "canceled"
                      ? `<button class="btn btn-sm warn history-cancel-btn" data-recipe-id="${recipeId}">등록 취소</button>`
                      : `<button class="btn btn-sm accent history-restore-btn" data-recipe-id="${recipeId}">취소 해제</button>`}
                    <button class="btn btn-sm danger history-delete-btn" data-recipe-id="${recipeId}">레시피 삭제</button>
                    <button class="btn btn-sm danger history-delete-with-records-btn" data-recipe-id="${recipeId}">레시피+기록 삭제</button>
                  </div>
                </div>
              </td>`;
              // 속성 편집기(기준 자재·허용 편차·분류·투입 로스 보정) — detailRow 스코프 내 렌더.
              // 펼침은 한 번에 한 행만(위에서 다른 detail-row 를 닫는다)이므로 id 충돌은 없지만,
              // 안전하게 detailRow.querySelector 스코프로 저장 핸들러를 건다.
              await renderAttributePanel(detailRow, detail, recipeId);
              row.after(detailRow);
              if (!ctx.canManage) {
                detailRow
                  .querySelectorAll(
                    ".history-edit-btn, .history-dhr-btn, .history-cancel-btn, .history-restore-btn, .history-delete-btn, .history-delete-with-records-btn",
                  )
                  .forEach((button) => {
                    button.hidden = true;
                    button.disabled = true;
                  });
              }

              detailRow.querySelector(".history-copy-btn").addEventListener("click", async (e) => {
                e.stopPropagation();
                try {
                  await ctx.copyToClipboard(detail.tsv);
                  IRMS.notify("클립보드에 복사되었습니다. 엑셀에서 Ctrl+V로 붙여넣으세요.", "success");
                } catch (err) {
                  IRMS.notify(`복사 실패: ${err.message}`, "error");
                }
              });

              detailRow.querySelector(".history-edit-btn").addEventListener("click", async (e) => {
                e.stopPropagation();
                try {
                  await ctx.recipeEditLoader.loadRecipeForEdit(recipeId, "레시피 현황");
                } catch (err) {
                  IRMS.notify(`수정 등록 준비 실패: ${err.message}`, "error");
                }
              });

              detailRow.querySelector(".history-version-btn").addEventListener("click", (e) => {
                e.stopPropagation();
                // 2026-08-06 재설계 — 버전 비교 탭으로 전환 + 이 반제품 자동 선택.
                // 모달(handleLookupHistory) 대신 탭 렌더러(openVersionCompareTab) 로.
                if (ctx.switchToLookupTab) {
                  ctx.switchToLookupTab(recipeId);
                }
              });

              detailRow.querySelector(".history-dhr-btn").addEventListener("click", async (e) => {
                e.stopPropagation();
                try {
                  await IRMS.setRecipeDhr(recipeId, !detail.is_dhr);
                  IRMS.notify(!detail.is_dhr ? "DHR 전용으로 지정했습니다." : "DHR 전용을 해제했습니다.", "success");
                  renderHistory();
                } catch (err) {
                  IRMS.notify(`DHR 변경 실패: ${err.message}`, "error");
                }
              });

              const cancelBtn = detailRow.querySelector(".history-cancel-btn");
              if (cancelBtn) {
                cancelBtn.addEventListener("click", async (e) => {
                  e.stopPropagation();
                  // 결과를 명시한다 — 취소하면 현장 배합 화면의 레시피 목록에서 사라진다.
                  const reason = window.prompt(
                    [
                      "이 레시피를 등록 취소합니다.",
                      "취소하면 배합 화면의 레시피 목록에서 사라집니다(기록은 남습니다).",
                      "나중에 이 화면에서 '취소 해제'로 되돌릴 수 있습니다.",
                      "",
                      "사유를 입력하세요.",
                    ].join("\n")
                  );
                  if (reason === null) return;
                  if (!reason.trim()) { IRMS.notify("사유를 입력해야 취소할 수 있습니다.", "error"); return; }
                  try {
                    await IRMS.updateRecipeStatus(recipeId, "cancel", reason.trim());
                    IRMS.notify("레시피를 취소했습니다 — 필요하면 '취소 해제'로 되돌릴 수 있습니다.", "success");
                    renderHistory();
                  } catch (err) {
                    IRMS.notify(`취소 실패: ${err.message}`, "error");
                  }
                });
              }

              const restoreBtn = detailRow.querySelector(".history-restore-btn");
              if (restoreBtn) {
                restoreBtn.addEventListener("click", async (e) => {
                  e.stopPropagation();
                  try {
                    await IRMS.updateRecipeStatus(recipeId, "restore");
                    IRMS.notify("레시피 취소를 해제했습니다 — 배합 화면 목록에 다시 나타납니다.", "success");
                    renderHistory();
                  } catch (err) {
                    IRMS.notify(`취소 해제 실패: ${err.message}`, "error");
                  }
                });
              }

              async function deleteRecipeFromHistory(deleteBlendRecords) {
                // 확인 전에 규모를 알려준다 — 예전에는 삭제가 끝난 뒤에야 건수가 나왔다.
                const linked = Number(detail.linked_record_count || 0);
                const scope = linked
                  ? `이 레시피로 만든 배합 기록이 ${linked}건 있습니다.`
                  : "이 레시피로 만든 배합 기록은 없습니다.";
                const message = deleteBlendRecords
                  ? [
                      scope,
                      "",
                      `그 ${linked}건을 레시피와 함께 영구 삭제합니다.`,
                      "되돌릴 수 없습니다 — 정말 진행할까요?",
                    ].join("\n")
                  : [
                      scope,
                      "",
                      "레시피만 삭제하고 기록은 남깁니다(레시피 연결만 끊김).",
                      "계속할까요?",
                    ].join("\n");
                if (!window.confirm(message)) return;
                try {
                  const result = await IRMS.deleteRecipe(recipeId, deleteBlendRecords);
                  const linkedCount = Number(result.linked_record_count || 0);
                  const suffix = linkedCount
                    ? ` 연결 기록 ${linkedCount}건 ${deleteBlendRecords ? "삭제" : "보존"}`
                    : "";
                  IRMS.notify(`레시피를 삭제했습니다.${suffix}`, "success");
                  renderHistory();
                } catch (err) {
                  IRMS.notify(`삭제 실패: ${err.message}`, "error");
                }
              }

              const deleteBtn = detailRow.querySelector(".history-delete-btn");
              if (deleteBtn) {
                deleteBtn.addEventListener("click", (e) => {
                  e.stopPropagation();
                  deleteRecipeFromHistory(false);
                });
              }

              const deleteWithRecordsBtn = detailRow.querySelector(".history-delete-with-records-btn");
              if (deleteWithRecordsBtn) {
                deleteWithRecordsBtn.addEventListener("click", (e) => {
                  e.stopPropagation();
                  deleteRecipeFromHistory(true);
                });
              }
            } catch (error) {
              IRMS.notify(`상세 조회 실패: ${error.message}`, "error");
            }
          });
        });
      } catch (error) {
        IRMS.notify(`이력 조회 실패: ${error.message}`, "error");
      }
    }

    // 전체 Excel 내보내기 — 책임자 전용 엔드포인트로 바로 이동(서버가 권한 통제).
    const exportBtn = document.getElementById("history-export-btn");
    if (exportBtn) {
      exportBtn.addEventListener("click", () => {
        window.location.assign("/api/recipes/export");
      });
    }

    // ── 속성 편집기(3단계 정리 2026-08-06) — recipe-lookup.js 에서 이전 ──
    // 현황 행 펼침(detailRow) 안에 4줄(기준 자재·허용 편차·분류·투입 로스 보정)을 그린다.
    // detailRow 스코프 내 querySelector 로 저장 핸들러를 묶는다(여러 행 동시 열림 방지).
    // 책임자가 아니면 현재값만 읽기 전용으로 표시.

    async function renderAttributePanel(detailRow, detail, recipeId) {
      const wrap = detailRow.querySelector(".history-attrs");
      if (!wrap) return;
      const canManage = !!ctx.canManage;
      const currentName = detail.anchor_material_name || "";
      const itemNames = (detail.items || [])
        .map((it) => it.material_name)
        .filter((n) => !!n);
      const seen = new Set();
      const uniq = [];
      for (const n of itemNames) {
        if (!seen.has(n)) { seen.add(n); uniq.push(n); }
      }

      const attrRow = (labelHtml, currentHtml, editorHtml) =>
        `<div class="lookup-attr-row">${labelHtml}`
        + `<span class="lookup-attr-current"><span class="muted">현재:</span> ${currentHtml}</span>`
        + (editorHtml ? `<span class="lookup-attr-editor">${editorHtml}</span>` : "")
        + `</div>`;

      // 기준 자재
      const currentText = currentName
        ? IRMS.escapeHtml(currentName)
        : '<span class="muted">없음</span>';
      const anchorOptions = '<option value="">없음</option>'
        + uniq.map((n) => `<option value="${IRMS.escapeHtml(n)}"${n === currentName ? " selected" : ""}>${IRMS.escapeHtml(n)}</option>`).join("");
      const anchorEditor = canManage
        ? `<select class="input attr-anchor-select">${anchorOptions}</select>` +
          `<button class="btn attr-anchor-save" type="button">저장</button>`
        : "";

      // 허용 편차
      const tolCurrent = detail.tolerance_g != null ? Number(detail.tolerance_g) : null;
      const tolCurrentText = tolCurrent != null && Number.isFinite(tolCurrent)
        ? `±${IRMS.escapeHtml(String(tolCurrent))} g`
        : '<span class="muted">기본 ±0.05 g</span>';
      const tolEditor = canManage
        ? `<input class="input attr-tolerance-input" type="number" step="0.01" min="0" `
          + `placeholder="선택 · 비우면 기본 0.05" value="${tolCurrent != null && Number.isFinite(tolCurrent) ? IRMS.escapeHtml(String(tolCurrent)) : ""}" />`
          + `<button class="btn attr-tolerance-save" type="button">저장</button>`
        : "";

      // 분류
      const CATS = ["약품", "합성", "잉크", "용수"];
      const catCurrent = detail.category || "";
      const catCurrentText = catCurrent
        ? IRMS.escapeHtml(catCurrent)
        : '<span class="muted">미분류</span>';
      const catOptions = '<option value="">미분류</option>'
        + CATS.map((c) => `<option value="${c}"${c === catCurrent ? " selected" : ""}>${c}</option>`).join("");
      const catEditor = canManage
        ? `<select class="input attr-category-select">${catOptions}</select>`
          + `<button class="btn attr-category-save" type="button">저장</button>`
        : "";

      wrap.innerHTML =
        attrRow(`<label class="filter-label">기준 자재</label>`, `<span class="attr-anchor-current">${currentText}</span>`, anchorEditor) +
        attrRow(`<label class="filter-label">허용 편차</label>`, `<span class="attr-tolerance-current">${tolCurrentText}</span>`, tolEditor) +
        attrRow(`<label class="filter-label">분류</label>`, `<span class="attr-category-current">${catCurrentText}</span>`, catEditor) +
        renderLossCompBlock(detail, uniq, canManage);

      if (canManage) {
        wrap.querySelector(".attr-anchor-save")?.addEventListener("click", () => handleSaveAnchor(detailRow, recipeId));
        wrap.querySelector(".attr-tolerance-save")?.addEventListener("click", () => handleSaveTolerance(detailRow, recipeId));
        wrap.querySelector(".attr-category-save")?.addEventListener("click", () => handleSaveCategory(detailRow, recipeId));
        wireLossCompEditor(detailRow, recipeId, uniq);
      }
    }

    function renderLossCompBlock(detail, itemNames, canManage) {
      const existing = (detail.items || []).filter(
        (it) => Number(it.loss_comp_g) > 0 && it.material_name,
      );
      const currentText = existing.length
        ? existing.map((it) => `<span class="lookup-losscomp-badge">+${it.loss_comp_g}g 보정</span> ${IRMS.escapeHtml(it.material_name)}`).join(" · ")
        : '<span class="muted">없음</span>';
      if (!canManage) {
        return `<div class="lookup-attr-row"><label class="filter-label">투입 로스 보정</label>`
          + `<span class="lookup-attr-current"><span class="muted">현재:</span> ${currentText}</span></div>`;
      }
      const options = itemNames.length
        ? itemNames.map((n) => `<option value="${IRMS.escapeHtml(n)}">${IRMS.escapeHtml(n)}</option>`).join("")
        : "";
      const rowsHtml = existing.map((it) => lossCompRowHtml(itemNames, it.material_name, it.loss_comp_g)).join("");
      return `<div class="lookup-attr-row lookup-attr-row-block">`
        + `<label class="filter-label lookup-losscomp-label">투입 로스 보정</label>`
        + `<span class="lookup-attr-current"><span class="muted">현재:</span> <span class="attr-losscomp-current">${currentText}</span></span>`
        + `<div class="lookup-losscomp-rows attr-losscomp-rows">${rowsHtml}</div>`
        + `<div class="lookup-losscomp-actions">`
        + `<button class="btn btn-sm attr-losscomp-add" type="button">+ 보정 추가</button>`
        + `<button class="btn attr-losscomp-save" type="button">저장</button>`
        + `</div>`
        + `<p class="imp-attr-desc lookup-losscomp-desc">지정 자재는 계량 목표가 (비율 환산량 + 보정 g)이 됩니다. 붓는 과정 로스가 있는 파우더용 — 기록·출력엔 보정 포함량이 그대로 남습니다.</p>`
        + `<p class="imp-attr-desc lookup-losscomp-desc">기본은 품목코드 탭의 자재 마스터에서 지정합니다 — 여기는 이 레시피만의 예외값(마스터보다 우선).</p>`
        + (itemNames.length ? "" : '<p class="login-error attr-losscomp-error">BOM 자재가 없습니다.</p>')
        + `<input type="hidden" class="attr-losscomp-options" value="" data-options="${IRMS.escapeHtml(options)}" />`
        + `</div>`;
    }

    function lossCompRowHtml(itemNames, selectedName, value) {
      const opts = itemNames.length
        ? itemNames.map((n) => `<option value="${IRMS.escapeHtml(n)}"${n === selectedName ? " selected" : ""}>${IRMS.escapeHtml(n)}</option>`).join("")
        : "";
      return `<div class="lookup-losscomp-row">`
        + `<select class="input attr-losscomp-mat">${opts}</select>`
        + `<input class="input attr-losscomp-g" type="number" step="0.1" min="0" max="100" placeholder="보정 g" value="${value != null ? IRMS.escapeHtml(String(value)) : ""}" />`
        + `<button class="btn btn-sm attr-losscomp-del" type="button" title="삭제">✕</button>`
        + `</div>`;
    }

    function wireLossCompEditor(detailRow, recipeId, itemNames) {
      const wrap = detailRow.querySelector(".history-attrs");
      if (!wrap) return;
      const rowsEl = wrap.querySelector(".attr-losscomp-rows");
      const addBtn = wrap.querySelector(".attr-losscomp-add");
      const saveBtn = wrap.querySelector(".attr-losscomp-save");
      if (addBtn) addBtn.addEventListener("click", () => {
        if (!rowsEl) return;
        const tmp = document.createElement("div");
        tmp.innerHTML = lossCompRowHtml(itemNames, "", "");
        rowsEl.appendChild(tmp.firstChild);
      });
      if (rowsEl) rowsEl.addEventListener("click", (e) => {
        const del = e.target.closest(".attr-losscomp-del");
        if (del) del.closest(".lookup-losscomp-row").remove();
      });
      if (saveBtn) saveBtn.addEventListener("click", () => handleSaveLossComp(detailRow, recipeId));
    }

    // ── 저장 핸들러(모두 detailRow 스코프) ──
    async function handleSaveAnchor(detailRow, recipeId) {
      const wrap = detailRow.querySelector(".history-attrs");
      const sel = wrap && wrap.querySelector(".attr-anchor-select");
      const saveBtn = wrap && wrap.querySelector(".attr-anchor-save");
      if (!sel) return;
      let materialId = null;
      const chosenName = sel.value.trim();
      if (chosenName) {
        try {
          const detail = await IRMS.getRecipeDetail(recipeId);
          const match = (detail.items || []).find((it) => it.material_name === chosenName);
          if (!match || match.material_id == null) {
            IRMS.notify("선택한 자재의 식별자를 찾을 수 없습니다.", "error");
            return;
          }
          materialId = Number(match.material_id);
        } catch (error) {
          IRMS.notify(`기준 자재 저장 실패: ${error.message}`, "error");
          return;
        }
      }
      if (saveBtn) IRMS.btnLoading(saveBtn, true);
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const resp = await fetch(`/api/recipes/${recipeId}/anchor`, {
          method: "PUT", credentials: "same-origin", headers,
          body: JSON.stringify({ material_id: materialId }),
        });
        if (!resp.ok) throw new Error(await fetchErrDetail(resp));
        await resp.json();
        const cur = wrap.querySelector(".attr-anchor-current");
        if (cur) cur.innerHTML = chosenName ? IRMS.escapeHtml(chosenName) : '<span class="muted">없음</span>';
        IRMS.notify(chosenName ? `기준 자재를 '${chosenName}'(으)로 지정했습니다.` : "기준 자재를 해제했습니다.", "success");
      } catch (error) {
        IRMS.notify(`기준 자재 저장 실패: ${error.message}`, "error");
      } finally {
        if (saveBtn) IRMS.btnLoading(saveBtn, false);
      }
    }

    async function handleSaveTolerance(detailRow, recipeId) {
      const wrap = detailRow.querySelector(".history-attrs");
      const input = wrap && wrap.querySelector(".attr-tolerance-input");
      const saveBtn = wrap && wrap.querySelector(".attr-tolerance-save");
      if (!input) return;
      const raw = (input.value || "").trim();
      let toleranceG = null;
      let label;
      if (raw !== "") {
        const v = Number(raw);
        if (!Number.isFinite(v) || !(v > 0)) {
          IRMS.notify("허용 편차는 0 초과 숫자여야 합니다. (비우면 기본 0.05g)", "error");
          return;
        }
        toleranceG = v;
        label = `±${v} g`;
      } else {
        label = '<span class="muted">기본 ±0.05 g</span>';
      }
      if (saveBtn) IRMS.btnLoading(saveBtn, true);
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const resp = await fetch(`/api/recipes/${recipeId}/tolerance`, {
          method: "PUT", credentials: "same-origin", headers,
          body: JSON.stringify({ tolerance_g: toleranceG }),
        });
        if (!resp.ok) throw new Error(await fetchErrDetail(resp));
        await resp.json();
        const cur = wrap.querySelector(".attr-tolerance-current");
        if (cur) cur.innerHTML = label;
        IRMS.notify(toleranceG != null ? `허용 편차를 ±${toleranceG} g 으로 지정했습니다.` : "허용 편차를 기본값(±0.05 g)으로 되돌렸습니다.", "success");
      } catch (error) {
        IRMS.notify(`허용 편차 저장 실패: ${error.message}`, "error");
      } finally {
        if (saveBtn) IRMS.btnLoading(saveBtn, false);
      }
    }

    async function handleSaveCategory(detailRow, recipeId) {
      const wrap = detailRow.querySelector(".history-attrs");
      const sel = wrap && wrap.querySelector(".attr-category-select");
      const saveBtn = wrap && wrap.querySelector(".attr-category-save");
      if (!sel) return;
      const category = sel.value ? sel.value : null;
      if (saveBtn) IRMS.btnLoading(saveBtn, true);
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const resp = await fetch(`/api/recipes/${recipeId}/category`, {
          method: "PUT", credentials: "same-origin", headers,
          body: JSON.stringify({ category }),
        });
        if (!resp.ok) throw new Error(await fetchErrDetail(resp));
        await resp.json();
        const cur = wrap.querySelector(".attr-category-current");
        if (cur) cur.innerHTML = category ? IRMS.escapeHtml(category) : '<span class="muted">미분류</span>';
        IRMS.notify(category ? `분류를 '${category}'(으)로 지정했습니다.` : "분류를 미분류로 되돌렸습니다.", "success");
      } catch (error) {
        IRMS.notify(`분류 저장 실패: ${error.message}`, "error");
      } finally {
        if (saveBtn) IRMS.btnLoading(saveBtn, false);
      }
    }

    async function handleSaveLossComp(detailRow, recipeId) {
      const wrap = detailRow.querySelector(".history-attrs");
      const saveBtn = wrap && wrap.querySelector(".attr-losscomp-save");
      const rowsEl = wrap && wrap.querySelector(".attr-losscomp-rows");
      const errEl = wrap && wrap.querySelector(".attr-losscomp-error");
      if (errEl) errEl.remove();
      const items = [];
      const seen = new Set();
      let bad = "";
      if (rowsEl) {
        rowsEl.querySelectorAll(".lookup-losscomp-row").forEach((row) => {
          const matSel = row.querySelector(".attr-losscomp-mat");
          const gInput = row.querySelector(".attr-losscomp-g");
          const name = (matSel && matSel.value || "").trim();
          const rawG = (gInput && gInput.value || "").trim();
          if (!name && !rawG) return;
          const g = Number(rawG);
          if (!name) { bad = "자재를 선택하세요."; return; }
          if (!Number.isFinite(g) || g <= 0 || g > 100) { bad = `보정값은 0 초과 100 이하여야 합니다: ${name}`; return; }
          if (seen.has(name)) { bad = `같은 자재가 중복됩니다: ${name}`; return; }
          seen.add(name);
          items.push({ material_name: name, loss_comp_g: g });
        });
      }
      if (bad) { IRMS.notify(bad, "error"); return; }
      if (saveBtn) IRMS.btnLoading(saveBtn, true);
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const resp = await fetch(`/api/recipes/${recipeId}/loss-comp`, {
          method: "PUT", credentials: "same-origin", headers,
          body: JSON.stringify({ items }),
        });
        if (!resp.ok) throw new Error(await fetchErrDetail(resp));
        await resp.json();
        // 성공 — 속성 영역을 detail 재조회로 다시 그린다.
        const detail = await IRMS.getRecipeDetail(recipeId);
        await renderAttributePanel(detailRow, detail, recipeId);
        IRMS.notify(items.length ? `투입 로스 보정 ${items.length}건을 저장했습니다.` : "투입 로스 보정을 모두 해제했습니다.", "success");
      } catch (error) {
        IRMS.notify(`투입 로스 보정 저장 실패: ${error.message}`, "error");
      } finally {
        if (saveBtn) IRMS.btnLoading(saveBtn, false);
      }
    }

    // 에러 응답 detail 추출 헬퍼(저장 핸들러 공용).
    async function fetchErrDetail(resp) {
      try {
        const payload = await resp.json();
        const d = payload && payload.detail;
        return d && typeof d === "object" && d.message ? d.message
          : (d !== undefined ? String(d) : `Request failed (${resp.status})`);
      } catch (_e) {
        return `Request failed (${resp.status})`;
      }
    }

    return {
      persistHistoryFilters,
      updateHistorySummary,
      restoreHistoryFilters,
      resetHistoryFilters,
      renderHistory,
    };
  };
})();
