/**
 * version-manage module — '버전 관리' 탭(#tab-versions, 책임자 전용) 렌더러(2026-10-08).
 *
 * 버전 비교(version-compare.js)는 읽기 전용 이력 화면으로 두고, 쓰기 동작은 여기로 모았다.
 *   이름        PUT    /api/recipes/{id}/version-name   (판을 사람이 구분하게 하는 자유 문구)
 *   현재판 지정 PUT    /api/recipes/{id}/current        (복사 없이 체인의 현재판을 이 판으로 바꿈)
 *   삭제        DELETE /api/recipes/{id}                (기록 없는 미사용 판은 바로 확인 후 삭제)
 *               DELETE /api/recipes/{id}?move_records_to=<판 id>
 *                 기록이 있는 미사용 판은 행 안의 옮기기 폼에서 같은 체인의 다른 판을 골라
 *                 기록을 그 판으로 옮긴 뒤 삭제한다(옮길 판이 없으면 버튼 비활성).
 *   정리        기록 없는 미사용 판을 차례로 삭제
 *   사용 기간   history 의 first_used_on ~ last_used_on(취소 아닌 기록의 작업일 최소·최대)
 * 현재판 지정은 새 판을 만들지 않는다(2026-10-08 현장 요청: 저점도용 v2 ↔ 고점도용 v4 전환).
 * 지정은 다음 수정 등록 때 풀리고 새 판이 현재판이 된다(서버 import 라우트).
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

    // 사용 기간 칸 — 첫날 ~ 마지막 날(같으면 하루), 기록 없으면 '-'.
    function usagePeriod(it) {
      const first = it.first_used_on;
      const last = it.last_used_on;
      if (!first && !last) return "-";
      if (!first || !last || first === last) return IRMS.escapeHtml(first || last);
      return `${IRMS.escapeHtml(first)} ~ ${IRMS.escapeHtml(last)}`;
    }

    // 기록을 옮겨 받을 수 있는 판 — 같은 체인의 다른 판 중 취소·초안이 아닌 것.
    function moveCandidates(items, target) {
      return items.filter((it) => it.id !== target.id
        && it.status !== "canceled" && it.status !== "draft");
    }

    function versionOptionText(it) {
      return it.version_name ? `${it.version_label} · ${it.version_name}` : it.version_label;
    }

    // 상태칩 — 버전 비교 타임라인과 같은 규칙.
    // 현재판이 아닌 판은 더 새 판일 수도 있어(현재판 지정) '이전'이 아니라 '미사용'.
    function statusChip(it) {
      if (it.status === "canceled") {
        return `<span class="status-chip ${IRMS.statusClass(it.status)}">${IRMS.statusLabel(it.status)}</span>`;
      }
      return it.is_current
        ? '<span class="status-chip status-completed">사용중</span>'
        : '<span class="status-chip">미사용</span>';
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

    function renderEmpty(message) {
      const body = document.getElementById("vm-body");
      if (body) body.innerHTML = `<tr><td colspan="9"><p class="empty-state">${IRMS.escapeHtml(message)}</p></td></tr>`;
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
      // '지정' 표기는 최신이 아닌 판을 현재판으로 쓰는 중일 때만. 최신=현재면 덧붙이지 않는다.
      const activeIds = items
        .filter((it) => it.status !== "canceled" && it.status !== "draft")
        .map((it) => Number(it.id));
      const newestActiveId = activeIds.length ? Math.max(...activeIds) : null;
      const pinnedNote = (it) => (it.is_current && it.is_pinned && Number(it.id) !== newestActiveId
        ? ' <span class="muted small">· 지정</span>'
        : "");
      body.innerHTML = items.map((it) => {
        const nameTag = showName && it.product_name
          ? ` <span class="muted small">${IRMS.escapeHtml(it.product_name)}</span>`
          : "";
        const curChip = it.is_current ? ' <span class="status-chip status-completed">현재</span>' : "";
        const canPin = !it.is_current && it.status !== "canceled" && it.status !== "draft";
        // 기록이 있는 판은 옮길 판이 있어야 삭제할 수 있다(옮기기 폼으로 이어짐).
        const noMoveTarget = recCount(it) > 0 && moveCandidates(items, it).length === 0;
        const actions = it.is_current
          ? '<span class="muted">-</span>'
          : `<div class="button-row">`
            + (canPin
              ? `<button type="button" class="btn btn-sm accent vm-pin-btn" data-recipe-id="${it.id}">현재판 지정</button>`
              : "")
            + `<button type="button" class="btn btn-sm danger vm-delete-btn" data-recipe-id="${it.id}"`
            + (noMoveTarget ? ' disabled title="옮길 판이 없어 삭제할 수 없습니다"' : "")
            + `>삭제</button>`
            + `</div>`;
        const savedName = IRMS.escapeHtml(it.version_name || "");
        return `<tr data-recipe-id="${it.id}">`
          + `<td><b>${IRMS.escapeHtml(it.version_label)}</b>${curChip}${nameTag}</td>`
          + `<td><div class="vm-name-cell">`
          + `<input type="text" class="input vm-name-input" maxlength="40" data-recipe-id="${it.id}" data-saved="${savedName}" value="${savedName}" />`
          + `<button type="button" class="btn btn-sm vm-name-save-btn" data-recipe-id="${it.id}">저장</button>`
          + `</div></td>`
          + `<td>${IRMS.formatDateTime(it.created_at)}</td>`
          + `<td>${IRMS.escapeHtml(it.created_by || "-")}</td>`
          + `<td class="num">${Number(it.item_count || 0)}</td>`
          + `<td class="num">${recCount(it)}건</td>`
          + `<td>${usagePeriod(it)}</td>`
          + `<td>${statusChip(it)}${pinnedNote(it)}</td>`
          + `<td class="vm-actions-cell">${actions}</td>`
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
        // 값이 바뀐 채로 칸을 벗어나면(blur) 저장. Enter 직후의 change 는 data-saved 비교로 건너뛴다.
        input.addEventListener("change", () => {
          handleSaveName(Number(input.dataset.recipeId), input);
        });
      });
      body.querySelectorAll(".vm-pin-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          const target = byId.get(Number(btn.dataset.recipeId));
          if (target) pinVersion(target);
        });
      });
      body.querySelectorAll(".vm-delete-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
          if (btn.disabled) return;
          const target = byId.get(Number(btn.dataset.recipeId));
          if (!target) return;
          if (recCount(target) > 0) {
            showMoveForm(btn, target, items, current);
          } else {
            deleteVersion(target, current);
          }
        });
      });

      // 정리 = 기록 없는 미사용 판(현재판 제외). 하나도 없으면 버튼을 숨긴다.
      const prunable = items.filter((it) => !it.is_current && recCount(it) === 0);
      const pruneBtn = document.getElementById("vm-prune-btn");
      const pruneNote = document.getElementById("vm-prune-note");
      if (pruneBtn) {
        pruneBtn.hidden = prunable.length === 0;
        // 다시 그릴 때마다 핸들러가 쌓이지 않게 onclick 으로 덮어쓴다.
        pruneBtn.onclick = () => pruneVersions(prunable, current);
      }
      if (pruneNote) pruneNote.textContent = prunable.length ? `기록 없는 미사용 판 ${prunable.length}개` : "";
    }

    // 같은 값이면 요청하지 않는다(Enter 저장 뒤 blur 의 change 가 한 번 더 부르는 것을 막음).
    async function handleSaveName(recipeId, input) {
      const name = String(input.value || "").trim();
      if (name === (input.dataset.saved || "")) {
        input.value = name;
        return;
      }
      input.dataset.saved = name; // 응답 전에 같은 값으로 다시 불려도 건너뛴다
      try {
        await saveVersionName(recipeId, name);
        IRMS.notify("이름을 저장했습니다.", "success");
      } catch (error) {
        IRMS.notify(`이름 저장 실패: ${error.message}`, "error");
      }
      // 서버에 남은 값으로 다시 그린다(실패했으면 원래 이름으로 돌아온다).
      await open(currentTipId || recipeId);
    }

    // 현재판 지정 = 복사 없이 체인의 현재판을 이 판으로 바꾼다.
    async function pinVersion(target) {
      const label = target.version_label;
      if (!window.confirm(`${label} 판을 현재판으로 지정합니다.\n배합 화면은 이 판을 씁니다.`)) return;
      try {
        await sendJson(`/api/recipes/${target.id}/current`, "PUT", {});
      } catch (error) {
        IRMS.notify(`현재판 지정 실패: ${error.message}`, "error");
        return;
      }
      IRMS.notify(`${label} 판을 현재판으로 지정했습니다.`, "success");
      await open(target.id);
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

    // 기록이 있는 판의 삭제 = 그 행 동작 칸을 옮기기 폼으로 바꾼다. 취소하면 표를 다시 그린다.
    function showMoveForm(btn, target, items, current) {
      const cell = btn.closest(".vm-actions-cell");
      if (!cell) return;
      const candidates = moveCandidates(items, target);
      if (!candidates.length) return;
      const n = recCount(target);
      const defaultId = (candidates.find((it) => it.is_current) || candidates[candidates.length - 1]).id;
      const options = candidates.map((it) => `<option value="${it.id}"`
        + (it.id === defaultId ? " selected" : "")
        + `>${IRMS.escapeHtml(versionOptionText(it))}</option>`).join("");
      cell.innerHTML = `<div class="vm-move-form">`
        + `<label class="filter-label">기록 ${n}건을 옮길 판</label>`
        + `<select class="input vm-move-target">${options}</select>`
        + `<button type="button" class="btn btn-sm danger vm-move-delete-btn">옮기고 삭제</button>`
        + `<button type="button" class="btn btn-sm vm-move-cancel-btn">취소</button>`
        + `</div>`;
      const select = cell.querySelector(".vm-move-target");
      cell.querySelector(".vm-move-cancel-btn").addEventListener("click", () => render(items, current));
      cell.querySelector(".vm-move-delete-btn").addEventListener("click", () => {
        const dest = candidates.find((it) => String(it.id) === String(select.value));
        if (dest) moveAndDeleteVersion(target, dest, current);
      });
    }

    async function moveAndDeleteVersion(target, dest, current) {
      const n = recCount(target);
      const from = target.version_label;
      const to = dest.version_label;
      if (!window.confirm(`${from} 판의 기록 ${n}건을 ${to} 판으로 옮기고 ${from} 판을 삭제합니다.\n되돌릴 수 없습니다.`)) return;
      try {
        await IRMS.deleteRecipe(target.id, false, { moveRecordsTo: dest.id });
        IRMS.notify(`${from} 판을 삭제했습니다. 기록 ${n}건은 ${to} 판으로 옮겼습니다.`, "success");
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
      if (!window.confirm(`기록 없는 미사용 판 ${n}개를 삭제합니다. 되돌릴 수 없습니다.`)) return;
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
        IRMS.notify(`미사용 판 ${done}개를 삭제했습니다. 실패: ${firstError}`, "error");
      } else {
        IRMS.notify(`미사용 판 ${done}개를 삭제했습니다.`, "success");
      }
      await open(current.id || currentTipId);
      refreshHistoryTable();
    }

    return { open };
  };
})();
