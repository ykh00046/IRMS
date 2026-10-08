/**
 * recipe-history module — 이력 tab: filters persistence + history table
 * with accordion detail rows.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Detail row (2026-10-09): two columns + footer.
 *   left  .rh-items     자재 구성 표(#·자재·배합량·비율·보정, '기준' 칩, 합계)
 *   right .rh-settings  설정(기준 자재·허용 편차는 바꾸면 저장, 투입 로스 보정은 [저장])
 *   foot  .rh-actions   주 동작(수정 등록·버전·Excel) | 관리 동작(DHR·취소·삭제)
 *   분류는 현황 행의 인라인 선택에서만 바꾼다.
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

      // 조건을 하나라도 걸었을 때만 요약을 띄운다. 아무 조건도 없을 때의
      // "전체 기준으로 표시 중입니다" 는 화면에 이미 보이는 것을 되풀이할 뿐이다.
      const STATUS_LABELS = { completed: "사용중", canceled: "취소" };
      const parts = [];
      const status = dom.historyStatus.value;
      const search = dom.historySearch.value.trim();
      const from = dom.historyFrom.value;
      const to = dom.historyTo.value;

      if (status) {
        parts.push(`상태 ${STATUS_LABELS[status] || status}`);
      }
      if (search) {
        parts.push(`검색어 "${search}"`);
      }
      if (from || to) {
        parts.push(`기간 ${from || "처음"} ~ ${to || "오늘"}`);
      }

      dom.historySummary.textContent = parts.join(" · ");
      dom.historySummary.hidden = parts.length === 0;
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
          return `<td><select class="input recipe-stage1-select" data-recipe-id="${recipe.id}" data-cur="${cur}" title="이 레시피(2차)의 1차 레시피 · 개정 없이 바로 지정"><option value="${cur}">${label}</option></select></td>`;
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
            + `<span class="muted"> · 2차 ${kids.length}종${kidNames ? `: ${IRMS.escapeHtml(kidNames)}` : ""}</span>`
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
              const detailRow = document.createElement("tr");
              detailRow.classList.add("history-detail-row");
              const dhrActionLabel = detail.is_dhr ? "DHR 전용 해제" : "DHR 전용 지정";
              // 상세 = [자재 구성 표 | 설정] 2단 + 아래 동작 줄(왼쪽 주 동작, 오른쪽 관리 동작).
              detailRow.innerHTML = `<td colspan="11">
                <div class="history-detail-content">
                  <section class="rh-items"></section>
                  <section class="rh-settings history-attrs" data-attrs-for="${recipeId}"></section>
                  <div class="rh-actions">
                    <div class="rh-actions-main">
                      <button class="btn btn-sm accent history-edit-btn" data-recipe-id="${recipeId}">수정 등록</button>
                      <button class="btn btn-sm history-version-btn" data-recipe-id="${recipeId}">버전 이력</button>
                      <button class="btn btn-sm history-versions-btn" data-recipe-id="${recipeId}">버전 관리</button>
                      <button class="btn btn-sm history-copy-btn" data-recipe-id="${recipeId}">엑셀로 복사</button>
                    </div>
                    <div class="rh-actions-manage">
                      <button class="btn btn-sm history-dhr-btn" data-recipe-id="${recipeId}">${dhrActionLabel}</button>
                      ${detail.status !== "canceled"
                        ? `<button class="btn btn-sm warn history-cancel-btn" data-recipe-id="${recipeId}">등록 취소</button>`
                        : `<button class="btn btn-sm accent history-restore-btn" data-recipe-id="${recipeId}">취소 해제</button>`}
                      <button class="btn btn-sm danger history-delete-btn" data-recipe-id="${recipeId}">레시피 삭제</button>
                      <button class="btn btn-sm danger history-delete-with-records-btn" data-recipe-id="${recipeId}">레시피+기록 삭제</button>
                    </div>
                  </div>
                </div>
              </td>`;
              // 자재 구성 표와 설정 칸 — detailRow 스코프 내 렌더.
              // 펼침은 한 번에 한 행만(위에서 다른 detail-row 를 닫는다)이므로 id 충돌은 없지만,
              // 안전하게 detailRow.querySelector 스코프로 저장 핸들러를 건다.
              renderItemsTable(detailRow, detail);
              await renderAttributePanel(detailRow, detail, recipeId);
              row.after(detailRow);
              if (!ctx.canManage) {
                detailRow
                  .querySelectorAll(
                    ".history-edit-btn, .history-versions-btn, .history-dhr-btn, .history-cancel-btn, .history-restore-btn, .history-delete-btn, .history-delete-with-records-btn",
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

              // 버전 관리(책임자) — 판 이름·되돌리기·삭제·정리. 버전 비교는 읽기 전용으로 남긴다.
              detailRow.querySelector(".history-versions-btn").addEventListener("click", (e) => {
                e.stopPropagation();
                if (ctx.switchToVersionsTab) {
                  ctx.switchToVersionsTab(recipeId);
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
                    IRMS.notify("레시피를 취소했습니다. 필요하면 '취소 해제'로 되돌릴 수 있습니다.", "success");
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
                    IRMS.notify("레시피 취소를 해제했습니다. 배합 화면 목록에 다시 나타납니다.", "success");
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
                      "되돌릴 수 없습니다. 정말 진행할까요?",
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

    // ── 현황 행 상세(2026-10-09 재배치) ──
    // 왼쪽 = 자재 구성 표(.rh-items), 오른쪽 = 설정 칸(.rh-settings, 기준 자재·허용 편차·
    // 투입 로스 보정), 아래 = 동작 줄(.rh-actions). 분류는 현황 행의 인라인 선택에서만 바꾼다.
    // 컨트롤 자체가 현재값을 보여 준다("현재:" 중복 표기 없음). 기준 자재·허용 편차는 바꾸면
    // 바로 저장, 보정은 여러 줄이라 [저장]으로 한꺼번에 보낸다.
    // detailRow 스코프 내 querySelector 로 저장 핸들러를 묶는다(여러 행 동시 열림 방지).
    // 책임자가 아니면 같은 칸을 값만 읽기 전용으로 표시.

    function formatFixed(value, digits) {
      return Number(value).toLocaleString("ko-KR", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
    }

    function uniqueMaterialNames(detail) {
      const seen = new Set();
      const uniq = [];
      for (const it of detail.items || []) {
        const n = it.material_name;
        if (n && !seen.has(n)) { seen.add(n); uniq.push(n); }
      }
      return uniq;
    }

    // 자재 구성 표 — 저장 후(기준 자재·보정) 다시 그릴 수 있게 detail 을 행에 붙여 둔다.
    function renderItemsTable(detailRow, detail) {
      detailRow._rhDetail = detail;
      const wrap = detailRow.querySelector(".rh-items");
      if (!wrap) return;
      const items = detail.items || [];
      if (!items.length) {
        wrap.innerHTML = '<p class="empty-state">자재가 없습니다.</p>';
        return;
      }
      const anchorName = detail.anchor_material_name || "";
      const weights = items.map((it) => Number(it.value));
      const total = weights.reduce((sum, w) => sum + (Number.isFinite(w) ? w : 0), 0);
      const rowsHtml = items.map((it, idx) => {
        const w = weights[idx];
        const name = IRMS.escapeHtml(it.material_name || "");
        const anchorChip = anchorName && it.material_name === anchorName
          ? ' <span class="status-chip rh-anchor-chip">기준</span>'
          : "";
        const weightText = Number.isFinite(w) ? formatFixed(w, 2) : IRMS.escapeHtml(String(it.value ?? ""));
        const ratioText = Number.isFinite(w) && total > 0 ? formatFixed((w / total) * 100, 1) : "-";
        const comp = Number(it.loss_comp_g);
        const compHtml = comp > 0
          ? `+${formatFixed(comp, 1)}`
          : '<span class="muted">-</span>';
        return `<tr>`
          + `<td class="num rh-idx">${idx + 1}</td>`
          + `<td>${name}${anchorChip}</td>`
          + `<td class="num">${weightText}</td>`
          + `<td class="num">${ratioText}</td>`
          + `<td class="num">${compHtml}</td>`
          + `</tr>`;
      }).join("");
      wrap.innerHTML =
        `<h4 class="rh-section-title">자재 구성 · ${items.length}종 · ${formatFixed(total, 2)} g</h4>`
        + `<div class="table-wrap rh-items-wrap"><table class="blend-table rh-items-table">`
        + `<thead><tr><th class="num">#</th><th>자재</th><th class="num">배합량(g)</th><th class="num">비율(%)</th><th class="num">보정(g)</th></tr></thead>`
        + `<tbody>${rowsHtml}</tbody>`
        + `<tfoot><tr><td></td><td>합계</td><td class="num">${formatFixed(total, 2)}</td><td class="num">100.0</td><td></td></tr></tfoot>`
        + `</table></div>`;
    }

    async function renderAttributePanel(detailRow, detail, recipeId) {
      const wrap = detailRow.querySelector(".history-attrs");
      if (!wrap) return;
      const canManage = !!ctx.canManage;
      const currentName = detail.anchor_material_name || "";
      const uniq = uniqueMaterialNames(detail);

      const settingRow = (labelHtml, controlHtml, extraClass) =>
        `<div class="rh-setting${extraClass ? ` ${extraClass}` : ""}">${labelHtml}`
        + `<div class="rh-setting-control">${controlHtml}</div></div>`;

      const tolCurrent = detail.tolerance_g != null ? Number(detail.tolerance_g) : null;
      const tolSet = tolCurrent != null && Number.isFinite(tolCurrent);
      const lossLabel = `<label class="filter-label" title="자재 마스터 기본값보다 이 값이 우선합니다.">투입 로스 보정</label>`;

      if (!canManage) {
        const lossItems = (detail.items || []).filter((it) => Number(it.loss_comp_g) > 0 && it.material_name);
        const lossText = lossItems.length
          ? lossItems.map((it) => `${IRMS.escapeHtml(it.material_name)} +${formatFixed(it.loss_comp_g, 1)} g`).join(" · ")
          : '<span class="muted">없음</span>';
        wrap.innerHTML =
          `<h4 class="rh-section-title">설정</h4>`
          + settingRow(`<label class="filter-label">기준 자재</label>`,
            currentName ? IRMS.escapeHtml(currentName) : '<span class="muted">없음</span>')
          + settingRow(`<label class="filter-label">허용 편차</label>`,
            tolSet ? `±${IRMS.escapeHtml(String(tolCurrent))} g` : '<span class="muted">±0.05 g 기본</span>')
          + settingRow(lossLabel, lossText);
        return;
      }

      // 기준 자재 — 바꾸면 바로 저장. data-saved 는 실패 시 되돌릴 값.
      const anchorOptions = '<option value="">없음</option>'
        + uniq.map((n) => `<option value="${IRMS.escapeHtml(n)}"${n === currentName ? " selected" : ""}>${IRMS.escapeHtml(n)}</option>`).join("");
      const anchorControl = `<select class="input attr-anchor-select" data-saved="${IRMS.escapeHtml(currentName)}">${anchorOptions}</select>`;

      // 허용 편차 — Enter 또는 칸을 벗어날 때(change) 값이 달라졌으면 저장.
      const tolValue = tolSet ? IRMS.escapeHtml(String(tolCurrent)) : "";
      const tolControl =
        `<input class="input attr-tolerance-input" type="number" step="0.01" min="0" placeholder="0.05" value="${tolValue}" data-saved="${tolValue}" />`
        + `<span class="rh-unit">g</span>`
        + `<span class="muted rh-hint">비우면 기본 0.05</span>`;

      wrap.innerHTML =
        `<h4 class="rh-section-title">설정</h4>`
        + settingRow(`<label class="filter-label">기준 자재</label>`, anchorControl)
        + settingRow(`<label class="filter-label">허용 편차</label>`, tolControl)
        + settingRow(lossLabel, renderLossCompBlock(detail, uniq), "rh-setting-losscomp");

      const anchorSel = wrap.querySelector(".attr-anchor-select");
      if (anchorSel) anchorSel.addEventListener("change", () => handleSaveAnchor(detailRow, recipeId));
      const tolInput = wrap.querySelector(".attr-tolerance-input");
      if (tolInput) {
        tolInput.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            handleSaveTolerance(detailRow, recipeId);
          }
        });
        // Enter 직후의 change 는 data-saved 비교로 건너뛴다.
        tolInput.addEventListener("change", () => handleSaveTolerance(detailRow, recipeId));
      }
      wireLossCompEditor(detailRow, recipeId, uniq);
    }

    // 보정 편집기(책임자 전용) — [자재 ▼][g][✕] 반복 줄 + [+ 보정 추가][저장] + 안내 한 줄.
    function renderLossCompBlock(detail, itemNames) {
      const existing = (detail.items || []).filter(
        (it) => Number(it.loss_comp_g) > 0 && it.material_name,
      );
      const rowsHtml = existing.map((it) => lossCompRowHtml(itemNames, it.material_name, it.loss_comp_g)).join("");
      return `<div class="rh-losscomp-rows attr-losscomp-rows">${rowsHtml}</div>`
        + `<div class="button-row rh-losscomp-actions">`
        + `<button class="btn btn-sm attr-losscomp-add" type="button">+ 보정 추가</button>`
        + `<button class="btn btn-sm accent attr-losscomp-save" type="button">저장</button>`
        + `</div>`
        + `<p class="rh-hint muted">보정 g만큼 계량 목표가 늘어납니다.</p>`
        + (itemNames.length ? "" : '<p class="login-error attr-losscomp-error">자재가 없습니다.</p>');
    }

    function lossCompRowHtml(itemNames, selectedName, value) {
      const opts = itemNames.length
        ? itemNames.map((n) => `<option value="${IRMS.escapeHtml(n)}"${n === selectedName ? " selected" : ""}>${IRMS.escapeHtml(n)}</option>`).join("")
        : "";
      return `<div class="rh-losscomp-row">`
        + `<select class="input attr-losscomp-mat">${opts}</select>`
        + `<input class="input attr-losscomp-g" type="number" step="0.1" min="0" max="100" placeholder="0.0" value="${value != null ? IRMS.escapeHtml(String(value)) : ""}" />`
        + `<span class="rh-unit">g</span>`
        + `<button class="btn btn-sm attr-losscomp-del" type="button" title="삭제" aria-label="삭제">✕</button>`
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
        if (del) del.closest(".rh-losscomp-row").remove();
      });
      if (saveBtn) saveBtn.addEventListener("click", () => handleSaveLossComp(detailRow, recipeId));
    }

    // ── 저장 핸들러(모두 detailRow 스코프) ──
    async function handleSaveAnchor(detailRow, recipeId) {
      const wrap = detailRow.querySelector(".history-attrs");
      const sel = wrap && wrap.querySelector(".attr-anchor-select");
      if (!sel) return;
      const previous = sel.dataset.saved || "";
      const chosenName = sel.value.trim();
      if (chosenName === previous) return;
      // 실패하면 선택을 저장된 값으로 되돌린다.
      const revert = () => { sel.value = previous; };
      let materialId = null;
      if (chosenName) {
        try {
          const detail = await IRMS.getRecipeDetail(recipeId);
          const match = (detail.items || []).find((it) => it.material_name === chosenName);
          if (!match || match.material_id == null) {
            IRMS.notify("선택한 자재의 식별자를 찾을 수 없습니다.", "error");
            revert();
            return;
          }
          materialId = Number(match.material_id);
        } catch (error) {
          IRMS.notify(`기준 자재 저장 실패: ${error.message}`, "error");
          revert();
          return;
        }
      }
      sel.disabled = true;
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
        sel.dataset.saved = chosenName;
        // 자재 구성 표의 '기준' 표시를 새 값으로 다시 그린다.
        const cached = detailRow._rhDetail;
        if (cached) {
          cached.anchor_material_name = chosenName || null;
          renderItemsTable(detailRow, cached);
        }
        IRMS.notify(chosenName ? `기준 자재를 '${chosenName}'(으)로 지정했습니다.` : "기준 자재를 해제했습니다.", "success");
      } catch (error) {
        revert();
        IRMS.notify(`기준 자재 저장 실패: ${error.message}`, "error");
      } finally {
        sel.disabled = false;
      }
    }

    async function handleSaveTolerance(detailRow, recipeId) {
      const wrap = detailRow.querySelector(".history-attrs");
      const input = wrap && wrap.querySelector(".attr-tolerance-input");
      if (!input) return;
      const raw = (input.value || "").trim();
      const previous = input.dataset.saved || "";
      // 같은 값이면 요청하지 않는다(Enter 저장 뒤 blur 의 change 가 한 번 더 부르는 것을 막음).
      if (raw === previous) {
        input.value = raw;
        return;
      }
      let toleranceG = null;
      if (raw !== "") {
        const v = Number(raw);
        if (!Number.isFinite(v) || !(v > 0)) {
          IRMS.notify("허용 편차는 0보다 큰 숫자여야 합니다.", "error");
          input.value = previous;
          return;
        }
        toleranceG = v;
      }
      input.dataset.saved = raw; // 응답 전에 같은 값으로 다시 불려도 건너뛴다
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
        const cached = detailRow._rhDetail;
        if (cached) cached.tolerance_g = toleranceG;
        IRMS.notify(toleranceG != null ? `허용 편차를 ±${toleranceG} g으로 지정했습니다.` : "허용 편차를 기본값 ±0.05 g으로 되돌렸습니다.", "success");
      } catch (error) {
        // 서버에 남은 값으로 되돌린다.
        input.dataset.saved = previous;
        input.value = previous;
        IRMS.notify(`허용 편차 저장 실패: ${error.message}`, "error");
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
        rowsEl.querySelectorAll(".rh-losscomp-row").forEach((row) => {
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
        // 성공 — 자재 구성 표(보정 열)와 설정 칸을 detail 재조회로 다시 그린다.
        const detail = await IRMS.getRecipeDetail(recipeId);
        renderItemsTable(detailRow, detail);
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
