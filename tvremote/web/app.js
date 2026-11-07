"use strict";
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  let ws, state = null, retry = 500, playing = false;

  // ---------- connection ----------
  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { retry = 500; };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "state") render(m);
      else if (m.type === "error") toast(m.msg);
    };
    ws.onclose = () => {
      setStatus("off", "Remote server unreachable…");
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 5000);
    };
  }
  function send(msg) {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg));
    else toast("Not connected to the remote server");
  }
  const buzz = (ms = 8) => { try { navigator.vibrate && navigator.vibrate(ms); } catch (_) {} };

  // ---------- rendering ----------
  function setStatus(cls, text) {
    const el = $("#status");
    el.className = "status " + cls;
    $("#statusText").textContent = text;
  }

  const initial = (t) => (t || "?").trim().charAt(0).toUpperCase();
  function iconEl(app) {
    const img = new Image();
    img.loading = "lazy";
    img.alt = "";
    img.src = `/icon/${encodeURIComponent(app.id)}`;
    img.onerror = () => {
      const ph = document.createElement("div");
      ph.className = "ph";
      ph.textContent = initial(app.title);
      if (app.bg) ph.style.background = app.bg;
      img.replaceWith(ph);
    };
    return img;
  }

  let lastAppsKey = "", lastFavKey = "";
  function render(s) {
    const prev = state;
    state = s;
    const label = {
      connected: s.on ? (s.currentTitle || "TV on") : "TV in standby",
      pairing: "Pairing: press Accept on the TV",
      off: "TV is off",
      connecting: "Connecting to TV…",
      error: s.detail || "Error",
    }[s.status] || s.status;
    setStatus(s.status === "connected" && !s.on ? "off" : s.status, label);

    $("#volNum").textContent = s.volume == null ? "–" : s.volume;
    $("#muteIcon").innerHTML = `<use href="#i-${s.muted ? "mute" : "vol"}"/>`;
    $(".rk.mid").classList.toggle("muted", !!s.muted);

    $("#fastToggle").checked = s.fastUi;
    $("#powerToggle").checked = s.launchOnPower;
    $("#homeLabel").textContent = s.fastUi ? "Fast home" : "Home";
    $("#fastHint").textContent = s.fastUi
      ? "Home opens the fast launcher. The LG menu button still opens the stock home."
      : "Off. Home works exactly like the original LG remote.";
    $("#launcherState").textContent = s.launcherInstalled
      ? "The fast launcher app is installed on the TV."
      : "The fast launcher app is not installed yet, so it opens in the TV's web browser. See the README to install it.";

    const appsKey = s.apps.map((a) => a.id).join("|") + "#" + s.favorites.join("|");
    if (appsKey !== lastAppsKey) { lastAppsKey = appsKey; renderApps(); }
    const favKey = s.favorites.join("|") + "#" + s.apps.length;
    if (favKey !== lastFavKey) { lastFavKey = favKey; renderFavs(); }
    $$(".fav,.app").forEach((el) => el.classList.toggle("current", el.dataset.id === s.current));
    renderInputs();

    // keyboard bar: TV focused a text field
    if (s.keyboard && !(prev && prev.keyboard)) openKbdBar();
    if (!s.keyboard && prev && prev.keyboard) closeKbdBar();
  }

  function renderFavs() {
    const strip = $("#favStrip");
    strip.innerHTML = "";
    const byId = Object.fromEntries(state.apps.map((a) => [a.id, a]));
    const favs = state.favorites.map((id) => byId[id]).filter(Boolean);
    if (!favs.length) {
      strip.innerHTML = '<span class="empty">Pin your apps from the Apps tab (☆) and they show up here.</span>';
      return;
    }
    for (const app of favs) {
      const b = document.createElement("button");
      b.className = "fav";
      b.dataset.id = app.id;
      b.append(iconEl(app));
      const t = document.createElement("small");
      t.textContent = app.title;
      b.append(t);
      b.addEventListener("click", () => { buzz(); send({ a: "launch", id: app.id }); });
      strip.append(b);
    }
  }

  // Same split as the TV launcher: main apps up top, the rest folded away.
  const MAIN_APPS = ["netflix", "amazon", "hotstar", "youtube.leanback.v4", "com.zee5.app", "com.sonyliv2.tvapp",
    "com.sun.sunnxt", "aha", "etvwin", "com.apple.appletv", "spotify-beehive",
    "com.webos.app.photovideo", "com.webos.app.music", "airplay", "com.webos.app.discovery"];
  let showOthers = false;

  function appTile(app, favs) {
    const tile = document.createElement("button");
    tile.className = "app";
    tile.dataset.id = app.id;
    tile.append(iconEl(app));
    const t = document.createElement("span");
    t.textContent = app.title;
    tile.append(t);
    const star = document.createElement("div");
    star.className = "star" + (favs.has(app.id) ? " on" : "");
    star.setAttribute("role", "button");
    star.setAttribute("aria-label", "Pin");
    star.innerHTML = '<svg><use href="#i-star"/></svg>';
    star.addEventListener("click", (e) => {
      e.stopPropagation();
      buzz();
      send({ a: "fav", id: app.id, on: !favs.has(app.id) });
    });
    tile.append(star);
    tile.addEventListener("click", () => {
      buzz();
      send({ a: "launch", id: app.id });
      toast(`Opening ${app.title}`);
    });
    return tile;
  }

  function renderApps() {
    const q = $("#appSearch").value.trim().toLowerCase();
    const grid = $("#appGrid");
    grid.innerHTML = "";
    if (!state.apps.length) {
      grid.innerHTML = '<p class="hint">Apps appear here once the TV is on and connected.</p>';
      return;
    }
    const favs = new Set(state.favorites);
    const match = (a) => !q || a.title.toLowerCase().includes(q);
    const isMain = (a) => MAIN_APPS.includes(a.id) || favs.has(a.id);
    const main = state.apps.filter((a) => isMain(a) && match(a))
      .sort((a, b) => MAIN_APPS.indexOf(a.id) - MAIN_APPS.indexOf(b.id));
    const others = state.apps.filter((a) => !isMain(a) && match(a));
    main.forEach((a) => grid.append(appTile(a, favs)));
    if (others.length) {
      const open = showOthers || !!q;
      const head = document.createElement("button");
      head.className = "others-head";
      head.textContent = (open ? "Hide" : "Show") + ` other apps (${others.length})`;
      head.addEventListener("click", () => { showOthers = !showOthers; renderApps(); });
      grid.append(head);
      if (open) others.forEach((a) => grid.append(appTile(a, favs)));
    }
  }
  $("#appSearch").addEventListener("input", renderApps);

  function renderInputs() {
    const box = $("#inputs");
    const key = JSON.stringify(state.inputs) + state.current;
    if (box.dataset.key === key) return;
    box.dataset.key = key;
    box.innerHTML = "";
    for (const inp of state.inputs) {
      const b = document.createElement("button");
      b.className = "chip" + (inp.app === state.current ? " current" : "") + (inp.connected ? "" : " dim");
      b.textContent = inp.label;
      b.addEventListener("click", () => { buzz(); send({ a: "input", id: inp.id }); });
      box.append(b);
    }
    if (!state.inputs.length) box.innerHTML = '<span class="hint">No inputs reported yet.</span>';
  }

  // ---------- buttons ----------
  function fire(el) {
    buzz();
    if (el.dataset.btn) return send({ a: "btn", name: el.dataset.btn });
    switch (el.dataset.act) {
      case "power": return send({ a: "power" });
      case "home": return send({ a: "home" });
      case "original": toast("Opening the original LG home"); return send({ a: "original" });
      case "mute": return send({ a: "mute", on: !(state && state.muted) });
      case "playpause":
        playing = !playing;
        $("#ppIcon").innerHTML = `<use href="#i-${playing ? "pause" : "play"}"/>`;
        return send({ a: "btn", name: playing ? "PLAY" : "PAUSE" });
      case "enter": return send({ a: "enter" });
      case "clearType": return typeClear($("#typeBox"));
      case "kbdClose": return closeKbdBar();
    }
  }

  // Remote-tab buttons fire on touch-down (no click delay) and repeat while held.
  // Buttons in scrolling tabs use click so a scroll never presses anything.
  $$("[data-btn],[data-act]").forEach((el) => {
    const instant = el.closest("#v-remote") || el.closest(".top");
    if (!instant) { el.addEventListener("click", () => fire(el)); return; }
    let t1, t2;
    const stop = () => { clearTimeout(t1); clearInterval(t2); el.classList.remove("pressed"); };
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      el.classList.add("pressed");
      fire(el);
      if (el.hasAttribute("data-repeat")) {
        t1 = setTimeout(() => { t2 = setInterval(() => fire(el), 110); }, 380);
      }
    });
    ["pointerup", "pointercancel", "pointerleave"].forEach((ev) => el.addEventListener(ev, stop));
    el.addEventListener("contextmenu", (e) => e.preventDefault());
  });

  $("#fastToggle").addEventListener("change", (e) => {
    buzz();
    send({ a: "set_fast_ui", on: e.target.checked });
    toast(e.target.checked ? "Fast UI on" : "Fast UI off. Original LG behaviour restored");
  });

  $("#powerToggle").addEventListener("change", (e) => {
    buzz();
    send({ a: "set_launch_on_power", on: e.target.checked });
  });

  // ---------- tabs & modes ----------
  function showView(id) {
    $$(".tab").forEach((x) => x.classList.toggle("active", x.dataset.view === id));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === id));
  }
  $$(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
  // deep links: /#apps, /#type, /#more
  if (location.hash && $("#v-" + location.hash.slice(1))) showView("v-" + location.hash.slice(1));

  function setMode(mode) {
    $$(".seg").forEach((s) => s.classList.toggle("active", s.dataset.mode === mode));
    $("#dpad").classList.toggle("hidden", mode !== "dpad");
    $("#swipe").classList.toggle("hidden", mode !== "swipe");
    $("#touchpad").classList.toggle("hidden", mode !== "pad");
    try { localStorage.setItem("mode", mode); } catch (_) {}
  }
  $$(".seg").forEach((s) => s.addEventListener("click", () => setMode(s.dataset.mode)));
  try { setMode(localStorage.getItem("mode") || "dpad"); } catch (_) { setMode("dpad"); }

  function ripple(surface, x, y) {
    const r = surface.getBoundingClientRect();
    const d = document.createElement("div");
    d.className = "ripple";
    d.style.left = x - r.left + "px";
    d.style.top = y - r.top + "px";
    surface.append(d);
    setTimeout(() => d.remove(), 400);
  }

  // ---------- swipe surface: flick = arrow key, tap = OK ----------
  (() => {
    const el = $("#swipe");
    const STEP = 42;
    let ox, oy, sx, sy, t0, moved, lastDir, holdT, holdI;
    const clearHold = () => { clearTimeout(holdT); clearInterval(holdI); };
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId);
      ox = sx = e.clientX; oy = sy = e.clientY; t0 = Date.now(); moved = false; lastDir = null;
    });
    el.addEventListener("pointermove", (e) => {
      if (t0 == null) return;
      const dx = e.clientX - ox, dy = e.clientY - oy;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < STEP) return;
      const dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "RIGHT" : "LEFT") : (dy > 0 ? "DOWN" : "UP");
      ox = e.clientX; oy = e.clientY; moved = true; lastDir = dir;
      buzz(6);
      send({ a: "btn", name: dir });
      // finger stays down after a swipe: keep moving in that direction
      clearHold();
      holdT = setTimeout(() => { holdI = setInterval(() => send({ a: "btn", name: lastDir }), 160); }, 420);
    });
    const end = (e) => {
      clearHold();
      if (t0 == null) return;
      const dist = Math.hypot(e.clientX - sx, e.clientY - sy);
      if (!moved && dist < 12 && Date.now() - t0 < 350) {
        buzz(12);
        ripple(el, e.clientX, e.clientY);
        send({ a: "btn", name: "ENTER" });
      }
      t0 = null;
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", () => { clearHold(); t0 = null; });
  })();

  // ---------- touchpad: drag = pointer, tap = click, 2 fingers = scroll ----------
  (() => {
    const el = $("#touchpad");
    const pts = new Map();
    let accX = 0, accY = 0, scrollY = 0, raf = 0, tapStart = 0, tapMoved = 0, multi = false;
    const flush = () => {
      raf = 0;
      if (accX || accY) { send({ a: "move", dx: Math.round(accX), dy: Math.round(accY) }); accX = accY = 0; }
      if (Math.abs(scrollY) >= 1) { send({ a: "scroll", dx: 0, dy: Math.round(scrollY) }); scrollY = 0; }
    };
    const queue = () => { if (!raf) raf = requestAnimationFrame(flush); };
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId);
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 1) { tapStart = Date.now(); tapMoved = 0; multi = false; }
      else multi = true;
    });
    el.addEventListener("pointermove", (e) => {
      const p = pts.get(e.pointerId);
      if (!p) return;
      const dx = e.clientX - p.x, dy = e.clientY - p.y;
      p.x = e.clientX; p.y = e.clientY;
      tapMoved += Math.abs(dx) + Math.abs(dy);
      if (pts.size >= 2) { scrollY += -dy / 2; queue(); return; }
      // mild acceleration: slow drags are precise, fast flicks cross the screen
      const speed = Math.hypot(dx, dy);
      const gain = 1.4 + Math.min(speed / 12, 2.2);
      accX += dx * gain; accY += dy * gain;
      queue();
    });
    const up = (e) => {
      if (!pts.has(e.pointerId)) return;
      pts.delete(e.pointerId);
      if (pts.size === 0 && !multi && tapMoved < 10 && Date.now() - tapStart < 280) {
        buzz(12);
        ripple(el, e.clientX, e.clientY);
        send({ a: "click" });
      }
    };
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", (e) => pts.delete(e.pointerId));
  })();

  // ---------- typing with the phone keyboard ----------
  // Send only the difference since the last input event, so autocorrect and
  // word suggestions that rewrite the end of the text still land correctly.
  function bindTyping(field, onEnter) {
    field._last = "";
    field.addEventListener("input", () => {
      const now = field.value, before = field._last;
      let i = 0;
      while (i < before.length && i < now.length && before[i] === now[i]) i++;
      const del = before.length - i, add = now.slice(i);
      if (del > 0) send({ a: "backspace", n: del });
      if (add) send({ a: "text", text: add });
      field._last = now;
    });
    field.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); send({ a: "enter" }); onEnter && onEnter(); }
      // backspace on an empty field still deletes on the TV
      if (e.key === "Backspace" && field.value === "") send({ a: "backspace", n: 1 });
    });
  }
  function typeClear(field) {
    if (field._last) send({ a: "backspace", n: field._last.length });
    field.value = ""; field._last = "";
  }
  bindTyping($("#typeBox"));

  // ---------- voice input ----------
  // Browsers only allow the mic on secure pages: https, or this phone's own
  // http://127.0.0.1 (the phone running the server). Elsewhere, fall back to
  // the keyboard's own mic key (Gboard / iPhone dictation), which works anywhere.
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, recField = null;
  function stopVoice() {
    if (rec) { try { rec.stop(); } catch (_) {} }
  }
  function startVoice(field, btn) {
    if (rec) { stopVoice(); return; }
    if (!SR || !window.isSecureContext) {
      field.focus();
      toast(SR ? "Tap the mic key on your keyboard (this address can't use the browser mic)"
               : "Tap the mic key on your keyboard to speak");
      return;
    }
    rec = new SR();
    recField = field;
    rec.lang = navigator.language || "en-IN";
    rec.interimResults = true;
    rec.continuous = false;
    const base = field.value ? field.value.replace(/\s*$/, " ") : "";
    btn.classList.add("listening");
    buzz(15);
    rec.onresult = (e) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      field.value = base + text;
      field.dispatchEvent(new Event("input"));     // sends only the difference to the TV
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") toast("Allow the microphone for this page to use voice");
      else if (e.error !== "aborted" && e.error !== "no-speech") toast("Voice input stopped: " + e.error);
    };
    rec.onend = () => {
      btn.classList.remove("listening");
      rec = null; recField = null;
    };
    try { rec.start(); } catch (_) { btn.classList.remove("listening"); rec = null; }
  }
  $$(".mic").forEach((btn) => btn.addEventListener("click", () => startVoice($("#" + btn.dataset.mic), btn)));

  const bar = $("#kbdBar"), kin = $("#kbdInput");
  bindTyping(kin, () => closeKbdBar());
  function openKbdBar() {
    kin.value = ""; kin._last = "";
    bar.hidden = false;
    buzz(20);
    // Browsers only open the keyboard from a tap. Try anyway; it works on some.
    try { kin.focus({ preventScroll: true }); } catch (_) {}
  }
  function closeKbdBar() { if (recField === kin) stopVoice(); bar.hidden = true; kin.blur(); }

  // ---------- toast ----------
  let toastT;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastT);
    toastT = setTimeout(() => t.classList.remove("show"), 1800);
  }

  connect();
})();
