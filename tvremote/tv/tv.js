/* Quick Launch for webOS 4.x (Chromium 53). ES5 only.
 * Three columns: app roll (loops) | Continue | Inputs & System.
 * Left/Right move between columns, Up/Down within, OK opens,
 * hold OK on an app (roll or Search) to add/remove it from the roll. */
(function () {
  "use strict";

  var DEFAULT_ROLL = ["netflix", "amazon", "hotstar", "youtube.leanback.v4", "com.zee5.app", "com.sonyliv2.tvapp",
    "com.sun.sunnxt", "aha", "etvwin", "com.apple.appletv", "spotify-beehive",
    "com.webos.app.photovideo", "com.webos.app.music", "airplay", "com.webos.app.discovery"];
  var MEDIA = ["com.webos.app.photovideo", "com.webos.app.music", "airplay", "com.webos.app.discovery"];
  var MAIN_INPUTS = /^HDMI/;
  var INPUT_APP = /^com\.webos\.app\.(hdmi|externalinput)/;
  var SVG = {
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    settings: '<svg viewBox="0 0 24 24"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
    lg: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8"/></svg>'
  };
  var DAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
  var MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

  // ---------- storage ----------
  function load(k, json) { try { var v = localStorage.getItem(k); return json ? JSON.parse(v) : v; } catch (e) { return null; } }
  function save(k, v) { try { localStorage.setItem(k, typeof v === "string" ? v : JSON.stringify(v)); } catch (e) {} }
  function $(id) { return document.getElementById(id); }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (ch) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]; }); }
  function now() { return Math.floor(Date.now() / 1000); }

  // ---------- data ----------
  var hosted = /^https?:$/.test(location.protocol);
  var server = hosted ? location.origin : (load("ql-server") || window.TV_SERVER || "");
  var baked = window.BAKED || { apps: [], inputs: [], favorites: [] };
  var cache = load("ql-data", true) || {};
  var inputs = (cache.inputs && cache.inputs.length) ? cache.inputs : baked.inputs;
  var apps = baked.apps.length ? baked.apps : (cache.apps || []);

  function iconUrl(a) {
    if (!a.icon) return null;
    if (a.icon.indexOf("icons/") === 0 || /^https?:/.test(a.icon)) return a.icon;
    return server ? server + "/icon/" + encodeURIComponent(a.id) : null;
  }
  var byId = {};
  apps.forEach(function (a) { byId[a.id] = { id: a.id, title: a.title, kind: "app", icon: iconUrl(a) }; });
  var inputItems = inputs.map(function (i) {
    var port = (i.id || "").replace("_", " ");
    var it = { id: i.app, title: i.label, kind: "input", port: port, short: port.replace("HDMI ", "H").replace("AV ", "AV").replace("COMP ", "C") };
    byId[i.app] = it;
    return it;
  });
  var SIDE = inputItems.filter(function (i) { return MAIN_INPUTS.test(i.port); }).concat([
    { id: "@search", title: "Search", kind: "search", svg: SVG.search, sub: "ALL APPS" },
    { id: "@settings", launch: "com.palm.app.settings", title: "Settings", kind: "settings", svg: SVG.settings, sub: "LG SETTINGS" },
    { id: "@lg", title: "LG home", kind: "lg", svg: SVG.lg, sub: "ORIGINAL UI" }]);
  var everything = Object.keys(byId).map(function (k) { return byId[k]; })
    .filter(function (it) { return it.kind === "input" || !INPUT_APP.test(it.id); })
    .concat([{ id: "@settings", launch: "com.palm.app.settings", title: "Settings", kind: "settings", svg: SVG.settings }])
    .sort(function (a, b) { return a.title.toLowerCase() < b.title.toLowerCase() ? -1 : 1; });

  // roll contents: saved on the TV, so it works with no phone/PC around
  var rollIds = load("ql-roll", true);
  if (!rollIds) {
    var favs = (cache.favorites || []).map(function (f) { return f.id || f; });
    rollIds = favs.concat(DEFAULT_ROLL.filter(function (id) { return favs.indexOf(id) < 0; }));
  }
  rollIds = rollIds.filter(function (id) { return byId[id] && byId[id].kind === "app"; });

  // recently used: server history (all remotes) merged with our own launches
  var recentLocal = load("ql-recent", true) || [];
  var recentServer = cache.recent || [], serverSkew = 0;

  // ---------- actions ----------
  function luna(uri, params) {
    if (typeof PalmServiceBridge === "undefined") return false;
    var b = new PalmServiceBridge();
    b.onservicecallback = function () {};
    b.call(uri, JSON.stringify(params || {}));
    return true;
  }
  function serverAction(msg) {
    if (!server) return;
    var x = new XMLHttpRequest();
    x.open("POST", server + "/api/action");
    x.setRequestHeader("Content-Type", "application/json");
    x.send(JSON.stringify(msg));
  }
  function activate(item) {
    if (!item) return;
    if (item.kind === "search") return openLib();
    if (item.kind === "lg") {
      serverAction({ a: "original" });
      return setTimeout(function () { try { window.close(); } catch (e) {} }, 250);
    }
    var id = item.launch || item.id;
    if (item.kind === "app" || item.kind === "input") {
      recentLocal = [{ id: item.id, at: now() }].concat(recentLocal.filter(function (r) { return r.id !== item.id; })).slice(0, 8);
      save("ql-recent", recentLocal);
    }
    if (!luna("luna://com.webos.applicationManager/launch", { id: id })) serverAction({ a: "launch", id: id });
  }

  var toastTimer = null;
  function toast(msg) {
    $("toast").textContent = msg;
    $("toast").className = "show";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { $("toast").className = ""; }, 1800);
  }

  function inRoll(id) { return rollIds.indexOf(id) >= 0; }
  function setPinned(id, on) {
    var item = byId[id];
    if (!item || item.kind !== "app") { toast("Inputs live in the right-hand panel"); return; }
    if (on && !inRoll(id)) rollIds.push(id);
    if (!on) rollIds = rollIds.filter(function (x) { return x !== id; });
    save("ql-roll", rollIds);
    serverAction({ a: "fav", id: id, on: on });   // keeps the phone's pins in step
    buildRoll();
    toast(on ? "Added " + item.title + " to the main menu" : "Removed " + item.title + " · find it in Search");
  }

  // ---------- roll (loops) ----------
  var rollEls = [], rp = 0, prevD = [];
  var CENTER = 504, ITEM_H = 100, GAP = 64, SCALE = 1.42;
  function buildRoll() {
    var html = "";
    rollIds.forEach(function (id) {
      var it = byId[id];
      html += '<div class="wi">' + (it.icon ? '<img src="' + it.icon + '" alt="">' : "") + "</div>";
    });
    $("roll").innerHTML = html;
    rollEls = Array.prototype.slice.call($("roll").children);
    prevD = [];
    rp = Math.max(0, Math.min(rp, rollIds.length - 1));
    placeRoll(true);
  }
  function placeRoll(instant) {
    var n = rollEls.length;
    for (var i = 0; i < n; i++) {
      var d = ((i - rp) % n + n) % n;
      if (d > n / 2) d -= n;
      var ad = Math.abs(d), y = CENTER + d * ITEM_H + (d > 0 ? GAP : d < 0 ? -GAP : 0);
      var t = "translate3d(0," + y + "px,0)" + (d === 0 ? " scale(" + SCALE + ")" : "");
      var jump = instant || (prevD[i] !== undefined && Math.abs(prevD[i] - d) > 1);   // wrapped round: no slide
      var cls = "wi" + (ad <= 3 ? " d" + ad : "") + (jump ? " jump" : "");
      if (rollEls[i].className !== cls) rollEls[i].className = cls;
      rollEls[i].style.webkitTransform = rollEls[i].style.transform = t;
      prevD[i] = d;
    }
    var it = byId[rollIds[rp]];
    $("rollName").innerHTML = it ? esc(it.title) + "<small>" + (MEDIA.indexOf(it.id) >= 0 ? "MEDIA" : "STREAMING") + "</small>" : "";
  }
  function rollMove(delta) {
    var n = rollEls.length;
    if (!n) return;
    rp = ((rp + delta) % n + n) % n;
    placeRoll(false);
  }

  // ---------- continue + recent (middle) ----------
  var midItems = [], mp = 0;
  function ago(at) {
    var s = Math.max(0, now() - serverSkew - at);
    if (s < 90) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }
  function mergedRecent() {
    var seen = {}, out = [];
    recentServer.concat(recentLocal).sort(function (a, b) { return b.at - a.at; }).forEach(function (r) {
      if (seen[r.id] || !byId[r.id]) return;
      seen[r.id] = true;
      out.push(r);
    });
    return out;
  }
  function renderMid() {
    var rec = mergedRecent();
    midItems = rec.slice(0, 4);
    if (!midItems.length) {
      $("contTitle").textContent = "Nothing yet";
      $("contSub").textContent = "What you watch shows up here";
      $("contBtn").style.visibility = "hidden";
      $("recent").innerHTML = "";
      $("recentHead").style.visibility = "hidden";
      return;
    }
    var top = midItems[0], it = byId[top.id];
    $("contBtn").style.visibility = "visible";
    $("contTitle").textContent = it.title;
    $("contSub").textContent = (it.kind === "input" ? it.port + " · " : "") +
      (top.state === "paused" ? "Paused " : top.state === "playing" ? "Playing · " : "Last used ") + ago(top.at);
    var html = "";
    for (var i = 1; i < midItems.length; i++) {
      var r = byId[midItems[i].id];
      var ic = r.kind === "input" ? esc(r.short) : (r.icon ? '<img src="' + r.icon + '" alt="">' : "");
      html += '<div class="rc"><div class="ic">' + ic + '</div><div class="nm">' + esc(r.title) +
        '</div><div class="tm">' + ago(midItems[i].at).toUpperCase() + "</div></div>";
    }
    $("recent").innerHTML = html;
    $("recentHead").style.visibility = html ? "visible" : "hidden";
    if (zone === "mid") focusMid(mp);
  }
  function midEls() { return [$("cont")].concat(Array.prototype.slice.call($("recent").children)); }

  // ---------- side ----------
  var sideEls = [];
  SIDE.forEach(function (it) {
    var el = document.createElement("div");
    el.className = "si";
    el.innerHTML = '<div class="tb">' + (it.kind === "input" ? esc(it.short) : it.svg) + '</div><div class="nm">' +
      esc(it.title) + '</div><div class="sb">' + esc(it.kind === "input" ? it.port : it.sub) + "</div>";
    $(it.kind === "input" ? "sideInputs" : "sideSystem").appendChild(el);
    sideEls.push(el);
  });

  // ---------- focus ----------
  var zone = "roll", sp = 0, focusedEl = null;
  var HINTS = {
    roll: "<span><b>OK</b>OPEN</span><span><b>HOLD OK</b>REMOVE</span><span><b>&#9654;</b>CONTINUE</span>",
    mid: "<span><b>OK</b>RESUME</span><span><b>&#9664;</b>APPS</span><span><b>&#9654;</b>INPUTS</span>",
    side: "<span><b>OK</b>OPEN</span><span><b>&#9664;</b>BACK</span>"
  };
  function setFocus(el) {
    if (focusedEl === el) return;
    if (focusedEl) focusedEl.classList.remove("focus");
    focusedEl = el;
    if (el) el.classList.add("focus");
  }
  function enter(z) {
    zone = z;
    document.body.classList.toggle("notRoll", z !== "roll");
    $("hints").innerHTML = HINTS[z] || "";
  }
  function focusRoll() { enter("roll"); setFocus(null); }
  function focusMid(i) {
    var els = midEls();
    if (!midItems.length) return focusRoll();
    mp = Math.max(0, Math.min(els.length - 1, i));
    if (zone !== "mid") enter("mid");
    setFocus(els[mp]);
  }
  function focusSide(i) {
    sp = Math.max(0, Math.min(SIDE.length - 1, i));
    if (zone !== "side") enter("side");
    setFocus(sideEls[sp]);
  }

  // ---------- search ----------
  var libItems = [], si = 0, libOpen = false, COLS = 7, TILE_H = 218;
  function renderLib() {
    var q = $("q").value.trim().toLowerCase(), grid = $("libGrid"), html = "";
    libItems = everything.filter(function (it) { return !q || it.title.toLowerCase().indexOf(q) >= 0; });
    for (var i = 0; i < libItems.length; i++) {
      var it = libItems[i];
      var ic = it.kind === "input" ? '<div class="port">' + esc(it.short) + "</div>"
        : it.svg ? it.svg : (it.icon ? '<img data-src="' + it.icon + '" alt="">' : "");
      html += '<div class="tile' + (inRoll(it.id) ? " pinned" : "") + '"><div class="ic">' + ic + '</div><div class="nm">' + esc(it.title) + "</div></div>";
    }
    grid.innerHTML = html;
    var imgs = grid.querySelectorAll("img");
    for (var j = 0; j < imgs.length; j++) imgs[j].src = imgs[j].getAttribute("data-src");
    si = Math.min(si, Math.max(0, libItems.length - 1));
    focusedEl = null;
    grid.style.webkitTransform = grid.style.transform = "translate3d(0,0,0)";
  }
  function openLib() {
    libOpen = true;
    $("lib").classList.remove("hidden");
    $("q").value = "";
    si = 0;
    renderLib();
    focusSearchBox();
  }
  function closeLib() {
    libOpen = false;
    $("q").blur();
    $("lib").classList.add("hidden");
    focusedEl = null;
    focusSide(sp);
  }
  function focusSearchBox() { zone = "libq"; setFocus($("libBox")); $("q").focus(); }
  function focusTile() {
    zone = "lib";
    $("q").blur();
    var tiles = $("libGrid").children;
    if (!tiles.length) return focusSearchBox();
    si = Math.max(0, Math.min(si, tiles.length - 1));
    setFocus(tiles[si]);
    var off = Math.max(0, (Math.floor(si / COLS) - 2) * TILE_H);
    $("libGrid").style.webkitTransform = $("libGrid").style.transform = "translate3d(0," + (-off) + "px,0)";
  }
  $("q").addEventListener("input", function () { si = 0; renderLib(); });

  // ---------- keys ----------
  var K = { L: 37, U: 38, R: 39, D: 40, OK: 13, BACK: 461, ESC: 27 };
  var okTimer = null, okLong = false;
  function okShort() {
    if (zone === "roll") activate(byId[rollIds[rp]]);
    else if (zone === "lib") activate(libItems[si]);
  }
  function okHold() {
    if (zone === "roll") setPinned(rollIds[rp], false);
    else if (zone === "lib") {
      var it = libItems[si];
      setPinned(it.id, !inRoll(it.id));
      var tile = $("libGrid").children[si];
      if (tile && it.kind === "app") tile.classList.toggle("pinned", inRoll(it.id));
    }
  }
  document.addEventListener("keydown", function (e) {
    var k = e.keyCode, handled = true;
    if (k === K.OK && (zone === "roll" || zone === "lib")) {
      // tap = open on release, hold 0.6 s = add/remove from the main menu
      e.preventDefault();
      if (okTimer || e.repeat) return;
      okLong = false;
      okTimer = setTimeout(function () { okLong = true; okHold(); }, 600);
      return;
    }
    if (k === K.BACK || k === K.ESC) {
      if (libOpen) closeLib();
      else if (zone !== "roll") focusRoll();
      else if (rp !== 0) { rp = 0; placeRoll(false); }
      else handled = false;
    } else if (zone === "libq") {
      if (k === K.D) focusTile();
      else if (k === K.OK && libItems.length === 1) activate(libItems[0]);
      else handled = false;
    } else if (zone === "lib") {
      if (k === K.L && si % COLS) si--;
      else if (k === K.R && si + 1 < libItems.length && (si + 1) % COLS) si++;
      else if (k === K.D && si + COLS < libItems.length) si += COLS;
      else if (k === K.U) { if (si < COLS) { focusSearchBox(); e.preventDefault(); return; } si -= COLS; }
      focusTile();
    } else if (zone === "mid") {
      if (k === K.U) focusMid(mp - 1);
      else if (k === K.D) focusMid(mp + 1);
      else if (k === K.L) focusRoll();
      else if (k === K.R) focusSide(sp);
      else if (k === K.OK) activate(byId[midItems[mp].id]);
      else handled = false;
    } else if (zone === "side") {
      if (k === K.U) focusSide(sp - 1);
      else if (k === K.D) focusSide(sp + 1);
      else if (k === K.L) { if (midItems.length) focusMid(mp); else focusRoll(); }
      else if (k === K.OK) activate(SIDE[sp]);
      else if (k !== K.R) handled = false;
    } else {
      if (k === K.U) rollMove(-1);
      else if (k === K.D) rollMove(1);
      else if (k === K.R) { if (midItems.length) focusMid(0); else focusSide(sp); }
      else if (k !== K.L) handled = false;
    }
    if (handled) e.preventDefault();
  });
  document.addEventListener("keyup", function (e) {
    if (e.keyCode !== K.OK || !okTimer) return;
    clearTimeout(okTimer);
    okTimer = null;
    if (!okLong) okShort();
  });

  // pointer (magic remote / phone touchpad)
  document.addEventListener("click", function (e) {
    var n = e.target;
    while (n && n !== document.body) {
      if (n.classList.contains("wi")) {
        var r = rollEls.indexOf(n);
        if (r >= 0) { if (r === rp) activate(byId[rollIds[r]]); else { rp = r; placeRoll(false); } return; }
      }
      if (n.id === "cont" || n.classList.contains("rc")) {
        var m = midEls().indexOf(n);
        if (m >= 0) return activate(byId[midItems[m].id]);
      }
      if (n.classList.contains("si")) { var s2 = sideEls.indexOf(n); if (s2 >= 0) return activate(SIDE[s2]); }
      if (n.classList.contains("tile")) {
        var t = Array.prototype.indexOf.call($("libGrid").children, n);
        if (t >= 0) return activate(libItems[t]);
      }
      n = n.parentNode;
    }
  });

  // ---------- clock ----------
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function tick() {
    var d = new Date();
    $("clock").textContent = pad(d.getHours()) + ":" + pad(d.getMinutes());
    $("date").textContent = DAYS[d.getDay()] + " " + pad(d.getDate()) + " " + MONTHS[d.getMonth()];
  }

  // ---------- server sync: recent history + phone pins (never blocks first paint) ----------
  function readParams() {
    try {
      var p = window.PalmSystem && PalmSystem.launchParams ? JSON.parse(PalmSystem.launchParams) : {};
      if (p && p.server) { server = p.server; save("ql-server", server); }
    } catch (e) {}
  }
  function syncServer() {
    if (!server) return;
    var x = new XMLHttpRequest();
    x.open("GET", server + "/api/launcher");
    x.timeout = 2500;
    x.onload = function () {
      if (x.status !== 200) return;
      var j = JSON.parse(x.responseText);
      if (j.now) serverSkew = now() - j.now;
      recentServer = j.recent || [];
      // phone pins that aren't on the roll yet get added (never removes anything)
      var added = false;
      (j.favorites || []).forEach(function (f) {
        if (byId[f.id] && byId[f.id].kind === "app" && !inRoll(f.id)) { rollIds.unshift(f.id); added = true; }
      });
      if (added) { save("ql-roll", rollIds); buildRoll(); }
      save("ql-data", { favorites: j.favorites || [], inputs: j.inputs || [], recent: recentServer });
      renderMid();
    };
    x.send();
  }
  document.addEventListener("webOSRelaunch", function () {
    readParams();
    if (libOpen) closeLib();
    renderMid();
    if (midItems.length) focusMid(0); else focusRoll();
    syncServer();
  });

  tick(); setInterval(tick, 15000);
  buildRoll();
  renderMid();
  if (midItems.length) focusMid(0); else focusRoll();   // open on "Continue": OK resumes right away
  readParams();
  setTimeout(syncServer, 300);
  setInterval(renderMid, 60000);   // keep "x min ago" honest
})();
