/**
 * api-chat.js — Chat room and message endpoints.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   listChatRooms, getChatMessages, postChatMessage, clearChatMessages
 *
 * Side effects: none.
 * Dependencies: core.js, mappers.js.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;
  const { mapChatRoom, mapChatMessage } = IRMS._mappers;

  async function listChatRooms() {
    const payload = await request("/chat/rooms");
    return {
      items: (payload.items || []).map(mapChatRoom),
      total: Number(payload.total || 0),
    };
  }

  async function getChatMessages(filters) {
    const payload = await request("/chat/messages", {
      query: {
        room_key: filters?.roomKey,
        limit: filters?.limit,
        after_id: filters?.afterId,
      },
    });
    return {
      room: payload.room ? mapChatRoom(payload.room) : null,
      items: (payload.items || []).map(mapChatMessage),
      total: Number(payload.total || 0),
      latestId: Number(payload.latest_id || 0),
    };
  }

  async function postChatMessage(message) {
    const payload = await request("/chat/messages", {
      method: "POST",
      body: {
        room_key: message.roomKey,
        message_text: message.messageText,
        stage: message.stage || null,
      },
    });
    return {
      room: payload.room ? mapChatRoom(payload.room) : null,
      message: payload.message ? mapChatMessage(payload.message) : null,
    };
  }

  async function clearChatMessages() {
    return request("/chat/messages", { method: "DELETE" });
  }

  Object.assign(IRMS, {
    listChatRooms,
    getChatMessages,
    postChatMessage,
    clearChatMessages,
  });
})();
