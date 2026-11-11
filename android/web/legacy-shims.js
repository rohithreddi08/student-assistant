/* ULTRON legacy shims — plain ES5, loaded after core-js and before the interface.
   They fill in browser APIs that old Android WebViews (Chrome 44+) lack, so the interface runs
   instead of failing. Every shim checks first and does nothing on modern engines. */
(function () {
  "use strict";
  var w = window, nav = navigator;

  // isSecureContext (Chrome 47): the app always loads from http://localhost, which is a secure origin
  if (!("isSecureContext" in w)) {
    try { w.isSecureContext = /^(localhost|127\.0\.0\.1)$/.test(location.hostname) || location.protocol === "https:"; } catch (e) {}
  }

  // navigator.mediaDevices.getUserMedia (Chrome 53) from the old prefixed callback API
  var legacyGUM = nav.getUserMedia || nav.webkitGetUserMedia;
  if (!nav.mediaDevices) { try { nav.mediaDevices = {}; } catch (e) {} }
  if (nav.mediaDevices && !nav.mediaDevices.getUserMedia && legacyGUM) {
    var simplify = function (c) {        // old engines reject the modern constraint syntax: keep only what they know
      var out = {};
      if (c.audio) out.audio = true;
      if (c.video) out.video = (c.video.facingMode ? { optional: [{ facingMode: c.video.facingMode }] } : true);
      return out;
    };
    nav.mediaDevices.getUserMedia = function (c) {
      return new Promise(function (ok, bad) {
        legacyGUM.call(nav, simplify(c || {}), ok, function () {
          legacyGUM.call(nav, { audio: !!(c && c.audio), video: !!(c && c.video) }, ok, bad);   // last try: plain true/false
        });
      });
    };
  }
  if (nav.mediaDevices && !nav.mediaDevices.enumerateDevices && w.MediaStreamTrack && MediaStreamTrack.getSources) {
    nav.mediaDevices.enumerateDevices = function () {
      return new Promise(function (ok) { MediaStreamTrack.getSources(function (s) {
        ok(s.map(function (d) { return { deviceId: d.id, kind: d.kind === "audio" ? "audioinput" : "videoinput", label: d.label }; }));
      }); });
    };
  }

  // <video>.srcObject (Chrome 52): fall back to a blob URL
  if (w.HTMLMediaElement && !("srcObject" in HTMLMediaElement.prototype)) {
    Object.defineProperty(HTMLMediaElement.prototype, "srcObject", {
      configurable: true,
      get: function () { return this._ultronStream || null; },
      set: function (s) {
        this._ultronStream = s;
        if (this._ultronUrl) { try { URL.revokeObjectURL(this._ultronUrl); } catch (e) {} this._ultronUrl = null; }
        if (s) { this._ultronUrl = URL.createObjectURL(s); this.src = this._ultronUrl; } else { this.removeAttribute("src"); try { this.load(); } catch (e) {} }
      }
    });
  }
  // video.play() returned nothing before Chrome 50; the interface awaits it
  if (w.HTMLMediaElement) {
    var play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      var r = play.apply(this, arguments);
      return r && typeof r.then === "function" ? r : Promise.resolve();
    };
  }
  // MediaStream.getTracks (Chrome 45)
  var MS = w.MediaStream || w.webkitMediaStream;
  if (MS && MS.prototype && !MS.prototype.getTracks) {
    MS.prototype.getTracks = function () { return (this.getAudioTracks ? this.getAudioTracks() : []).concat(this.getVideoTracks ? this.getVideoTracks() : []); };
  }

  // AudioContext (prefixed before Chrome 35)
  if (!w.AudioContext && w.webkitAudioContext) w.AudioContext = w.webkitAudioContext;

  // Element.closest / matches (very old engines)
  var EP = w.Element && Element.prototype;
  if (EP && !EP.matches) EP.matches = EP.webkitMatchesSelector;
  if (EP && !EP.closest) EP.closest = function (sel) { var n = this; while (n && n.nodeType === 1) { if (n.matches(sel)) return n; n = n.parentNode; } return null; };

  // KeyboardEvent.key (Chrome 51): Enter / Backspace / digits are what the interface reads
  if (w.KeyboardEvent && !("key" in KeyboardEvent.prototype)) {
    var NAMES = { 8: "Backspace", 9: "Tab", 13: "Enter", 27: "Escape", 32: " ", 37: "ArrowLeft", 38: "ArrowUp", 39: "ArrowRight", 40: "ArrowDown", 46: "Delete" };
    Object.defineProperty(KeyboardEvent.prototype, "key", { configurable: true, get: function () {
      var c = this.keyCode || this.which;
      if (NAMES[c]) return NAMES[c];
      if (c >= 96 && c <= 105) return String(c - 96);                 // numpad digits
      if (this.type === "keypress" && this.charCode) return String.fromCharCode(this.charCode);
      if ((c >= 48 && c <= 57) || (c >= 65 && c <= 90)) { var ch = String.fromCharCode(c); return this.shiftKey ? ch : ch.toLowerCase(); }
      return "Unidentified";
    } });
  }

  // performance.now
  if (!w.performance) w.performance = {};
  if (!w.performance.now) { var t0 = Date.now(); w.performance.now = function () { return Date.now() - t0; }; }

  // Never show a blank screen: if a part of the interface throws on an old engine, say so and keep going
  var shown = 0;
  function report(msg) {
    try { console.error("[ULTRON legacy]", msg); } catch (e) {}
    if (shown++ > 2 || !document.body) return;
    var d = document.createElement("div");
    d.setAttribute("style", "position:fixed;left:8px;right:8px;top:8px;z-index:99999;background:#1a0408;color:#ffb3bb;border:1px solid #ff2b45;" +
      "font:13px sans-serif;padding:8px 10px;border-radius:3px");
    d.textContent = "ULTRON: part of the interface isn't supported on this phone (" + String(msg).slice(0, 140) + "). Everything else still works.";
    d.onclick = function () { d.parentNode && d.parentNode.removeChild(d); };
    document.body.appendChild(d);
    setTimeout(function () { d.parentNode && d.parentNode.removeChild(d); }, 9000);
  }
  w.addEventListener("error", function (e) { if (e && e.message) report(e.message); });
  w.addEventListener("unhandledrejection", function (e) {
    var r = e && e.reason; var m = r && (r.message || r.name) || String(r);
    if (/NotAllowed|Permission|NotFound|Abort|network/i.test(m)) return;   // normal runtime outcomes, handled by the UI
    report(m);
  });
  w.ULTRON_LEGACY_BUILD = true;
})();
