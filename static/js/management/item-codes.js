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
 *   GET  /api/item-codes/materials        — 자재 목록(uncoded/q/include_inactive 필터)
 *   GET  /api/item-codes/master           — 마스터 제안(q, kind=material)
 *   PUT  /api/materials/{id}/code         — 자재 코드 지정/해제(빈 값 = 해제)
 *   PUT  /api/materials/{id}/name         — 자재명 수정(과거 기록 표기까지 전파)
 *   PUT  /api/materials/{id}/active       — 사용 안 함(숨김) / 다시 사용
 *   DELETE /api/materials/{id}            — 자재 삭제(레시피 참조 시 409 → 숨김 제안)
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

    function includeInactive() {
      const cb = document.getElementById("codes-include-inactive");
      return !!(cb && cb.checked);
    }

    function activeFilters() {
      const kindSel = document.getElementById("codes-kind");
      return {
        uncoded: dom.codesUncoded && dom.codesUncoded.checked ? "1" : undefined,
        include_inactive: includeInactive() ? "1" : undefined,
        // 분류(코드 계열) 필터 — 값은 서버 파생(raw/product/managed/uncoded).
        kind: kindSel && kindSel.value ? kindSel.value : undefined,
        q: dom.codesSearch ? dom.codesSearch.value.trim() : "",
      };
    }

    // 제안 목록이 표 밖으로 뜨도록 표 래퍼의 클리핑을 잠시 푼다 —
    // .table-wrap 의 overflow-x:auto 는 세로도 함께 잘라낸다(CSS 규격).
    function setSuggestClipping(open) {
      if (!dom.codesBody) return;
      const wrap = dom.codesBody.closest(".table-wrap");
      if (wrap) wrap.classList.toggle("suggest-open", !!open);
    }

    async function refresh() {
      if (!dom.codesBody) return;
      editingMaterialId = null;
      setSuggestClipping(false);
      const filters = activeFilters();
      try {
        const data = await IRMS._core.request("/item-codes/materials", { query: filters });
        const items = data.items || [];
        if (!items.length) {
          dom.codesBody.innerHTML =
            '<tr><td colspan="5"><div class="empty-state">조건에 맞는 자재가 없습니다.</div></td></tr>';
          return;
        }
        dom.codesBody.innerHTML = items
          .map((m) => {
            const code = m.code || "";
            // 분류(코드 계열) — 별도 열. 값은 코드가 결정하는 파생(원자재/반제품/관리용)
            // 이라 코드를 지정·변경하면 자동으로 따라온다(따로 입력하지 않는다).
            const KIND_LABEL = { raw: "원자재", product: "반제품", managed: "관리용" };
            const kindLabel = code && m.code_kind ? KIND_LABEL[m.code_kind] : "";
            const kindCell = kindLabel
              ? `<span class="code-kind-badge">${IRMS.escapeHtml(kindLabel)}</span>`
              : '<span class="muted">코드 없음</span>';
            const codeHtml = code
              ? `<span class="code-value">${IRMS.escapeHtml(code)}</span>`
              : '<span class="muted">-</span>';
            // [해제] 버튼은 없앴다 — 인라인 편집기에서 값을 비우고 저장하면 해제다.
            // 두 개의 빨간 버튼([해제]/[삭제])이 나란히 있어 현장에서 구분이 안 됐다.
            const codeActions = code
              ? `<button class="btn btn-sm code-edit-btn" data-id="${m.id}" type="button">수정</button>`
              : `<button class="btn btn-sm accent code-edit-btn" data-id="${m.id}" type="button">지정</button>`;
            // 사용 안 함(is_active=0) 자재는 되살리기 하나만 — 숨긴 행에 편집을 권하지 않는다.
            const inactive = Number(m.is_active) === 0;
            // 이름 정리(흡수) — 정리할 옛 동의어가 남은 자재에만 버튼을 보인다.
            // 전용 열이었지만 정리가 끝나면 전 행이 빈 버튼만 남아 자리를 차지했다.
            const aliasCount = Number(m.alias_count) || 0;
            const aliasBtn = aliasCount
              ? ` <button class="btn btn-sm alias-open-btn" data-id="${m.id}" type="button"`
                + ` title="다른 표기로 남은 과거 기록을 이 자재의 이름으로 통합합니다">`
                + `이름 정리 ${aliasCount}</button>`
              : "";
            const actionHtml = inactive
              ? `<button class="btn btn-sm accent material-reactivate-btn" data-id="${m.id}" type="button"`
                + ` title="이 자재를 목록에 다시 표시합니다">다시 사용</button>`
              : `${codeActions}`
                + ` <button class="btn btn-sm material-rename-btn" data-id="${m.id}" type="button"`
                + ` title="자재명 수정 - 과거 기록의 표기도 함께 바뀝니다">이름 수정</button>`
                + aliasBtn
                + ` <button class="btn btn-sm danger material-delete-btn" data-id="${m.id}" type="button">삭제</button>`;
            const inactiveTag = inactive
              ? ' <span class="muted">사용 안 함</span>'
              : "";
            // 투입 로스 보정(자재 마스터 기본값, 3라운드) — 인라인 입력+저장. 값이 있으면 표시.
            const compVal = Number(m.loss_comp_g) > 0 ? String(m.loss_comp_g) : "";
            const lossCompHtml = `<input class="input mat-losscomp-input" data-id="${m.id}" type="number" step="0.1" min="0" max="100" value="${IRMS.escapeHtml(compVal)}" placeholder="0" title="투입 로스 보정(g) — 이 자재가 들어가는 모든 레시피에 자동 적용" />`
              + `<button class="btn btn-sm mat-losscomp-save" data-id="${m.id}" type="button">저장</button>`;
            return `
              <tr class="codes-row${inactive ? " is-inactive" : ""}" data-id="${m.id}" data-name="${IRMS.escapeHtml(m.name)}">
                <td class="name-cell">${IRMS.escapeHtml(m.name)}${inactiveTag}</td>
                <td class="code-cell">${codeHtml}</td>
                <td class="kind-cell">${kindCell}</td>
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
        // 색인은 '사용 안 함' 자재까지 담는다 — 숨긴 자재명을 빠른 지정에 적으면
        // 신규 등록(POST)으로 빠져 "이미 등록된 자재명" 409 막다른 길이 됐다.
        // 추천 목록(datalist)에는 쓰는 자재만 올린다.
        const data = await IRMS._core.request("/item-codes/materials", {
          query: { include_inactive: "1" },
        });
        const items = data.items || [];
        matNameToId = {};
        items.forEach((m) => {
          matNameToId[String(m.name).trim().toLowerCase()] = m.id;
        });
        if (dl) {
          dl.innerHTML = items
            .filter((m) => Number(m.is_active) !== 0)
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
      const inactiveCb = document.getElementById("codes-include-inactive");
      if (inactiveCb) {
        inactiveCb.addEventListener("change", refresh);
      }
      const kindSel = document.getElementById("codes-kind");
      if (kindSel) {
        kindSel.addEventListener("change", refresh);
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
      dom.codesBody.querySelectorAll(".material-delete-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const row = btn.closest(".codes-row");
          if (row) deleteMaterial(row);
        });
      });
      dom.codesBody.querySelectorAll(".material-reactivate-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const row = btn.closest(".codes-row");
          if (row) setActive(Number(row.dataset.id), 1);
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
          if (row) startRenameEdit(row);
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
    // 이름은 하나다 — 바꾸면 서버가 과거 배합 기록의 표기까지 같이 통일한다(동의어를
    // 새로 만들지 않는다). 편집은 코드와 같은 인라인 편집기로 한다: window.prompt 는
    // 화면 밖 팝업이라 어느 행을 고치는지 안 보이고, 자재명이 고정값처럼 느껴졌다.
    function startRenameEdit(row) {
      const cell = row.querySelector(".name-cell");
      if (!cell || cell.querySelector(".name-inline-input")) return;
      cancelAllInlineEdits();
      const oldName = row.getAttribute("data-name") || "";
      cell._prevHtml = cell.innerHTML;
      cell.innerHTML = `
        <div class="code-edit-wrap">
          <input class="input compact name-inline-input" value="${IRMS.escapeHtml(oldName)}" placeholder="새 자재명" />
          <button class="btn btn-sm success name-save-btn" type="button">저장</button>
          <button class="btn btn-sm name-cancel-btn" type="button">취소</button>
        </div>`;

      const input = cell.querySelector(".name-inline-input");
      input.focus();
      input.select();
      input.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          saveName(row, input.value);
        } else if (ev.key === "Escape") {
          ev.preventDefault();
          cancelAllInlineEdits();
        }
      });
      cell.querySelector(".name-save-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        saveName(row, input.value);
      });
      cell.querySelector(".name-cancel-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        cancelAllInlineEdits();
      });
    }

    async function saveName(row, rawValue) {
      const id = row.getAttribute("data-id");
      const oldName = row.getAttribute("data-name") || "";
      const newName = String(rawValue || "").trim();
      if (!newName) {
        IRMS.notify("자재명을 입력하세요.", "error");
        return;
      }
      if (newName === oldName) {
        cancelAllInlineEdits();
        return;
      }
      try {
        const resp = await fetch(`/api/materials/${id}/name`, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...csrfHeader() },
          body: JSON.stringify({ name: newName }),
        });
        if (!resp.ok) throw new Error(await detailOf(resp));
        const result = await resp.json();
        const changed = Number(result.updated_records) || 0;
        IRMS.notify(
          changed > 0
            ? `이름을 바꿨습니다 - 과거 기록 ${changed}건의 표기도 함께 바뀌었습니다.`
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

    // ── 기록 이름 정리(A6 — 2026-08-13 흡수 방식 전환) ─────────────────────
    // 코드 중심·이름 하나 원칙: 변형 표기를 동의어(영구 다리)로 쌓는 대신, 그 표기로
    // 남은 과거 기록을 그 자리에서 정본 이름으로 고쳐 쓰고 끝낸다(흡수). 남아 있는
    // 옛 동의어도 [흡수 후 삭제] 로 같은 방식으로 정리한다.
    // 자재 행 바로 아래에 확장 행을 끼우고, 한 번에 하나만 연다(기존 패턴 유지).

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
        `<td colspan="5"><div class="alias-editor">`
        + `<p class="panel-subtitle">${IRMS.escapeHtml(name)} 의 기록 이름 정리 — 다른 표기로 남은 과거 배합 기록을 이 자재의 이름으로 통합합니다(이름은 하나만 유지).</p>`
        + `<div class="filter-bar">`
        + `<input class="input alias-new-input" placeholder="기록에 남은 다른 표기 (예: MEHQ)" autocomplete="off" />`
        + `<button class="btn accent alias-add-btn" type="button">기록 흡수</button>`
        + `<button class="btn alias-close-btn" type="button">닫기</button>`
        + `</div>`
        + `<div class="alias-list"><span class="muted">불러오는 중…</span></div>`
        + `</div></td>`;
      row.parentNode.insertBefore(tr, row.nextSibling);

      tr.querySelector(".alias-close-btn").addEventListener("click", closeAliasEditor);
      const input = tr.querySelector(".alias-new-input");
      const addBtn = tr.querySelector(".alias-add-btn");
      addBtn.addEventListener("click", () => absorbName(id, input.value, input));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          absorbName(id, input.value, input);
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
          box.innerHTML = '<span class="muted">정리할 옛 동의어가 없습니다.</span>';
          return;
        }
        // 남아 있는 옛 동의어 — [흡수 후 삭제] 로 그 표기의 기록을 통합하며 정리한다.
        box.innerHTML = items
          .map(
            (a) =>
              `<div class="alias-item"><span>${IRMS.escapeHtml(a.alias_name)}</span>`
              + `<button class="btn btn-sm alias-del-btn" data-alias-name="${IRMS.escapeHtml(a.alias_name)}" type="button"`
              + ` title="이 표기로 남은 기록을 정본 이름으로 통합하고 동의어를 지웁니다">흡수 후 삭제</button></div>`,
          )
          .join("");
        box.querySelectorAll(".alias-del-btn").forEach((b) => {
          b.addEventListener("click", () =>
            absorbName(id, b.getAttribute("data-alias-name"), null),
          );
        });
      } catch (err) {
        box.innerHTML = `<span class="muted">목록 조회 실패: ${IRMS.escapeHtml(err.message)}</span>`;
      }
    }

    // 기록 표기 흡수 — 그 표기로 남은 과거 기록을 이 자재의 정본 이름으로 통합.
    // 같은 표기의 옛 동의어가 있으면 서버가 함께 지운다. input 이 있으면(직접 입력
    // 경로) 성공 시 비운다.
    async function absorbName(id, rawValue, input) {
      const value = String(rawValue || "").trim();
      if (!value) {
        IRMS.notify("기록에 남은 표기를 입력하세요.", "error");
        if (input) input.focus();
        return;
      }
      try {
        const resp = await fetch(`/api/materials/${id}/absorb-name`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...csrfHeader() },
          body: JSON.stringify({ name: value }),
        });
        if (!resp.ok) throw new Error(await detailOf(resp));
        const result = await resp.json();
        const n = Number(result.absorbed_records) || 0;
        const cleaned = Number(result.alias_removed) ? " 옛 동의어도 정리했습니다." : "";
        IRMS.notify(
          n
            ? `'${value}' 표기 기록 ${n}건을 '${result.canonical}' 으로 통합했습니다.${cleaned}`
            : `'${value}' 표기로 남은 기록이 없습니다.${cleaned}`,
          n ? "success" : "warn",
        );
        if (input) input.value = "";
        await loadAliases(id);
        await refreshKeepingAliasEditor(id);
      } catch (err) {
        IRMS.notify(`기록 흡수 실패: ${err.message}`, "error");
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
          <input class="input compact code-inline-input" value="${IRMS.escapeHtml(currentValue)}" placeholder="코드 입력 - 비우고 저장하면 해제" />
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
          setSuggestClipping(false);
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
        }
      });

      cell.querySelector(".code-save-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        saveCode(row, input.value);
      });
      cell.querySelector(".code-cancel-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        cancelAllInlineEdits();
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
          setSuggestClipping(false);
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
        setSuggestClipping(true);
        suggestList.querySelectorAll(".code-suggest-item").forEach((li) => {
          li.addEventListener("mousedown", (ev) => {
            ev.preventDefault(); // input blur 보존
            input.value = li.dataset.code;
            suggestList.hidden = true;
            setSuggestClipping(false);
            input.focus();
          });
        });
      } catch (_err) {
        suggestList.hidden = true;
        setSuggestClipping(false);
      }
    }

    // 편집 취소 — 열려 있는 인라인 편집(코드·자재명)을 모두 닫고 원래 셀로 되돌린다.
    // 편집기는 코드 셀과 이름 셀 양쪽에 뜨므로 셀 종류를 가리지 않고 원본을 복원한다.
    function cancelAllInlineEdits() {
      editingMaterialId = null;
      setSuggestClipping(false);
      dom.codesBody.querySelectorAll(".code-edit-wrap").forEach((w) => {
        const cell = w.closest("td");
        if (!cell) return;
        // 편집 시작 때 보관한 원본이 있으면 그대로, 없으면(방어) 미지정 표시.
        cell.innerHTML = cell._prevHtml != null
          ? cell._prevHtml
          : '<span class="muted">-</span>';
        delete cell._prevHtml;
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

    // ── 자재 삭제 → 막히면 '사용 안 함' 으로 ──
    // A5 DELETE 는 과거 레시피가 참조하는 자재를 409 로 막는다. 종전에는 거기서
    // 끝이라 운영자에게 막다른 길이었다 — 409 면 곧바로 숨기기(사용 안 함)를 권한다.
    // 데이터는 그대로 남고 '사용 안 함 포함' 목록에서 언제든 되살릴 수 있다.
    async function deleteMaterial(row) {
      const id = Number(row.dataset.id);
      const name = row.dataset.name || "";
      if (
        !window.confirm(
          `자재 '${name}' 를 삭제할까요? 배합 기록의 연결이 끊깁니다(기록 자체는 남음).`,
        )
      ) {
        return;
      }
      try {
        const resp = await fetch(`/api/materials/${id}`, {
          method: "DELETE",
          credentials: "same-origin",
          headers: { ...csrfHeader() },
        });
        if (!resp.ok) {
          const msg = await detailOf(resp);
          if (resp.status === 409) {
            const hide = window.confirm(
              `삭제할 수 없습니다: ${msg}\n`
                + "대신 '사용 안 함'으로 목록에서 숨길까요? 데이터는 보존되고 언제든 되살릴 수 있습니다.",
            );
            if (hide) await setActive(id, 0);
            return;
          }
          IRMS.notify(`자재 삭제 실패: ${msg}`, "error");
          return;
        }
        const result = await resp.json();
        const deletedName = result.deleted || "";
        IRMS.notify(`자재 '${deletedName}' 을 삭제했습니다.`, "success");
        await refresh();
        await loadMaterialIndex();
        // BOM 편집기 자재 색인 갱신 — fire-and-forget(실패해도 패널 동작엔 영향 없음).
        if (ctx.refreshMaterials) ctx.refreshMaterials().catch(() => {});
      } catch (err) {
        IRMS.notify(`자재 삭제 실패: ${err.message}`, "error");
      }
    }

    // 사용 안 함(0) / 다시 사용(1) — A5c PUT. 삭제와 달리 어떤 데이터도 지우지 않는다.
    async function setActive(id, active) {
      try {
        const resp = await fetch(`/api/materials/${id}/active`, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", ...csrfHeader() },
          body: JSON.stringify({ is_active: active }),
        });
        if (!resp.ok) throw new Error(await detailOf(resp));
        IRMS.notify(
          active
            ? "다시 사용으로 되돌렸습니다."
            : "'사용 안 함'으로 숨겼습니다. [사용 안 함 포함] 을 켜면 되살릴 수 있습니다.",
          "success",
        );
        await refresh();
        await loadMaterialIndex();
        if (ctx.refreshMaterials) ctx.refreshMaterials().catch(() => {});
      } catch (err) {
        IRMS.notify(`상태 변경 실패: ${err.message}`, "error");
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
