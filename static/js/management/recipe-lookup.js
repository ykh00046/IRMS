/**
 * recipe-lookup module — 클립보드 복사 헬퍼.
 *
 * 3단계 정리(2026-08-06): 속성 편집기는 recipe-history.js 행 펼침으로 이전,
 * 검색·칩·액션·비교 탭 진입구는 제거됐다. 이 모듈은 현황(.history-copy-btn)이
 * 쓰는 copyToClipboard 만 남는다 — 다른 화면이 IRMS.management.createRecipeLookup
 * 을 참조하므로 팩토리 자체는 유지하되 반환은 copyToClipboard 하나다.
 *
 * Factory: IRMS.management.createRecipeLookup(ctx)
 * Returns: { copyToClipboard }
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createRecipeLookup = function (_ctx) {
    function copyToClipboard(text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
      }
      // Fallback for non-HTTPS or older browsers
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      return Promise.resolve();
    }

    return { copyToClipboard };
  };
})();
