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

    function activeFilters() {
      return {
        uncoded: dom.codesUncoded && dom.codesUncoded.checked ? "1" : undefined,
        q: dom.codesSearch ? dom.codesSearch.value.trim() : "",
      };
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
            const actionHtml = code
              ? `<button class="btn btn-sm code-edit-btn" data-id="${m.id}">수정</button>
                 <button class="btn btn-sm danger code-clear-btn" data-id="${m.id}">해제</button>`
              : `<button class="btn btn-sm accent code-edit-btn" data-id="${m.id}">지정</button>`;
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

    function init() {
      if (dom.codesSearch) {
        dom.codesSearch.addEventListener(
          "input",
          IRMS.debounce(refresh, 300),
        );
      }
      if (dom.codesUncoded) {
        dom.codesUncoded.addEventListener("change", refresh);
      }
      if (dom.codesRefreshBtn) {
        dom.codesRefreshBtn.addEventListener("click", refresh);
      }
    }

    // ── 행 내 이벤트: 지정/수정(인라인 편집), 해제 ──
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
        if (!resp.ok) {
          const msg = await detailOf(resp);
          IRMS.notify(`코드 저장 실패: ${msg}`, "error");
          return;
        }
        const result = await resp.json();
        const saved = result.code || "";
        editingMaterialId = null;
        IRMS.notify(
          saved ? `품목코드를 '${saved}'(으)로 지정했습니다.` : "품목코드를 해제했습니다.",
          "success",
        );
        await refresh();
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
      } catch (err) {
        IRMS.notify(`코드 해제 실패: ${err.message}`, "error");
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

    return { init, refresh };
  };
})();
