/**
 * api-materials.js — 자재 목록 API 래퍼.
 *
 * (구) api-materials-weighing.js 에서 개명 — 계량(weighing) API 가 /blend 전환으로
 * 제거되면서(2026-07-07) getMaterials 만 남음.
 *
 * Exports (window.IRMS.*): getMaterials
 * Dependencies: core.js, mappers.js.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;
  const { mapMaterial } = IRMS._mappers;

  async function getMaterials() {
    const payload = await request("/materials");
    return (payload.items || []).map(mapMaterial);
  }

  Object.assign(IRMS, {
    getMaterials,
  });
})();
