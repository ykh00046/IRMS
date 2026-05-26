/**
 * import-notifications module — 책임자가 import한 신규 레시피 알림 폴링.
 * Split from static/js/work.js (split-work-js, 2026-05).
 *
 * Factory: IRMS.work.createImportNotifications(ctx)
 * Returns: { check, start }
 * ctx deps:
 *   - ctx.state.recipeImportNotice
 *   - ctx.onRefreshTable(): Promise<void>
 *   - ctx.onRefreshWeighingQueue(): Promise<void> | void
 *
 * Polling: 8s setInterval, visibility-aware (hidden 시 skip).
 * 이중 start 가드: state.recipeImportNotice.timerId.
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.work = NS.work || {};

  const IMPORT_POLL_INTERVAL_MS = 8000;

  NS.work.createImportNotifications = function (ctx) {
    const { state } = ctx;
    const notice = state.recipeImportNotice;

    function storeLastSeenRecipeImportId(nextId) {
      const numericId = Number(nextId || 0);
      if (!Number.isFinite(numericId) || numericId <= 0) {
        return;
      }
      notice.lastSeenId = numericId;
      window.localStorage.setItem("irms_last_recipe_import_id", String(numericId));
    }

    async function check(options = {}) {
      if (notice.checking) {
        return;
      }

      const silent = Boolean(options.silent);
      notice.checking = true;

      try {
        const payload = await IRMS.getRecipeImportNotifications({
          afterId: notice.lastSeenId,
          limit: 20,
        });
        const items = payload.items || [];

        if (!items.length) {
          if (!notice.initialized && payload.latestId > notice.lastSeenId) {
            storeLastSeenRecipeImportId(payload.latestId);
          }
          notice.initialized = true;
          return;
        }

        const latestId = Number(
          items[items.length - 1]?.id || payload.latestId || notice.lastSeenId
        );

        if (!notice.initialized && notice.lastSeenId === 0) {
          storeLastSeenRecipeImportId(latestId);
          notice.initialized = true;
          return;
        }

        storeLastSeenRecipeImportId(latestId);
        notice.initialized = true;

        const importedRecipeCount = items.reduce((sum, item) => {
          const createdCount = Number(item.details?.created_count || 0);
          return sum + (Number.isFinite(createdCount) ? createdCount : 0);
        }, 0);

        const actorNames = Array.from(
          new Set(
            items
              .map((item) => item.actorDisplayName || item.actorUsername || "")
              .filter(Boolean)
          )
        );

        if (!silent) {
          const visibleCount = importedRecipeCount > 0 ? importedRecipeCount : items.length;
          const suffix = actorNames.length ? ` (${actorNames.slice(0, 2).join(", ")})` : "";
          const recipeLabel = visibleCount === 1 ? "recipe" : "recipes";
          IRMS.notify(`New ${recipeLabel} imported: ${visibleCount}${suffix}`, "info");
        }

        if (typeof ctx.onRefreshTable === "function") {
          await ctx.onRefreshTable();
        }
        if (typeof ctx.onRefreshWeighingQueue === "function") {
          await ctx.onRefreshWeighingQueue();
        }
      } catch (error) {
        if (!silent) {
          IRMS.notify(`레시피 알림 동기화 실패: ${error.message}`, "error");
        }
      } finally {
        notice.checking = false;
      }
    }

    function start() {
      // 이중 start 가드: 기존 timer 제거 후 재등록 (work.js L289-292 동작 보존)
      if (notice.timerId) {
        window.clearInterval(notice.timerId);
      }
      notice.timerId = window.setInterval(() => {
        if (document.visibilityState === "hidden") {
          return;
        }
        check();
      }, IMPORT_POLL_INTERVAL_MS);
    }

    return { check, start };
  };
})();
