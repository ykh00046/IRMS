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

    // 정리 모드(code-edit-relocate §4)의 잔재. 자재 관리 화면(/materials)이 생기면서
    // 삭제·이름수정은 이 화면의 정식 기능이 되어 체크박스 뒤에 숨길 이유가 없어졌다.
    // 체크박스가 없는 화면(=자재 관리)에서는 항상 노출한다. 남아 있는 화면이 있으면
    // 종전대로 체크했을 때만 보인다.
    function cleanupMode() {
      const cb = document.getElementById("codes-cleanup");
      return cb ? !!cb.checked : true;
    }

    async function refresh() {
      if (!dom.codesBody) return;
      const filters = activeFilters();
      try {
        const data = await IRMS._core.request("/item-codes/materials", { query: filters });
        const items = data.items || [];
        if (!items.length) {
          dom.codesBody.innerHTML =
            '<tr><td colspan="6"><div class="empty-state">조건에 맞는 자재가 없습니다.</div></td></tr>';
          return;
        }
        dom.codesBody.innerHTML = items
          .map((m) => {
            const code = m.code || "";
            // 코드 계열 배지 — 원자재/반제품/관리용. 코드 없는 자재는 배지 없음.
            // 서버가 code_kind(raw/product/managed/None) 를 내려주므로 화면은 표기만 한다.
            const KIND_LABEL = { raw: "원자재", product: "반제품", managed: "관리용" };
            const kindLabel = code && m.code_kind ? KIND_LABEL[m.code_kind] : "";
            const kindBadge = kindLabel
              ? ` <span class="code-kind-badge">${IRMS.escapeHtml(kindLabel)}</span>`
              : "";
            const codeHtml = code
              ? `<span class="code-value">${IRMS.escapeHtml(code)}</span>${kindBadge}`
              : '<span class="muted">-</span>';
            const codeActions = code
              ? `<button class="btn btn-sm code-edit-btn" data-id="${m.id}">수정</button>
                 <button class="btn btn-sm danger code-clear-btn" data-id="${m.id}">해제</button>`
              : `<button class="btn btn-sm accent code-edit-btn" data-id="${m.id}">지정</button>`;
            // 삭제 버튼은 정리 모드일 때만 표시(기본 해제).
            const deleteBtn = cleanupMode()
              ? `<button class="btn btn-sm danger material-delete-btn" data-id="${m.id}">삭제</button>`
              : "";
            // 자재명 수정 — 옛 이름은 서버가 동의어로 남기므로 과거 기록의 품목코드가 끊기지 않는다.
            const renameBtn = cleanupMode()
              ? `<button class="btn btn-sm material-rename-btn" data-id="${m.id}">이름</button>`
              : "";
            const actionHtml = `${codeActions}${renameBtn}${deleteBtn}`;
            // 투입 로스 보정(자재 마스터 기본값, 3라운드) — 인라인 입력+저장. 값이 있으면 표시.
            const compVal = Number(m.loss_comp_g) > 0 ? String(m.loss_comp_g) : "";
            const lossCompHtml = `<input class="input mat-losscomp-input" data-id="${m.id}" type="number" step="0.1" min="0" max="100" value="${IRMS.escapeHtml(compVal)}" placeholder="0" title="투입 로스 보정(g) — 이 자재가 들어가는 모든 레시피에 자동 적용" />`
              + `<button class="btn btn-sm mat-losscomp-save" data-id="${m.id}" type="button">저장</button>`;
            // 동의어(A6) — 개수를 배지처럼 버튼 라벨에 담아, 눌러야 목록이 열리게 한다.
            // 표를 넓히지 않으려고 목록은 아래 확장 행에서 보여준다.
            const aliasCount = Number(m.alias_count) || 0;
            const aliasLabel = aliasCount ? `동의어 ${aliasCount}` : "동의어";
            const aliasHtml =
              `<button class="btn btn-sm alias-open-btn" data-id="${m.id}" type="button"`
              + ` title="같은 원재료가 기록에 다른 이름으로 남았을 때 이 자재에 잇습니다">`
              + `${aliasLabel}</button>`;
            return `
              <tr class="codes-row" data-id="${m.id}" data-name="${IRMS.escapeHtml(m.name)}">
                <td>${IRMS.escapeHtml(m.name)}</td>
                <td class="code-cell">${codeHtml}</td>
                <td>${m.category ? IRMS.escapeHtml(m.category) : '<span class="muted">-</span>'}</td>
                <td class="alias-cell">${aliasHtml}</td>
                <td class="losscomp-cell">${lossCompHtml}</td>
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
      // 결정 직전에 자재 색인을 최신화한다 — 다른 탭에서 2차 레시피를 등록해 1차 반제품이
      // 자재로 자동 등록됐어도(페이지 로드 이후 생성) 이를 '기존 자재'로 인식해 수정(PUT)
      // 경로를 타게 한다. 색인이 낡으면 신규 등록(POST)으로 빠져 "이미 등록된 자재명" 409 로
      // 코드 지정이 막혔다(수정/신규 혼선). 실패는 조용히 무시(아래 색인으로 최선 판단).
      await loadMaterialIndex();
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
          if (result.master_status === "retired") {
            IRMS.notify("폐기된 품목코드입니다 — ERP 현행 코드가 맞는지 확인하세요.", "warn");
          }
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
      // 투입 로스 보정(자재 마스터, 3라운드) 저장 버튼 + Enter.
      dom.codesBody.querySelectorAll(".mat-losscomp-save").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const row = btn.closest(".codes-row");
          const input = row && row.querySelector(".mat-losscomp-input");
          if (input) saveLossComp(row, input.value);
        });
      });
      dom.codesBody.querySelectorAll(".mat-losscomp-input").forEach((input) => {
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            const row = input.closest(".codes-row");
            if (row) saveLossComp(row, input.value);
          }
        });
      });
      dom.codesBody.querySelectorAll(".material-rename-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const row = btn.closest(".codes-row");
          if (row) renameMaterial(row);
        });
      });
      // 동의어 열기/닫기(A6) — 같은 행을 다시 누르면 닫는다(토글).
      dom.codesBody.querySelectorAll(".alias-open-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const row = btn.closest(".codes-row");
          if (row) toggleAliasEditor(row);
        });
      });
    }

    // ── 자재명 수정(A4b) ───────────────────────────────────────────────────
    // 배합 기록의 자재명은 기록 시점 문자열로 박제돼 있어, 이름만 바꾸면 과거 기록이
    // 품목코드를 잃는다. 서버가 옛 이름을 동의어로 남겨 그 끊김을 막는다 —
    // 사용자에게도 그 사실을 미리 알린 뒤 진행한다(모르고 바꾸면 사고가 조용히 난다).
    async function renameMaterial(row) {
      const id = row.getAttribute("data-id");
      const oldName = row.getAttribute("data-name") || "";
      const input = window.prompt(
        `'${oldName}' 의 새 이름을 입력하세요.\n`
          + "옛 이름은 동의어로 남아 과거 배합 기록의 품목코드가 유지됩니다.",
        oldName,
      );
      if (input === null) return; // 취소
      const newName = String(input).trim();
      if (!newName || newName === oldName) return;
      try {
        const resp = await fetch(`/api/materials/${id}/name`, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...csrfHeader() },
          body: JSON.stringify({ name: newName }),
        });
        if (!resp.ok) throw new Error(await detailOf(resp));
        const result = await resp.json();
        IRMS.notify(
          result.alias_kept
            ? `이름을 바꿨습니다. 옛 이름 '${result.alias_kept}' 은 동의어로 남았습니다.`
            : "이름을 바꿨습니다.",
          "success",
        );
        await refresh();
        await loadMaterialIndex();
        if (ctx.refreshMaterials) ctx.refreshMaterials().catch(() => {});
      } catch (err) {
        IRMS.notify(`이름 수정 실패: ${err.message}`, "error");
      }
    }

    // ── 동의어(A6) ─────────────────────────────────────────────────────────
    // 자재 행 바로 아래에 확장 행을 끼워 목록·추가·삭제를 처리한다. 모달을 쓰지 않는
    // 이유는 이 패널의 다른 편집(코드·로스 보정)이 모두 인라인이라 흐름을 맞추기 위함.
    // 한 번에 하나만 열린다 — 여러 행을 펼쳐 두면 어느 자재를 편집 중인지 흐려진다.

    function closeAliasEditor() {
      const open = dom.codesBody.querySelector(".alias-editor-row");
      if (open) open.remove();
    }

    async function toggleAliasEditor(row) {
      const id = row.getAttribute("data-id");
      const next = row.nextElementSibling;
      // 이미 이 행의 편집기가 열려 있으면 닫기만 한다(토글).
      if (next && next.classList.contains("alias-editor-row") && next.getAttribute("data-id") === id) {
        next.remove();
        return;
      }
      closeAliasEditor();
      const name = row.getAttribute("data-name") || "";
      const tr = document.createElement("tr");
      tr.className = "alias-editor-row";
      tr.setAttribute("data-id", id);
      tr.innerHTML =
        `<td colspan="6"><div class="alias-editor">`
        + `<p class="panel-subtitle">${IRMS.escapeHtml(name)} 의 동의어 — 배합 기록에 이 이름으로 남은 실적이 이 자재의 품목코드로 집계됩니다.</p>`
        + `<div class="filter-bar">`
        + `<input class="input alias-new-input" placeholder="기록에 남은 다른 이름" autocomplete="off" />`
        + `<button class="btn accent alias-add-btn" type="button">추가</button>`
        + `<button class="btn alias-close-btn" type="button">닫기</button>`
        + `</div>`
        + `<div class="alias-list"><span class="muted">불러오는 중…</span></div>`
        + `</div></td>`;
      row.parentNode.insertBefore(tr, row.nextSibling);

      tr.querySelector(".alias-close-btn").addEventListener("click", closeAliasEditor);
      const input = tr.querySelector(".alias-new-input");
      const addBtn = tr.querySelector(".alias-add-btn");
      addBtn.addEventListener("click", () => addAlias(id, input));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          addAlias(id, input);
        }
      });
      input.focus();
      await loadAliases(id);
    }

    async function loadAliases(id) {
      const box = dom.codesBody.querySelector(
        `.alias-editor-row[data-id="${id}"] .alias-list`,
      );
      if (!box) return;
      try {
        const data = await IRMS._core.request(`/materials/${id}/aliases`);
        const items = data.items || [];
        if (!items.length) {
          box.innerHTML = '<span class="muted">등록된 동의어가 없습니다.</span>';
          return;
        }
        box.innerHTML = items
          .map(
            (a) =>
              `<div class="alias-item"><span>${IRMS.escapeHtml(a.alias_name)}</span>`
              + `<button class="btn btn-sm danger alias-del-btn" data-alias-id="${a.id}" type="button">해제</button></div>`,
          )
          .join("");
        box.querySelectorAll(".alias-del-btn").forEach((b) => {
          b.addEventListener("click", () =>
            removeAlias(id, b.getAttribute("data-alias-id")),
          );
        });
      } catch (err) {
        box.innerHTML = `<span class="muted">목록 조회 실패: ${IRMS.escapeHtml(err.message)}</span>`;
      }
    }

    async function addAlias(id, input) {
      const value = String(input.value || "").trim();
      if (!value) {
        IRMS.notify("동의어를 입력하세요.", "error");
        input.focus();
        return;
      }
      try {
        const resp = await fetch(`/api/materials/${id}/aliases`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...csrfHeader() },
          body: JSON.stringify({ alias_name: value }),
        });
        if (!resp.ok) throw new Error(await detailOf(resp));
        input.value = "";
        IRMS.notify("동의어를 등록했습니다.", "success");
        await loadAliases(id);
        await refreshKeepingAliasEditor(id);
      } catch (err) {
        IRMS.notify(`동의어 등록 실패: ${err.message}`, "error");
      }
    }

    async function removeAlias(id, aliasId) {
      try {
        const resp = await fetch(`/api/materials/${id}/aliases/${aliasId}`, {
          method: "DELETE",
          credentials: "same-origin",
          headers: csrfHeader(),
        });
        if (!resp.ok) throw new Error(await detailOf(resp));
        IRMS.notify("동의어를 해제했습니다.", "success");
        await loadAliases(id);
        await refreshKeepingAliasEditor(id);
      } catch (err) {
        IRMS.notify(`동의어 해제 실패: ${err.message}`, "error");
      }
    }

    // 목록을 새로 그리면 확장 행이 사라진다(배지 개수는 갱신돼야 한다) — 다시 펼쳐
    // 편집 흐름이 끊기지 않게 한다.
    async function refreshKeepingAliasEditor(id) {
      await refresh();
      const row = dom.codesBody.querySelector(`.codes-row[data-id="${id}"]`);
      if (row) await toggleAliasEditor(row);
    }

    // 자재 마스터 투입 로스 보정 저장 — PUT /api/materials/{id}/loss-comp.
    // 빈 값/0 은 해제(0). 0~100g. 저장 후 행 갱신.
    async function saveLossComp(row, rawValue) {
      const id = Number(row.dataset.id);
      const text = String(rawValue || "").trim();
      let lossComp = null;
      if (text !== "") {
        const v = Number(text);
        if (!Number.isFinite(v) || v < 0 || v > 100) {
          IRMS.notify("로스 보정은 0 이상 100 이하의 숫자여야 합니다. (비우면 해제)", "error");
          return;
        }
        lossComp = v;
      }
      try {
        const resp = await fetch(`/api/materials/${id}/loss-comp`, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...csrfHeader() },
          body: JSON.stringify({ loss_comp_g: lossComp }),
        });
        if (!resp.ok) {
          const detail = await detailOf(resp);
          IRMS.notify(`로스 보정 저장 실패: ${detail}`, "error");
          return;
        }
        IRMS.notify(
          lossComp != null ? `로스 보정을 ${lossComp}g 으로 지정했습니다.` : "로스 보정을 해제했습니다(0).",
          "success",
        );
        await refresh();
      } catch (err) {
        IRMS.notify(`로스 보정 저장 실패: ${err.message}`, "error");
      }
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

      // 편집 취소 시 원래 표시(코드+배지)로 되돌리기 위해 원본을 보관한다 —
      // 종전에는 취소가 무조건 '-' 를 그려서, 행 A 편집 중 행 B [수정]을 누르면
      // 코드가 있는 행 A 가 미지정처럼 보였다(운영자가 중복 지정을 시도할 소지).
      cell._prevHtml = cell.innerHTML;

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
          .map((it) => {
            // 폐기(retired) 코드도 목록에는 나온다 — 숨기면 "왜 안 나오지"가 되므로
            // [폐기] 표기로 알리고 선택은 허용한다(경고는 지정 시점에 한 번 더).
            const retired = it.status === "retired"
              ? ' <span class="muted">[폐기]</span>' : "";
            return `<li class="code-suggest-item" data-code="${IRMS.escapeHtml(it.code)}">${IRMS.escapeHtml(it.code)} — ${IRMS.escapeHtml(it.name)}${retired}</li>`;
          })
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
          // 원래 코드 표시로 원복 — 편집 시작 때 보관한 원본이 있으면 그대로,
          // 없으면(방어) 미지정 표시.
          cell.innerHTML = cell._prevHtml != null
            ? cell._prevHtml
            : '<span class="muted">-</span>';
          delete cell._prevHtml;
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
        if (result.master_status === "retired") {
          IRMS.notify("폐기된 품목코드입니다 — ERP 현행 코드가 맞는지 확인하세요.", "warn");
        }
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
