/**
 * audio.js — chat notification chime + Korean TTS queue.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   playChatSound, speakText
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

  function playChatSound() {
    try {
      if (!notifSoundCtx) notifSoundCtx = new (window.AudioContext || window.webkitAudioContext)();
      var ctx = notifSoundCtx;
      if (ctx.state === "suspended") { ctx.resume(); }
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.setValueAtTime(1047, ctx.currentTime + 0.08);
      gain.gain.setValueAtTime(0.18, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.3);
    } catch (_) { /* AudioContext unavailable */ }
  }

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

  IRMS.playChatSound = playChatSound;
  IRMS.speakText = speakText;
})();
