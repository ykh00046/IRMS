/**
 * blend_drafts_page.js — "작성 중 배합"(/blend/drafts) 화면 컨트롤러.
 *
 * 배합·다중 계량 화면이 이 PC 의 localStorage 에 남긴 임시저장(화면당 최대 3칸)을
 * 한 곳에 모아 보여주고, [이어서 하기] / [삭제] 를 제공한다. 진입 배너를 폐지했으므로
 * 끊긴 작업을 이어서 하는 입구는 이 화면 하나뿐이다.
 *
 * 서버 저장·조회는 없다. 다만 각 초안의 레시피를 GET /blend/recipes/{id} 로 한 번 읽어
 * "복구 전에" 레시피 변경(재료 추가·삭제·기준 배합량 변경)을 배지로 고지한다. 그 판정은
 * 복구 후 배합 화면이 쓰는 것과 같은 blend_drafts.js buildDiff 를 재사용한다.
 *
 * [이어서 하기] → sessionStorage("irms.blend.resume") 에 {kind, id} 를 남기고 해당
 * 화면으로 이동. 그 화면이 DOMContentLoaded 에서 한 번 읽고 지운 뒤 복원한다.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const notify = IRMS.notify || function (m) { console.log(m); };
  const drafts = IRMS.blendDrafts;

  const $ = (id) => document.getElementById(id);
  const esc = drafts ? drafts.esc : (v) => String(v === null || v === undefined ? "" : v);

  // 레시피 상세 캐시 — 같은 레시피의 초안이 두 화면에 걸쳐 있을 수 있다.
  const recipeCache = new Map();

  async function loadRecipe(recipeId) {
    const key = String(recipeId);
    if (recipeCache.has(key)) return recipeCache.get(key);
    let data = null;
    try {
      data = await request(`/blend/recipes/${key}`);
    } catch (_e) {
      data = null;   // 레시피 조회 실패 — 변경 여부 미확인으로 표시(복구 자체는 가능)
    }
    recipeCache.set(key, data);
    return data;
  }

  function chip(className, text) {
    return `<span class="status-chip ${className}">${esc(text)}</span>`;
  }

  // 복구 전 고지 배지 + 상세 문구. diff 가 null 이면 레시피 조회 실패.
  function changeCellHtml(kind, slot, recipeData) {
    if (!recipeData) {
      return chip("drafts-badge-info", "레시피 확인 불가");
    }
    const diff = drafts.buildDiff(kind, slot, recipeData);
    if (diff.legacy) {
      return chip("drafts-badge-warn", "오래된 임시저장")
        + '<p class="drafts-change-note">레시피 변경 여부를 확인할 수 없는 오래된 임시저장입니다 '
        + "— 이어서 한 뒤 재료별 값을 직접 확인하세요.</p>";
    }
    if (!diff.changed) return "";
    const badges = [];
    if (diff.dropped.length) badges.push(chip("drafts-badge-error", `재료 삭제 ${diff.dropped.length}`));
    if (diff.added.length) badges.push(chip("drafts-badge-warn", `재료 추가 ${diff.added.length}`));
    if (diff.baseNotes.length) badges.push(chip("drafts-badge-warn", "기준 배합량 변경"));

    const notes = [];
    if (diff.dropped.length) {
      notes.push("<li>레시피에서 삭제됨: "
        + diff.dropped.map((d) => `${esc(d.name)} — ${esc(d.text)}`).join(" / ")
        + "</li>");
    }
    if (diff.added.length) {
      notes.push(`<li>새 재료(계량 전): ${esc(diff.added.join(", "))}</li>`);
    }
    diff.baseNotes.forEach((t) => notes.push(`<li>${esc(t)}</li>`));

    return `<div class="drafts-badges">${badges.join("")}</div>`
      + `<div class="drafts-change-note"><ul>${notes.join("")}</ul></div>`;
  }

  function rowHtml(entry, changeHtml) {
    const slot = entry.slot;
    const prog = drafts.progressOf(entry.kind, slot);
    const progText = prog.total ? `${prog.filled} / ${prog.total} 칸` : "-";
    const name = slot.product_name || "(제품명 없음)";
    // 삭제 확인 문구에 쓸 제품명은 data 속성으로 따로 싣는다 — 셀 textContent 에는
    // 변경 배지·상세 문구가 섞여 있어 그대로 쓰면 확인창이 읽을 수 없게 된다.
    return `<tr data-kind="${esc(entry.kind)}" data-id="${esc(slot.id)}" data-name="${esc(name)}">`
      + `<td class="product-cell">${esc(name)}${changeHtml}</td>`
      + `<td>${esc(entry.label)}</td>`
      + `<td class="drafts-progress">${esc(progText)}</td>`
      + `<td class="drafts-saved">${esc(drafts.savedAtText(slot) || "-")}</td>`
      + '<td><div class="drafts-actions">'
      + '<button type="button" class="btn btn-sm accent drafts-resume">이어서 하기</button>'
      + '<button type="button" class="btn btn-sm drafts-delete">삭제</button>'
      + "</div></td></tr>";
  }

  function showEmpty() {
    $("drafts-loading").hidden = true;
    $("drafts-list-panel").hidden = true;
    $("drafts-empty-panel").hidden = false;
  }

  async function render() {
    if (!drafts) { showEmpty(); return; }
    const entries = drafts.listAll();
    if (!entries.length) { showEmpty(); return; }

    // 레시피 변경 고지는 복구 "전"에 보여야 의미가 있다 — 목록을 그리기 전에 모아 읽는다.
    const recipes = await Promise.all(entries.map((e) => loadRecipe(e.slot.recipe_id)));
    const rows = entries.map((e, i) => rowHtml(e, changeCellHtml(e.kind, e.slot, recipes[i])));

    $("drafts-body").innerHTML = rows.join("");
    $("drafts-loading").hidden = true;
    $("drafts-empty-panel").hidden = true;
    $("drafts-list-panel").hidden = false;
  }

  function onBodyClick(event) {
    const row = event.target.closest ? event.target.closest("tr[data-id]") : null;
    if (!row) return;
    const kind = row.dataset.kind;
    const id = row.dataset.id;
    const meta = drafts.kindMeta(kind);
    if (!meta) return;

    if (event.target.classList.contains("drafts-resume")) {
      // 어느 슬롯을 이어서 할지 sessionStorage 로 전달(탭 단위, 한 번 읽고 지움).
      if (!drafts.setResume(kind, id)) {
        notify("이 브라우저에서 이어서 하기를 사용할 수 없습니다(저장소 접근 불가).", "error");
        return;
      }
      window.location.assign(meta.path);
      return;
    }
    if (event.target.classList.contains("drafts-delete")) {
      const label = row.dataset.name || "";
      if (!window.confirm(`'${label}' 임시저장을 삭제할까요? 계량값은 되돌릴 수 없습니다.`)) return;
      drafts.removeSlot(kind, id);
      notify("임시저장을 삭제했습니다.", "success");
      render().catch((e) => notify(e.message, "error"));
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!request) { console.error("IRMS core not loaded"); return; }
    const body = $("drafts-body");
    if (body) body.addEventListener("click", onBodyClick);
    render().catch((e) => {
      notify(`임시저장 목록을 읽지 못했습니다: ${e.message}`, "error");
      showEmpty();
    });
  });
})();
