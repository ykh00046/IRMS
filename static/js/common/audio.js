/**
 * audio.js — chat notification chime + Korean TTS queue.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   speakText
 *
 * Side effects (executed on script parse):
 *   document.addEventListener("click", resumeAudioCtx)
 *   document.addEventListener("keydown", resumeAudioCtx)
 *   (browser autoplay policy: AudioContext must resume on user gesture)
 *
 * Dependencies: none (uses browser-native AudioContext + speechSynthesis).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};

  var notifSoundCtx = null;

  // Resume AudioContext on first user interaction (browser autoplay policy)
  function resumeAudioCtx() {
    if (notifSoundCtx && notifSoundCtx.state === "suspended") notifSoundCtx.resume();
    document.removeEventListener("click", resumeAudioCtx);
    document.removeEventListener("keydown", resumeAudioCtx);
  }
  document.addEventListener("click", resumeAudioCtx);
  document.addEventListener("keydown", resumeAudioCtx);

  var speechQueue = [];
  var speechActive = false;

  function speakNextQueuedText() {
    if (speechActive || !speechQueue.length || !window.speechSynthesis) return;
    var cleaned = speechQueue.shift();
    speechActive = true;
    var utterance = new SpeechSynthesisUtterance(cleaned);
    var finish = function () {
      speechActive = false;
      speakNextQueuedText();
    };
    utterance.lang = "ko-KR";
    utterance.rate = 1.1;
    utterance.volume = 0.9;
    utterance.onend = finish;
    utterance.onerror = finish;
    try {
      window.speechSynthesis.speak(utterance);
    } catch (_) {
      finish();
    }
  }

  function speakText(text) {
    if (!window.speechSynthesis || !text) return;
    var cleaned = String(text)
      .replace(/[()（）\[\]]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!cleaned) return;
    speechQueue.push(cleaned);
    speakNextQueuedText();
  }

  IRMS.speakText = speakText;
})();
