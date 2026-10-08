/**
 * version-manage module — '버전 관리' 탭(#tab-versions, 책임자 전용) 렌더러(2026-10-08).
 *
 * 버전 비교(version-compare.js)는 읽기 전용 이력 화면으로 두고, 쓰기 동작은 여기로 모았다.
 *   이름     PUT    /api/recipes/{id}/version-name   (판을 사람이 구분하게 하는 자유 문구)
 *   되돌리기 POST   /api/recipes/import              (옛 판 내용으로 새 판 등록, revision_of = 현재판)
 *   삭제     DELETE /api/recipes/{id}                (기록 없는 이전 판만, 기록은 건드리지 않음)
 *   정리     기록 없는 이전 판을 차례로 삭제
 * 되돌리기는 옛 판을 되살리지 않는다. 이력은 앞으로만 쌓이고 현재판은 이전 버전이 된다.
 *
 * Factory: IRMS.management.createVersionManage(ctx)
 * Returns: { open }
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createVersionManage = function (ctx) {
    // 현재 열린 체인의 현재판 id — 동작 후 다시 그릴 때 쓴다.
    let currentTipId = null;

    function recCount(it) {
      return Number(it.linked_record_count || 0);
    }

    // 상태칩 — 버전 비교 타임라인과 같은 규칙.
    function statusChip(it) {
      if (it.status === "canceled") {
        return `<span class="status-chip ${IRMS.statusClass(it.status)}">${IRMS.statusLabel(it.status)}</span>`;
      }
      return it.is_current
        ? '<span class="status-chip status-completed">사용중</span>'
        : '<span class="status-chip">이전 버전</span>';
    }

    function jsonHeaders() {
      const headers = { "Content-Type": "application/json" };
      const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
      if (token) headers["x-csrftoken"] = token;
      return headers;
    }

    // import-validate.js importWithAnchor 와 같은 CSRF 헤더·오류 해석.
    async function sendJson(url, method, body) {
      const resp = await fetch(url, {
        method,
        credentials: "same-origin",
        headers: jsonHeaders(),
        body: JSON.stringify(body),
      });
      let payload = null;
      try { payload = await resp.json(); } catch (_e) { /* 본문 없음 */ }
      if (!resp.ok) {
        const d = payload && payload.detail;
        const msg = d && typeof d === "object" && d.message ? d.message
          : (d !== undefined && d !== null ? String(d) : `HTTP ${resp.status}`);
        throw new Error(msg);
      }
      return payload || {};
    }

    function saveVersionName(recipeId, name) {
      return sendJson(`/api/recipes/${recipeId}/version-name`, "PUT", { version_name: name || null });
    }

    // 등록 이력 표 새로고침 — 그 탭 모듈이 있으면만(조용히 무시).
    function refreshHistoryTable() {
      try {
        if (ctx.recipeHistory && ctx.recipeHistory.renderHistory) ctx.recipeHistory.renderHistory();
      } catch (_e) { /* 무시 */ }
    }

    // TSV 값 행의 첫 칸(반제품명)을 현재판 이름으로 바꾼다. 이름을 갈아탄 계보에서
    // 옛 판 이름으로 등록되면 현재 이름이 되돌아가 버리므로 현재 이름을 지킨다.
    function renameTsvProduct(tsv, productName) {
      const lines = String(tsv || "").split(/\r?\n/);
      if (lines.length < 2) throw new Error("레시피 내용을 읽지 못했습니다.");
      const cells = lines[1].split("\t");
      cells[0] = productName;
      lines[1] = cells.join("\t");
      return lines.join("\n");
    }

    function renderEmpty(message) {
      const body = document.getElementById("vm-body");
      if (body) body.innerHTML = `<tr><td colspan="8"><p class="empty-state">${IRMS.escapeHtml(message)}</p></td></tr>`;
      const pruneBtn = document.getElementById("vm-prune-btn");
      const pruneNote = document.getElementById("vm-prune-note");
      if (pruneBtn) pruneBtn.hidden = true;
      if (pruneNote) pruneNote.textContent = "";
    }

    async function open(recipeId) {
      if (!recipeId) return;
      const body = document.getElementById("vm-body");
      if (!body) return;
      let items = [];
      try {
        const res = await fetch(`/api/recipes/${recipeId}/history`, { credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const history = await res.json();
        items = history.items || [];
      } catch (error) {
        renderEmpty(`버전 이력 조회 실패: ${error.message}`);
        return;
      }
      if (!items.length) {
        renderEmpty("이 반제품의 버전 이력이 없습니다.");
        return;
      }
      const current = items.find((it) => it.is_current) || items[items.length - 1];
      currentTipId = current.id;
      const titleEl = document.getElementById("vm-product-title");
      if (titleEl) titleEl.textContent = current.product_name || "-";
      render(items, current);
    }

    function render(items, current) {
      const body = document.getElementById("vm-body");
      // 이름을 갈아탄 계보면 판마다 그 시절 반제품명을 버전 칸에 덧붙인다(버전 비교와 같은 규칙).
      const names = new Set(items.map((it) => it.product_name).filter(Boolean));
      const showName = names.size > 1;
      body.innerHTML = items.map((it) => {
        const nameTag = showName && it.product_name
          ? ` <span class="muted small">${IRMS.escapeHtml(it.product_name)}</span>`
          : "";
        const curChip = it.is_current ? ' <span class="status-chip status-completed">현재</span>' : "";
        const actions = it.is_current
          ? '<span class="muted">-</span>'
          : `<div class="button-row">`
            + `<button type="button" class="btn btn-sm vm-revert-btn" data-recipe-id="${it.id}">되돌리기</button>`
            + `<button type="button" class="btn btn-sm danger vm-delete-btn" data-recipe-id="${it.id}"`
            + (recCount(it) > 0 ? ' disabled title="배합 기록이 있어 삭제할 수 없습니다"' : "")
            + `>삭제</button>`
            + `</div>`;
        return `<tr data-recipe-id="${it.id}">`
          + `<td><b>${IRMS.escapeHtml(it.version_label)}</b>${curChip}${nameTag}</td>`
          + `<td><div class="vm-name-cell">`
          + `<input type="text" class="input vm-name-input" maxlength="40" data-recipe-id="${it.id}" value="${IRMS.escapeHtml(it.version_name || "")}" />`
          + `<button type="button" class="btn btn-sm vm-name-save-btn" data-recipe-id="${it.id}">저장</button>`
          + `</div></td>`
          + `<td>${IRMS.formatDateTime(it.created_at)}</td>`
          + `<td>${IRMS.escapeHtml(it.created_by || "-")}</td>`
          + `<td class="num">${Number(it.item_count || 0)}</td>`
          + `<td class="num">${recCount(it)}건</td>`
          + `<td>${statusChip(it)}</td>`
          + `<td>${actions}</td>`
          + `</tr>`;
      }).join("");

      const byId = new Map(items.map((it) => [it.id, it]));

      body.querySelectorAll(".vm-name-save-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const input = body.querySelector(`.vm-name-input[data-recipe-id="${btn.dataset.recipeId}"]`);
          if (input) handleSaveName(Number(btn.dataset.recipeId), input);
        });
      });
      body.querySelectorAll(".vm-name-input").forEach((input) => {
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            handleSaveName(Number(input.dataset.recipeId), input);
          }
        });
      });
      body.querySelectorAll(".vm-revert-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const target = byId.get(Number(btn.dataset.recipeId));
          if (target) revertToVersion(target, current, items.length);
        });
      });
      body.querySelectorAll(".vm-delete-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          if (btn.disabled) return;
          const target = byId.get(Number(btn.dataset.recipeId));
          if (target) deleteVersion(target, current);
        });
      });

      // 정리 = 기록 없는 이전 판(현재판 제외). 하나도 없으면 버튼을 숨긴다.
      const prunable = items.filter((it) => !it.is_current && recCount(it) === 0);
      const pruneBtn = document.getElementById("vm-prune-btn");
      const pruneNote = document.getElementById("vm-prune-note");
      if (pruneBtn) {
        pruneBtn.hidden = prunable.length === 0;
        // 다시 그릴 때마다 핸들러가 쌓이지 않게 onclick 으로 덮어쓴다.
        pruneBtn.onclick = () => pruneVersions(prunable, current);
      }
      if (pruneNote) pruneNote.textContent = prunable.length ? `기록 없는 이전 판 ${prunable.length}개` : "";
    }

    async function handleSaveName(recipeId, input) {
      const name = String(input.value || "").trim();
      try {
        await saveVersionName(recipeId, name);
        input.value = name;
        IRMS.notify("이름을 저장했습니다.", "success");
      } catch (error) {
        IRMS.notify(`이름 저장 실패: ${error.message}`, "error");
      }
    }

    // 되돌리기 = 옛 판 내용으로 새 판을 등록한다. 판 이름도 옛 판 것을 옮긴다.
    async function revertToVersion(target, current, chainLength) {
      const newLabel = `v${chainLength + 1}`;
      const ok = window.confirm(
        `${target.version_label} 내용으로 새 판을 등록합니다.\n`
        + `현재판 ${current.version_label} 대신 새 판이 사용됩니다.`,
      );
      if (!ok) return;
      let newId = null;
      try {
        const detail = await IRMS.getRecipeDetail(target.id);
        const rawText = renameTsvProduct(detail.tsv, current.product_name || detail.product_name);
        const result = await sendJson("/api/recipes/import", "POST", {
          raw_text: rawText,
          created_by: "레시피 관리",
          revision_of: current.id,
          force: true,
        });
        newId = (result.created_ids || [])[0] || null;
      } catch (error) {
        IRMS.notify(`되돌리기 실패: ${error.message}`, "error");
        return;
      }
      if (newId && target.version_name) {
        try {
          await saveVersionName(newId, target.version_name);
        } catch (_e) { /* 등록은 이미 성공 · 이름은 다시 붙이면 된다 */ }
      }
      IRMS.notify(`${target.version_label} 내용으로 ${newLabel} 판을 등록했습니다.`, "success");
      await open(newId || current.id);
      refreshHistoryTable();
    }

    async function deleteVersion(target, current) {
      if (!window.confirm(`${target.version_label} 판을 삭제합니다. 되돌릴 수 없습니다.`)) return;
      try {
        await IRMS.deleteRecipe(target.id, false);
        IRMS.notify(`${target.version_label} 판을 삭제했습니다.`, "success");
      } catch (error) {
        IRMS.notify(`삭제 실패: ${error.message}`, "error");
        return;
      }
      await open(current.id || currentTipId);
      refreshHistoryTable();
    }

    // 하나씩 차례로 지운다. 동시에 지우면 체인 재연결(자식→조부모)이 엇갈릴 수 있다.
    async function pruneVersions(targets, current) {
      const n = targets.length;
      if (!n) return;
      if (!window.confirm(`기록 없는 이전 판 ${n}개를 삭제합니다. 되돌릴 수 없습니다.`)) return;
      let done = 0;
      let firstError = null;
      for (const it of targets) {
        try {
          await IRMS.deleteRecipe(it.id, false);
          done += 1;
        } catch (error) {
          if (!firstError) firstError = `${it.version_label} ${error.message}`;
        }
      }
      if (firstError) {
        IRMS.notify(`이전 판 ${done}개를 삭제했습니다. 실패: ${firstError}`, "error");
      } else {
        IRMS.notify(`이전 판 ${done}개를 삭제했습니다.`, "success");
      }
      await open(current.id || currentTipId);
      refreshHistoryTable();
    }

    return { open };
  };
})();
