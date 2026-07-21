/**
 * item-codes module — 품목코드 탭: 자재 코드 지정/해제 화면.
 *
 * item-code admin(item-code-admin spec §B2). 운영자(책임자)가 자재별 ERP 품목코드를
 * 확인·지정·해제하는 패널. 인라인 편집 + 마스터 제안(A1)으로 코드를 채운 뒤 A3 PUT.
 *
 * Factory: IRMS.management.createItemCodesPanel(ctx)
 * Returns: { init, refresh }
 *
 * ctx dependencies:
 *   dom:   codesSearch, codesUncoded, codesRefreshBtn, codesBody
 *   const: (없음)
 *   other: ctx.canManage
 *
 * 백엔드 연동:
 *   GET  /api/item-codes/materials        — 자재 목록(uncoded/q 필터)
 *   GET  /api/item-codes/master           — 마스터 제안(q, kind=material)
 *   PUT  /api/materials/{id}/code         — 자재 코드 지정/해제
 *
 * PUT fetch 는 recipe-history.js 분류 PUT 과 동일하게 credentials:"same-origin" +
 * x-csrftoken 헤더 직접 부착(IRMS.request 사용 금지 — 관리 화면에 미로드 대비).
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createItemCodesPanel = function (ctx) {
    const { dom } = ctx;

    // 마스터 제안 요청을 입력별로 디바운스(300ms). spec B2.
    const debouncedSuggest = IRMS.debounce(loadSuggestions, 300);

    // 활성 편집 행 추적 — 같은 행 중복 편집 방지.
    let editingMaterialId = null;

    // 맨 위 빠른 지정용 자재명→id 색인(datalist 원본). 필터와 무관하게 전체를 담는다.
    let matNameToId = {};

    function activeFilters() {
      return {
        uncoded: dom.codesUncoded && dom.codesUncoded.checked ? "1" : undefined,
        q: dom.codesSearch ? dom.codesSearch.value.trim() : "",
      };
    }

    // 정리 모드(code-edit-relocate §4): 기본 해제. 해제 상태에서는 행의 삭제 버튼을
    // 표시하지 않는다(지정/수정/해제만). 체크 시 기존 삭제 버튼(인라인 예/아니오 확인
    // 포함)이 표시되고 동작은 기존 그대로.
    function cleanupMode() {
      const cb = document.getElementById("codes-cleanup");
      return !!(cb && cb.checked);
    }

    async function refresh() {
      if (!dom.codesBody) return;
      const filters = activeFilters();
      try {
        const data = await IRMS._core.request("/item-codes/materials", { query: filters });
        const items = data.items || [];
        if (!items.length) {
          dom.codesBody.innerHTML =
            '<tr><td colspan="4"><div class="empty-state">조건에 맞는 자재가 없습니다.</div></td></tr>';
          return;
        }
        dom.codesBody.innerHTML = items
          .map((m) => {
            const code = m.code || "";
            const codeHtml = code
              ? `<span class="code-value">${IRMS.escapeHtml(code)}</span>`
              : '<span class="muted">-</span>';
            const codeActions = code
              ? `<button class="btn btn-sm code-edit-btn" data-id="${m.id}">수정</button>
                 <button class="btn btn-sm danger code-clear-btn" data-id="${m.id}">해제</button>`
              : `<button class="btn btn-sm accent code-edit-btn" data-id="${m.id}">지정</button>`;
            // 삭제 버튼은 정리 모드일 때만 표시(기본 해제).
            const deleteBtn = cleanupMode()
              ? `<button class="btn btn-sm danger material-delete-btn" data-id="${m.id}">삭제</button>`
              : "";
            const actionHtml = `${codeActions}${deleteBtn}`;
            return `
              <tr class="codes-row" data-id="${m.id}" data-name="${IRMS.escapeHtml(m.name)}">
                <td>${IRMS.escapeHtml(m.name)}</td>
                <td class="code-cell">${codeHtml}</td>
                <td>${m.category ? IRMS.escapeHtml(m.category) : '<span class="muted">-</span>'}</td>
                <td class="action-cell">${actionHtml}</td>
              </tr>`;
          })
          .join("");
        bindRowEvents();
      } catch (err) {
        IRMS.notify(`자재 목록 조회 실패: ${err.message}`, "error");
      }
    }

    // 맨 위 빠른 지정 — 전체 자재를 한 번 불러 datalist(자재명)와 이름→id 색인을 채운다.
    // 필터("코드 없음만")와 무관하게 전체를 담아, 이미 코드가 있는 자재도 위에서 수정 가능.
    async function loadMaterialIndex() {
      const dl = document.getElementById("codes-mat-datalist");
      try {
        const data = await IRMS._core.request("/item-codes/materials", { query: {} });
        const items = data.items || [];
        matNameToId = {};
        items.forEach((m) => {
          matNameToId[String(m.name).trim().toLowerCase()] = m.id;
        });
        if (dl) {
          dl.innerHTML = items
            .map((m) => `<option value="${IRMS.escapeHtml(m.name)}"></option>`)
            .join("");
        }
      } catch (_e) {
        /* 색인 실패는 조용히 — 아래 표는 정상 동작 */
      }
    }

    async function quickAssign() {
      const nameEl = document.getElementById("codes-quick-name");
      const codeEl = document.getElementById("codes-quick-code");
      if (!nameEl || !codeEl) return;
      const name = String(nameEl.value || "").trim();
      const code = String(codeEl.value || "").trim();
      if (!name) {
        IRMS.notify("자재명을 입력하세요.", "error");
        nameEl.focus();
        return;
      }
      const id = matNameToId[name.toLowerCase()];

      // 기존 자재(name 이 색인에 있음) → 코드 지정 경로. 동작은 종전과 동일.
      // 코드는 이 경로에서만 필수(새 자재 등록 경로는 코드 없이도 가능).
      if (id) {
        if (!code) {
          IRMS.notify("코드를 입력하세요.", "error");
          codeEl.focus();
          return;
        }
        try {
          const resp = await fetch(`/api/materials/${id}/code`, {
            method: "PUT",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", ...csrfHeader() },
            body: JSON.stringify({ code }),
          });
          let result;
          if (!resp.ok) {
            const detail = await detailOf(resp);
            if (resp.status === 409) {
              // 코드 충돌이면 confirmMoveOn409 가 force:true 재시도로 코드를 이동한다.
              const moved = await confirmMoveOn409(detail, `/api/materials/${id}/code`, "PUT", { code });
              if (moved === null) return; // 취소 또는 자재명 중복 — 추가 notify 없음.
              result = moved;
            } else {
              IRMS.notify(`코드 저장 실패: ${detail}`, "error");
              return;
            }
          } else {
            result = await resp.json();
          }
          const moveNote = result.moved_from ? ` (기존 '${result.moved_from}'에서 해제)` : "";
          IRMS.notify(`품목코드를 '${result.code || code}'(으)로 지정했습니다.${moveNote}`, "success");
          nameEl.value = "";
          codeEl.value = "";
          nameEl.focus();
          await refresh();
          loadMaterialIndex();
          // BOM 편집기 자재 색인 갱신 — fire-and-forget(실패해도 패널 동작엔 영향 없음).
          if (ctx.refreshMaterials) ctx.refreshMaterials().catch(() => {});
        } catch (err) {
          IRMS.notify(`코드 저장 실패: ${err.message}`, "error");
        }
        return;
      }

      // 미등록 자재명 → 새 자재로 등록(코드는 있어도/없어도 됨).
      // 운영자가 Excel 재임포트 없이 단건 ERP 자재를 화면에서 바로 등록.
      let confirmMsg = `'${name}' 은(는) 등록되지 않은 자재입니다. 새 자재로 등록할까요?`;
      if (code) {
        confirmMsg += ` (품목코드 ${code} 지정)`;
      }
      if (!window.confirm(confirmMsg)) {
        return; // 취소 → 입력 그대로 유지
      }
      try {
        const resp = await fetch(`/api/materials`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...csrfHeader() },
          body: JSON.stringify({ name, code: code || null }),
        });
        let result;
        if (!resp.ok) {
          const detail = await detailOf(resp);
          if (resp.status === 409) {
            // 409 는 자재명 중복일 수도 있고 코드 충돌일 수도 있음 — 코드 충돌만 force:true 이동 제안.
            const moved = await confirmMoveOn409(detail, `/api/materials`, "POST", { name, code: code || null });
            if (moved === null) {
              // 코드 충돌(취소) 이면 종료; 자재명 중복이면 일반 에러 notify.
              if (!detail.includes("사용 중인 코드")) {
                IRMS.notify(`자재 등록 실패: ${detail}`, "error");
              }
              return;
            }
            result = moved;
          } else {
            IRMS.notify(`자재 등록 실패: ${detail}`, "error");
            return;
          }
        } else {
          result = await resp.json();
        }
        const moveNote = result.moved_from ? ` (기존 '${result.moved_from}'에서 해제)` : "";
        const successMsg = code
          ? `자재 '${result.name || name}' 을(를) 등록하고 품목코드 '${result.code || code}' 을(를) 지정했습니다.${moveNote}`
          : `자재 '${result.name || name}' 을(를) 등록했습니다.`;
        IRMS.notify(successMsg, "success");
        nameEl.value = "";
        codeEl.value = "";
        nameEl.focus();
        await refresh();
        loadMaterialIndex();
        // BOM 편집기 자재 색인 갱신 — fire-and-forget(실패해도 패널 동작엔 영향 없음).
        if (ctx.refreshMaterials) ctx.refreshMaterials().catch(() => {});
      } catch (err) {
        IRMS.notify(`자재 등록 실패: ${err.message}`, "error");
      }
    }

    function init() {
      loadMaterialIndex();
      const quickBtn = document.getElementById("codes-quick-assign-btn");
      if (quickBtn) {
        quickBtn.addEventListener("click", quickAssign);
      }
      const quickCode = document.getElementById("codes-quick-code");
      if (quickCode) {
        quickCode.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            quickAssign();
          }
        });
      }
      if (dom.codesSearch) {
        dom.codesSearch.addEventListener(
          "input",
          IRMS.debounce(refresh, 300),
        );
      }
      if (dom.codesUncoded) {
        dom.codesUncoded.addEventListener("change", refresh);
      }
      const cleanupCb = document.getElementById("codes-cleanup");
      if (cleanupCb) {
        cleanupCb.addEventListener("change", refresh);
      }
      if (dom.codesRefreshBtn) {
        dom.codesRefreshBtn.addEventListener("click", refresh);
      }
    }

    // ── 행 내 이벤트: 지정/수정(인라인 편집), 해제, 삭제 ──
    function bindRowEvents() {
      dom.codesBody.querySelectorAll(".code-edit-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const row = btn.closest(".codes-row");
          startInlineEdit(row);
        });
      });
      dom.codesBody.querySelectorAll(".code-clear-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const row = btn.closest(".codes-row");
          clearCode(row);
        });
      });
      dom.codesBody.querySelectorAll(".material-delete-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const row = btn.closest(".codes-row");
          startInlineDeleteConfirm(row);
        });
      });
    }

    // 인라인 편집 시작 — 행 안에 input + 제안 목록 + 저장/취소 버튼 표시.
    function startInlineEdit(row) {
      const id = Number(row.dataset.id);
      if (editingMaterialId === id) return;
      // 이미 열린 편집은 닫기(원복)
      cancelAllInlineEdits();
      editingMaterialId = id;

      const cell = row.querySelector(".code-cell");
      const current = cell.querySelector(".code-value");
      const currentValue = current ? current.textContent.trim() : "";

      cell.innerHTML = `
        <div class="code-edit-wrap">
          <input class="input compact code-inline-input" value="${IRMS.escapeHtml(currentValue)}" placeholder="코드 입력 (예: AS0001)" />
          <button class="btn btn-sm success code-save-btn" type="button">저장</button>
          <button class="btn btn-sm code-cancel-btn" type="button">취소</button>
          <ul class="code-suggest-list" hidden></ul>
        </div>`;

      const input = cell.querySelector(".code-inline-input");
      const suggestList = cell.querySelector(".code-suggest-list");

      input.focus();
      input.select();
      input.addEventListener("input", () => {
        const q = input.value.trim();
        if (q.length < 1) {
          suggestList.hidden = true;
          return;
        }
        debouncedSuggest(q, suggestList, input);
      });
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          saveCode(row, input.value);
        } else if (ev.key === "Escape") {
          ev.preventDefault();
          cancelAllInlineEdits();
          refresh();
        }
      });

      cell.querySelector(".code-save-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        saveCode(row, input.value);
      });
      cell.querySelector(".code-cancel-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        cancelAllInlineEdits();
        refresh();
      });
    }

    // 마스터 제안 로드 — A1 (kind=material). 결과를 (코드 — 이름) 목록으로 표시.
    async function loadSuggestions(q, suggestList, input) {
      try {
        const data = await IRMS._core.request("/item-codes/master", {
          query: { q, kind: "material" },
        });
        const items = data.items || [];
        if (!items.length) {
          suggestList.hidden = true;
          suggestList.innerHTML = "";
          return;
        }
        suggestList.innerHTML = items
          .map(
            (it) =>
              `<li class="code-suggest-item" data-code="${IRMS.escapeHtml(it.code)}">${IRMS.escapeHtml(it.code)} — ${IRMS.escapeHtml(it.name)}</li>`,
          )
          .join("");
        suggestList.hidden = false;
        suggestList.querySelectorAll(".code-suggest-item").forEach((li) => {
          li.addEventListener("mousedown", (ev) => {
            ev.preventDefault(); // input blur 보존
            input.value = li.dataset.code;
            suggestList.hidden = true;
            input.focus();
          });
        });
      } catch (_err) {
        suggestList.hidden = true;
      }
    }

    // 편집 취소 — 열려 있는 인라인 편집을 모두 닫는다.
    function cancelAllInlineEdits() {
      editingMaterialId = null;
      dom.codesBody.querySelectorAll(".code-edit-wrap").forEach((w) => {
        const cell = w.closest(".code-cell");
        const row = w.closest(".codes-row");
        if (row && cell) {
          // 원래 코드 표시로 원복 — 전체 새로고침보다 행 단위로 복구
          cell.innerHTML = '<span class="muted">-</span>';
        }
      });
    }

    // 코드 저장 — A3 PUT. 성공 시 행 갱신, 409/400 은 detail 을 error notify.
    async function saveCode(row, rawValue) {
      const id = Number(row.dataset.id);
      const value = String(rawValue || "").trim();
      const code = value === "" ? null : value;
      try {
        const resp = await fetch(`/api/materials/${id}/code`, {
          method: "PUT",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            ...csrfHeader(),
          },
          body: JSON.stringify({ code }),
        });
        let result;
        if (!resp.ok) {
          const detail = await detailOf(resp);
          if (resp.status === 409) {
            // 코드 충돌이면 confirmMoveOn409 가 force:true 재시도로 코드를 이동한다.
            const moved = await confirmMoveOn409(detail, `/api/materials/${id}/code`, "PUT", { code });
            if (moved === null) return; // 취소 또는 자재명 중복 — 추가 notify 없음.
            result = moved;
          } else {
            IRMS.notify(`코드 저장 실패: ${detail}`, "error");
            return;
          }
        } else {
          result = await resp.json();
        }
        const saved = result.code || "";
        editingMaterialId = null;
        const moveNote = result.moved_from ? ` (기존 '${result.moved_from}'에서 해제)` : "";
        IRMS.notify(
          saved ? `품목코드를 '${saved}'(으)로 지정했습니다.${moveNote}` : "품목코드를 해제했습니다.",
          "success",
        );
        await refresh();
        // BOM 편집기 자재 색인 갱신 — fire-and-forget(실패해도 패널 동작엔 영향 없음).
        if (ctx.refreshMaterials) ctx.refreshMaterials().catch(() => {});
      } catch (err) {
        IRMS.notify(`코드 저장 실패: ${err.message}`, "error");
      }
    }

    // 코드 해제 — code=null PUT.
    async function clearCode(row) {
      const id = Number(row.dataset.id);
      if (!window.confirm("이 자재의 품목코드를 해제하시겠습니까?")) return;
      try {
        const resp = await fetch(`/api/materials/${id}/code`, {
          method: "PUT",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            ...csrfHeader(),
          },
          body: JSON.stringify({ code: null }),
        });
        if (!resp.ok) {
          const msg = await detailOf(resp);
          IRMS.notify(`코드 해제 실패: ${msg}`, "error");
          return;
        }
        IRMS.notify("품목코드를 해제했습니다.", "success");
        await refresh();
        // BOM 편집기 자재 색인 갱신 — fire-and-forget(실패해도 패널 동작엔 영향 없음).
        if (ctx.refreshMaterials) ctx.refreshMaterials().catch(() => {});
      } catch (err) {
        IRMS.notify(`코드 해제 실패: ${err.message}`, "error");
      }
    }

    // ── 자재 삭제: 인라인 확인 ──
    // window.confirm 금지(spec 제약). 행 안의 action-cell 을
    // "정말 삭제 [예/아니오]" 두 버튼으로 교체 — 기존 인라인 편집 패턴 재사용.
    // 편집 중인 행과 충돌하지 않게 stopPropagation 은 호출단에서 이미 처리.
    function startInlineDeleteConfirm(row) {
      const id = Number(row.dataset.id);
      const name = row.dataset.name || "";
      const cell = row.querySelector(".action-cell");
      if (!cell) return;
      // 이미 확인 중이면 토글(재클릭 시 취소).
      if (cell.querySelector(".material-delete-confirm-wrap")) {
        refresh();
        return;
      }
      cell.innerHTML = `
        <div class="material-delete-confirm-wrap">
          <span class="muted">정말 삭제 ${IRMS.escapeHtml(name)}</span>
          <button class="btn btn-sm danger material-delete-yes-btn" type="button" data-id="${id}">예</button>
          <button class="btn btn-sm material-delete-no-btn" type="button">아니오</button>
        </div>`;

      cell.querySelector(".material-delete-yes-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        deleteMaterial(row);
      });
      cell.querySelector(".material-delete-no-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        refresh(); // 원래 행으로 복귀
      });
    }

    // DELETE fetch — A5. 성공 시 success notify + 목록 새로고침.
    // 409 는 detail(어떤 레시피가 쓰는지)을 그대로 error notify 로 노출.
    async function deleteMaterial(row) {
      const id = Number(row.dataset.id);
      try {
        const resp = await fetch(`/api/materials/${id}`, {
          method: "DELETE",
          credentials: "same-origin",
          headers: { ...csrfHeader() },
        });
        if (!resp.ok) {
          const msg = await detailOf(resp);
          IRMS.notify(`자재 삭제 실패: ${msg}`, "error");
          // 409 면 확인 UI 를 유지해 사용자가 메시지를 볼 수 있게 원래 행으로 복귀.
          if (resp.status === 409) refresh();
          return;
        }
        const result = await resp.json();
        const deletedName = result.deleted || "";
        IRMS.notify(`자재 '${deletedName}' 을 삭제했습니다.`, "success");
        await refresh();
        // BOM 편집기 자재 색인 갱신 — fire-and-forget(실패해도 패널 동작엔 영향 없음).
        if (ctx.refreshMaterials) ctx.refreshMaterials().catch(() => {});
      } catch (err) {
        IRMS.notify(`자재 삭제 실패: ${err.message}`, "error");
      }
    }

    // ── 공통: CSRF 헤더, 에러 detail 추출 ──
    function csrfHeader() {
      const token =
        IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
      return token ? { "x-csrftoken": token } : {};
    }

    async function detailOf(resp) {
      try {
        const p = await resp.json();
        if (p && p.detail) {
          return typeof p.detail === "object" ? p.detail.message || `Request failed (${resp.status})` : String(p.detail);
        }
      } catch (_e) { /* noop */ }
      return `Request failed (${resp.status})`;
    }

    // 409 코드 충돌 시 "코드 이동" 확인 → force:true 재시도.
    // detail 은 호출부에서 이미 뽑은 값(본문은 한 번만 읽도록). 코드 충돌(“사용 중인 코드”)
    // 일 때만 확인창을 띄운다 — POST /materials 의 409 는 자재명 중복일 수도 있어 detail 로 걸른다.
    // 반환: { result } (이동 성공 응답 본문) | null (사용자 취소 또는 코드 충돌 아님).
    // 사용자가 취소하거나 자재명 중복 등이면 null — 호출부는 추가 notify 없이 그냥 return.
    async function confirmMoveOn409(detail, url, method, baseBody) {
      if (!detail.includes("사용 중인 코드")) return null; // 코드 충돌 아님.
      const ok = window.confirm(
        `${detail}\n이 자재로 코드를 옮길까요? (기존 자재에서는 해제됩니다)`,
      );
      if (!ok) return null; // 취소 — 추가 notify 없음.
      const retryResp = await fetch(url, {
        method,
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...csrfHeader() },
        body: JSON.stringify({ ...baseBody, force: true }),
      });
      if (!retryResp.ok) {
        throw new Error(await detailOf(retryResp));
      }
      return retryResp.json();
    }

    return { init, refresh };
  };
})();
