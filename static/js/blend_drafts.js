/**
 * blend_drafts.js — 배합·다중 계량 임시저장(초안) 슬롯 저장소 + 레시피 변경 정합성 헬퍼.
 *
 * 배경: 예전에는 화면마다 localStorage 1칸("irms.blend.draft" / "irms.blend.cont.draft")
 * 만 두고, 진입 시 배너로 "이어서 하시겠어요?"를 물었다. 두 가지 문제가 있었다.
 *   (1) 1칸뿐이라 다른 레시피를 잠깐 만지면 앞 작업이 조용히 덮어써졌다.
 *   (2) 복구가 "위치 인덱스" 기반이라, 초안 저장 후 레시피가 편집되면(재료 순서 변경·
 *       중간 삽입/삭제) 사람이 저울로 계량한 값이 다른 재료 줄에 조용히 들어가고,
 *       재료 수가 줄면 초과 줄의 계량값이 아무 말 없이 사라졌다.
 *
 * 이 모듈은 그 둘을 한 곳에서 해결한다.
 *   - 화면당 최대 3칸(MAX_SLOTS). 초과 시 가장 오래된 것부터 밀어냄. 24시간 만료 유지.
 *   - 같은 레시피로 이어서 작업 중이면 새 슬롯을 만들지 않고 같은 슬롯을 갱신.
 *   - 옛 1칸 초안은 첫 읽기 때 새 목록의 첫 항목으로 이월(유실 금지).
 *   - 초안에 줄마다 품목 식별자(품목코드 우선, 없으면 품목명)를 기록하고, 복구 시
 *     위치가 아니라 그 식별자로 매칭한다(alignRows).
 *   - 사라진 재료의 계량값은 버리지 않고 목록으로 보고(droppedEntries), 새로 추가된
 *     재료·기준 배합량 변경은 복구 전에 고지(addedNames / baseChanges).
 *
 * 저장 스키마 (slot):
 *   { id, kind, schema:2, recipe_id, product_name, savedAt,
 *     materials: [{code, name}],                     // 줄 순서대로의 품목 식별자
 *     recipeMeta: {default_totals, tolerance_g, anchor_material_id},
 *     ...화면별 진행분(blend: items[] / cont: cells[][]) }
 *   schema/materials 가 없으면 "옛 초안" — 위치 기반 복구 + 경고.
 *
 * 화면 간 전달: sessionStorage "irms.blend.resume" = {kind, id} (한 번 읽고 지움).
 *
 * Side effects: window.IRMS.blendDrafts 부착만. 모든 저장소 접근은 인자로 주입 가능
 * (테스트에서 가짜 storage 를 넘긴다).
 */
(function () {
  "use strict";

  const root = (typeof window !== "undefined") ? window : {};
  const IRMS = root.IRMS = root.IRMS || {};

  // 화면당 임시저장 칸 수. 초과 시 savedAt 이 가장 오래된 것부터 밀어낸다.
  const MAX_SLOTS = 3;
  // 만료(24시간) — 옛 1칸 시절 규칙 그대로 유지.
  const TTL_MS = 24 * 3600 * 1000;
  // 품목 식별자를 기록하기 시작한 스키마 버전. 이 값이 없으면 옛 초안.
  const SCHEMA = 2;
  const RESUME_KEY = "irms.blend.resume";

  const KINDS = {
    blend: {
      kind: "blend",
      label: "배합",
      path: "/blend",
      key: "irms.blend.drafts",
      legacyKey: "irms.blend.draft",
    },
    cont: {
      kind: "cont",
      label: "다중 계량",
      path: "/blend/continuous",
      key: "irms.blend.cont.drafts",
      legacyKey: "irms.blend.cont.draft",
    },
  };
  const KIND_ORDER = ["blend", "cont"];

  function kindMeta(kind) {
    return KINDS[kind] || null;
  }

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ── 저장소 접근(주입 가능) ──────────────────────────────────────
  function localStore(storage) {
    if (storage) return storage;
    try { return root.localStorage || null; } catch (_e) { return null; }
  }

  function sessionStore(storage) {
    if (storage) return storage;
    try { return root.sessionStorage || null; } catch (_e) { return null; }
  }

  function safeGet(st, key) {
    try { return st.getItem(key); } catch (_e) { return null; }
  }

  function safeSet(st, key, value) {
    try { st.setItem(key, value); return true; } catch (_e) { return false; }
  }

  function safeRemove(st, key) {
    try { st.removeItem(key); } catch (_e) { /* 무시 */ }
  }

  function parseJson(raw) {
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (_e) { return null; }
  }

  let _seq = 0;
  function newId() {
    _seq += 1;
    const rand = Math.floor(Math.random() * 1e9).toString(36);
    return "d" + Date.now().toString(36) + "-" + _seq.toString(36) + "-" + rand;
  }

  // ── 슬롯 목록 읽기/쓰기 ─────────────────────────────────────────
  function isFresh(slot) {
    if (!slot || !slot.recipe_id) return false;
    if (!slot.savedAt) return true;               // 시각이 없으면 만료 판정 불가 — 살린다
    const t = Date.parse(slot.savedAt);
    if (!Number.isFinite(t)) return true;
    return (Date.now() - t) <= TTL_MS;
  }

  function bySavedDesc(a, b) {
    return String(b && b.savedAt || "").localeCompare(String(a && a.savedAt || ""));
  }

  /**
   * 화면(kind)의 초안 슬롯 목록. 최신순 정렬, 24시간 지난 것은 제외.
   * 옛 1칸 키에 값이 있으면 이 시점에 새 목록의 첫 항목으로 이월하고 옛 키를 지운다
   * (이월 결과를 즉시 write 해 유실을 막는다).
   */
  function readSlots(kind, storage) {
    const meta = kindMeta(kind);
    const st = localStore(storage);
    if (!meta || !st) return [];

    const parsed = parseJson(safeGet(st, meta.key));
    let slots = Array.isArray(parsed)
      ? parsed.filter((s) => s && typeof s === "object")
      : [];

    // 옛 1칸 초안 마이그레이션 — 사용자 진행분 유실 금지.
    const legacyRaw = safeGet(st, meta.legacyKey);
    if (legacyRaw) {
      const legacy = parseJson(legacyRaw);
      if (legacy && legacy.recipe_id) {
        if (!legacy.id) legacy.id = newId();
        legacy.kind = kind;
        if (!slots.some((s) => s.id === legacy.id)) slots.unshift(legacy);
      }
      safeRemove(st, meta.legacyKey);
      safeSet(st, meta.key, JSON.stringify(slots));
    }

    const fresh = [];
    for (const s of slots) {
      if (!isFresh(s)) continue;
      fresh.push(s.id ? s : Object.assign({}, s, { id: newId() }));
    }
    fresh.sort(bySavedDesc);
    return fresh;
  }

  function writeSlots(kind, slots, storage) {
    const meta = kindMeta(kind);
    const st = localStore(storage);
    if (!meta || !st) return false;
    return safeSet(st, meta.key, JSON.stringify(Array.isArray(slots) ? slots : []));
  }

  /**
   * 초안 저장. 같은 레시피로 이어서 작업 중이면 같은 슬롯을 갱신하고(슬롯 폭증 방지),
   * 새 작업이면 슬롯을 추가한다. 3칸을 넘으면 savedAt 이 가장 오래된 것부터 밀어낸다.
   * 반환: 저장된 슬롯 id (호출부는 이 값을 보관해 다음 저장의 preferredId 로 넘긴다).
   */
  function saveSlot(kind, draft, storage, preferredId) {
    const meta = kindMeta(kind);
    const st = localStore(storage);
    if (!meta || !st || !draft || !draft.recipe_id) return null;

    let slots = readSlots(kind, st);
    const rid = String(draft.recipe_id);
    let idx = -1;
    if (preferredId) {
      idx = slots.findIndex((s) => s.id === preferredId && String(s.recipe_id) === rid);
    }
    if (idx < 0) idx = slots.findIndex((s) => String(s.recipe_id) === rid);

    const id = idx >= 0 ? slots[idx].id : newId();
    const slot = Object.assign({}, draft, { id, kind, schema: draft.schema || SCHEMA });
    if (idx >= 0) slots[idx] = slot;
    else slots.push(slot);

    if (slots.length > MAX_SLOTS) {
      slots.sort(bySavedDesc);
      const kept = slots.slice(0, MAX_SLOTS);
      // 방금 저장한 슬롯은 어떤 경우에도 살아남아야 한다(시각이 비어 있을 때 대비).
      if (!kept.some((s) => s.id === id)) kept[kept.length - 1] = slot;
      slots = kept;
    }
    writeSlots(kind, slots, st);
    return id;
  }

  function getSlot(kind, id, storage) {
    if (!id) return null;
    const found = readSlots(kind, storage).find((s) => s.id === id);
    return found || null;
  }

  function removeSlot(kind, id, storage) {
    if (!id) return false;
    const slots = readSlots(kind, storage);
    const next = slots.filter((s) => s.id !== id);
    if (next.length === slots.length) {
      writeSlots(kind, next, storage);   // 만료분 정리는 그대로 반영
      return false;
    }
    writeSlots(kind, next, storage);
    return true;
  }

  /** 두 화면의 초안을 합쳐 최신순으로. 항목: {kind, label, path, slot}. */
  function listAll(storage) {
    const out = [];
    KIND_ORDER.forEach((kind) => {
      const meta = KINDS[kind];
      readSlots(kind, storage).forEach((slot) => {
        out.push({ kind, label: meta.label, path: meta.path, slot });
      });
    });
    out.sort((a, b) => bySavedDesc(a.slot, b.slot));
    return out;
  }

  // ── 화면 간 전달(어느 슬롯을 이어서 할지) ───────────────────────
  function setResume(kind, id, storage) {
    const ss = sessionStore(storage);
    if (!ss || !kind || !id) return false;
    return safeSet(ss, RESUME_KEY, JSON.stringify({ kind, id }));
  }

  /** 한 번 읽고 지운다 — 새로고침이 복구를 반복 트리거하지 않게. */
  function takeResume(kind, storage) {
    const ss = sessionStore(storage);
    if (!ss) return null;
    const raw = safeGet(ss, RESUME_KEY);
    if (!raw) return null;
    safeRemove(ss, RESUME_KEY);
    const value = parseJson(raw);
    if (!value || !value.id) return null;
    if (kind && value.kind !== kind) return null;
    return value.id;
  }

  // ── 품목 식별자 매칭 ────────────────────────────────────────────
  function normCode(value) {
    return String(value === null || value === undefined ? "" : value).trim().toLowerCase();
  }

  function normName(value) {
    return String(value === null || value === undefined ? "" : value)
      .trim().toLowerCase().replace(/\s+/g, "");
  }

  /** 초안 저장용 줄 식별자 배열 — [{code, name}]. */
  function materialIdentities(items) {
    return (Array.isArray(items) ? items : []).map((it) => ({
      code: (it && it.material_code) ? String(it.material_code) : "",
      name: (it && it.material_name) ? String(it.material_name) : "",
    }));
  }

  /** 레시피 메타 스냅샷 — 복구 전 '기준 배합량/설정 변경' 고지에 쓴다. */
  function recipeMetaOf(recipe) {
    const r = recipe || {};
    return {
      default_totals: Array.isArray(r.default_totals) ? r.default_totals.map(Number) : [],
      tolerance_g: r.tolerance_g === null || r.tolerance_g === undefined ? null : Number(r.tolerance_g),
      anchor_material_id: r.anchor_material_id === null || r.anchor_material_id === undefined
        ? null : String(r.anchor_material_id),
    };
  }

  function slotIsLegacy(slot) {
    return !(slot && Array.isArray(slot.materials) && slot.materials.length);
  }

  /**
   * 초안 줄 ↔ 현재 레시피 줄 매칭(순수). 품목코드 우선, 없으면 품목명.
   * 같은 식별자가 여러 줄이면 먼저 나온 미사용 줄부터 소비한다(중복 재료 안전).
   *
   * 반환: { legacy, map, addedIdx, removedIdx }
   *   legacy=true  → 식별자 없는 옛 초안. map=null 이며 호출부는 위치 기반으로 복구한다.
   *   map[draftIdx] = currentIdx (없으면 -1 = 레시피에서 사라진 재료)
   *   addedIdx     = 초안에 없던(=새로 추가된) 현재 레시피 줄 인덱스
   *   removedIdx   = 현재 레시피에 없는 초안 줄 인덱스
   */
  function alignRows(draftMaterials, currentMaterials) {
    const cur = Array.isArray(currentMaterials) ? currentMaterials : [];
    const dm = Array.isArray(draftMaterials) ? draftMaterials : [];
    if (!dm.length) {
      return { legacy: true, map: null, addedIdx: [], removedIdx: [] };
    }
    const used = new Array(cur.length).fill(false);
    const map = new Array(dm.length).fill(-1);

    // 1차: 품목코드 일치
    for (let i = 0; i < dm.length; i++) {
      const code = normCode(dm[i] && dm[i].code);
      if (!code) continue;
      for (let j = 0; j < cur.length; j++) {
        if (used[j]) continue;
        if (normCode(cur[j] && cur[j].code) !== code) continue;
        map[i] = j; used[j] = true; break;
      }
    }
    // 2차: 품목명 일치(코드가 없거나 코드로 못 찾은 줄)
    for (let i = 0; i < dm.length; i++) {
      if (map[i] >= 0) continue;
      const name = normName(dm[i] && dm[i].name);
      if (!name) continue;
      for (let j = 0; j < cur.length; j++) {
        if (used[j]) continue;
        if (normName(cur[j] && cur[j].name) !== name) continue;
        map[i] = j; used[j] = true; break;
      }
    }

    const removedIdx = [];
    for (let i = 0; i < map.length; i++) if (map[i] < 0) removedIdx.push(i);
    const addedIdx = [];
    for (let j = 0; j < cur.length; j++) if (!used[j]) addedIdx.push(j);
    return { legacy: false, map, addedIdx, removedIdx };
  }

  function fmtG(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    return n.toFixed(2) + "g";
  }

  /**
   * 새 레시피에서 사라진 재료의 계량값 목록(순수). 조용한 폐기 금지 — 이 목록을
   * 사용자에게 그대로 보여준다. 반환: [{name, text}].
   */
  function droppedEntries(kind, slot, removedIdx) {
    const mats = (slot && slot.materials) || [];
    const out = [];
    (removedIdx || []).forEach((i) => {
      const name = (mats[i] && mats[i].name) || `${i + 1}번 재료`;
      if (kind === "cont") {
        const row = ((slot && slot.cells) || [])[i] || [];
        const parts = [];
        row.forEach((c, j) => {
          const a = c && c.actual;
          if (a !== "" && a !== null && a !== undefined) parts.push(`로트${j + 1} ${fmtG(a)}`);
        });
        if (parts.length) { out.push({ name, text: parts.join(", ") + " 계량됨" }); return; }
        const lots = row.filter((c) => c && String(c.lot || "").trim());
        if (lots.length) out.push({ name, text: `LOT ${String(lots[0].lot).trim()} 입력됨` });
        return;
      }
      const it = ((slot && slot.items) || [])[i];
      if (!it) return;
      const a = it.actual_amount;
      if (a !== "" && a !== null && a !== undefined) {
        out.push({ name, text: `${fmtG(a)} 계량됨` });
      } else if (String(it.material_lot || "").trim()) {
        out.push({ name, text: `LOT ${String(it.material_lot).trim()} 입력됨` });
      }
    });
    return out;
  }

  /** 새로 추가된 재료 이름 목록(순수). */
  function addedNames(currentMaterials, addedIdx) {
    const cur = Array.isArray(currentMaterials) ? currentMaterials : [];
    return (addedIdx || [])
      .map((j) => (cur[j] && cur[j].name) || `${j + 1}번 재료`)
      .filter(Boolean);
  }

  /** 기준 배합량·허용 편차·기준 자재 설정 변경 문구(순수). 저장 시점 스냅샷과 비교. */
  function baseChanges(slot, recipe) {
    const before = slot && slot.recipeMeta;
    if (!before || !recipe) return [];
    const now = recipeMetaOf(recipe);
    const out = [];
    const a = (before.default_totals || []).map(Number).join(" / ");
    const b = (now.default_totals || []).join(" / ");
    if (a !== b) {
      out.push(`기준 배합량이 바뀌었습니다: ${a || "없음"} → ${b || "없음"} (g)`);
    }
    const ta = before.tolerance_g === null || before.tolerance_g === undefined ? null : Number(before.tolerance_g);
    const tb = now.tolerance_g;
    if (String(ta) !== String(tb)) {
      out.push(`허용 편차가 바뀌었습니다: ±${ta === null ? "기본" : ta}g → ±${tb === null ? "기본" : tb}g`);
    }
    if (String(before.anchor_material_id || "") !== String(now.anchor_material_id || "")) {
      out.push("기준 자재(먼저 계량) 설정이 바뀌었습니다 — 총 배합량 산출 방식이 달라집니다.");
    }
    return out;
  }

  /**
   * 초안 ↔ 현재 레시피 차이 요약(순수). 복구 전(작성 중 배합 화면 배지)과
   * 복구 후(배합 화면 알림) 양쪽에서 같은 판정을 쓰기 위한 단일 진입점.
   *
   * recipeData: GET /blend/recipes/{id} 응답 {recipe, items}.
   * 반환: { legacy, align, added:[names], dropped:[{name,text}], baseNotes:[], changed }
   */
  function buildDiff(kind, slot, recipeData) {
    const data = recipeData || {};
    const current = materialIdentities(data.items || []);
    const align = alignRows(slot && slot.materials, current);
    if (align.legacy) {
      return {
        legacy: true, align,
        added: [], dropped: [], baseNotes: [],
        changed: false,
      };
    }
    const added = addedNames(current, align.addedIdx);
    const dropped = droppedEntries(kind, slot, align.removedIdx);
    const baseNotes = baseChanges(slot, data.recipe);
    return {
      legacy: false, align, added, dropped, baseNotes,
      changed: Boolean(added.length || dropped.length || baseNotes.length),
    };
  }

  /** 진행도 — 계량값이 들어간 칸 수 / 전체 칸 수. */
  function progressOf(kind, slot) {
    if (!slot) return { filled: 0, total: 0 };
    if (kind === "cont") {
      let filled = 0;
      let total = 0;
      (slot.cells || []).forEach((row) => {
        (row || []).forEach((c) => {
          total += 1;
          if (c && c.actual !== "" && c.actual !== null && c.actual !== undefined) filled += 1;
        });
      });
      return { filled, total };
    }
    const items = slot.items || [];
    let filled = 0;
    items.forEach((it) => {
      if (it && it.actual_amount !== "" && it.actual_amount !== null && it.actual_amount !== undefined) filled += 1;
    });
    return { filled, total: items.length };
  }

  /** "2026-08-03T09:12:33.000Z" → "2026-08-03 09:12" (저장 시각 표시용). */
  function savedAtText(slot) {
    const raw = slot && slot.savedAt;
    if (!raw) return "";
    return String(raw).slice(0, 16).replace("T", " ");
  }

  /**
   * 복구 후 화면에 남길 안내 HTML(순수 문자열). 조용한 폐기 금지 — 사라진 재료의
   * 계량값을 값까지 그대로 적는다. 변경이 없으면 빈 문자열.
   */
  function restoreNoticeHtml(diff) {
    if (!diff) return "";
    const blocks = [];
    if (diff.legacy) {
      blocks.push('<p class="blend-draft-notice-line">레시피 변경 여부를 확인할 수 없는 오래된 임시저장입니다 '
        + "— 계량값이 재료 순서대로 채워졌습니다. 각 줄의 재료와 값을 직접 확인하세요.</p>");
    }
    if (diff.dropped && diff.dropped.length) {
      const rows = diff.dropped
        .map((d) => `<li>${esc(d.name)} — ${esc(d.text)} · 레시피에서 삭제됨</li>`).join("");
      blocks.push('<p class="blend-draft-notice-line">아래 재료는 현재 레시피에 없어 계량값을 옮기지 못했습니다:</p>'
        + `<ul class="blend-draft-notice-list">${rows}</ul>`);
    }
    if (diff.added && diff.added.length) {
      blocks.push('<p class="blend-draft-notice-line">레시피에 새 재료가 추가되었습니다: '
        + esc(diff.added.join(", ")) + " — 아직 계량 전입니다.</p>");
    }
    if (diff.baseNotes && diff.baseNotes.length) {
      blocks.push(diff.baseNotes
        .map((t) => `<p class="blend-draft-notice-line">${esc(t)}</p>`).join(""));
    }
    return blocks.join("");
  }

  IRMS.blendDrafts = {
    MAX_SLOTS,
    TTL_MS,
    SCHEMA,
    RESUME_KEY,
    KINDS,
    KIND_ORDER,
    kindMeta,
    newId,
    readSlots,
    writeSlots,
    saveSlot,
    getSlot,
    removeSlot,
    listAll,
    setResume,
    takeResume,
    materialIdentities,
    recipeMetaOf,
    slotIsLegacy,
    alignRows,
    droppedEntries,
    addedNames,
    baseChanges,
    buildDiff,
    progressOf,
    savedAtText,
    restoreNoticeHtml,
    fmtG,
    esc,
  };
})();
