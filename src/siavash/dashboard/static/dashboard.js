// Read-only dashboard helpers: theme, charts (Chart.js), decision drawer, JSON colouring, endgame countdown and
// in-place auto-refresh (re-fetches the same page with GET and swaps <main> + the top bar). Nothing here writes.
(function () {
  "use strict";
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  var saved = get("siavash-theme");
  if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function fmt(v, nd) {
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: nd, maximumFractionDigits: nd });
  }
  function signed(v, nd) { var s = fmt(Math.abs(v), nd); return (v > 0 ? "+" : v < 0 ? "-" : "") + s; }
  function alpha(hex, a) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex);
    if (!m) return hex;
    var n = parseInt(m[1], 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }

  // ------------------------------------------------------------------ charts
  var charts = [];
  function lineChart(canvas, labels, values, color, kind) {
    var muted = css("--muted"), grid = css("--grid"), surface = css("--surface-2"), text = css("--text");
    return new Chart(canvas, {
      type: "line",
      data: { labels: labels, datasets: [{
        data: values, borderColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
        pointHoverBackgroundColor: color, tension: 0.25, fill: kind === "dd" ? "origin" : "start",
        backgroundColor: function (ctx) {
          var area = ctx.chart.chartArea;
          if (!area) return alpha(color, 0.15);
          var g = ctx.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
          if (kind === "dd") { g.addColorStop(0, alpha(color, 0.02)); g.addColorStop(1, alpha(color, 0.3)); }
          else { g.addColorStop(0, alpha(color, 0.28)); g.addColorStop(1, alpha(color, 0)); }
          return g;
        }
      }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            rtl: true, textDirection: "rtl", backgroundColor: surface, titleColor: muted, bodyColor: text,
            borderColor: css("--border"), borderWidth: 1, padding: 10, displayColors: false,
            titleFont: { family: "Vazirmatn" }, bodyFont: { family: "Vazirmatn", weight: "600" },
            callbacks: {
              title: function (items) { return items.length ? "زمان (UTC): " + items[0].label : ""; },
              label: function (c) {
                return kind === "dd" ? "افت از اوج: " + signed(c.parsed.y, 2) + " ٪"
                                     : "ارزش حساب: " + fmt(c.parsed.y, 0) + " تومان";
              }
            }
          }
        },
        scales: {
          x: { ticks: { color: muted, maxTicksLimit: 5, maxRotation: 0, font: { size: 11 } },
               grid: { display: false }, border: { display: false } },
          y: { position: "right", ticks: { color: muted, maxTicksLimit: 5, font: { size: 11 },
                 callback: function (v) { return kind === "dd" ? fmt(v, 1) + "%" : fmt(v, 0); } },
               grid: { color: grid, drawTicks: false }, border: { display: false } }
        }
      }
    });
  }

  function initCharts() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    var holder = document.getElementById("charts");
    if (!holder || !window.Chart) return;
    Chart.defaults.font.family = "Vazirmatn, Tahoma, sans-serif";
    var d;
    try { d = JSON.parse(holder.getAttribute("data-chart")); } catch (e) { return; }
    if (!d.ts || d.ts.length < 2) return;
    charts.push(lineChart(document.getElementById("eq"), d.ts, d.equity, css("--accent"), "eq"));
    charts.push(lineChart(document.getElementById("dd"), d.ts, d.dd, css("--loss"), "dd"));
    holder.querySelectorAll(".skeleton").forEach(function (el) { el.classList.remove("skeleton"); });
  }

  // ------------------------------------------------------------------ JSON colouring (built from text nodes only)
  var TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  function colorJson(pre) {
    if (pre.getAttribute("data-colored")) return;
    var text = pre.textContent, last = 0, m, frag = document.createDocumentFragment();
    function span(cls, s) { var e = document.createElement("span"); e.className = cls; e.textContent = s; frag.appendChild(e); }
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(text))) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      if (m[1]) { span(m[2] ? "j-k" : "j-s", m[1]); if (m[2]) frag.appendChild(document.createTextNode(m[2])); }
      else if (m[3]) span("j-b", m[3]);
      else if (m[4]) span("j-z", m[4]);
      else span("j-n", m[5]);
      last = TOKEN.lastIndex;
    }
    frag.appendChild(document.createTextNode(text.slice(last)));
    pre.textContent = "";
    pre.appendChild(frag);
    pre.setAttribute("data-colored", "1");
  }

  // ------------------------------------------------------------------ drawer (opportunities row -> decision JSON)
  var drawer, lastFocus;
  function drawerOpen() { return drawer && drawer.classList.contains("open"); }
  function closeDrawer() {
    if (!drawerOpen()) return;
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    document.getElementById("drawer-bg").classList.remove("open");
    if (lastFocus) lastFocus.focus();
  }
  function openDrawer(href) {
    var body = document.getElementById("drawer-body"), title = document.getElementById("drawer-title");
    lastFocus = document.activeElement;
    document.getElementById("drawer-open").setAttribute("href", href);
    title.textContent = "جزئیات تصمیم";
    body.textContent = "";
    var sk = document.createElement("div"); sk.className = "skeleton"; sk.style.height = "320px"; body.appendChild(sk);
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    document.getElementById("drawer-bg").classList.add("open");
    document.getElementById("drawer-close").focus();
    fetch(href, { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      var pre = doc.getElementById("json"), t = doc.getElementById("decision-title"), reason = doc.getElementById("decision-reason");
      body.textContent = "";
      if (t) title.textContent = t.textContent.replace(/\s+/g, " ").trim();
      if (reason) { var p = document.createElement("p"); p.className = "reason"; p.textContent = reason.textContent; body.appendChild(p); }
      var out = document.createElement("pre"); out.className = "json"; out.textContent = pre ? pre.textContent : "—";
      body.appendChild(out);
      colorJson(out);
    }).catch(function () {
      body.textContent = "بارگذاری جزئیات ممکن نشد؛ با دکمه بالا صفحه جدا را باز کنید.";
    });
  }
  function bindRows(root) {
    root.querySelectorAll("tr[data-href]").forEach(function (tr) {
      function go(ev) {
        if (ev.target.closest("a")) return;
        var href = tr.getAttribute("data-href");
        if (ev.ctrlKey || ev.metaKey) { window.open(href, "_blank"); return; }
        openDrawer(href);
      }
      tr.addEventListener("click", go);
      tr.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(ev); } });
    });
  }

  // ------------------------------------------------------------------ endgame countdown (top bar)
  function tick() {
    document.querySelectorAll("[data-countdown]").forEach(function (el) {
      var end = Date.parse(el.getAttribute("data-end"));
      if (isNaN(end)) return;
      var s = Math.max(0, Math.floor((end - Date.now()) / 1000));
      var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
      function p(n) { return (n < 10 ? "0" : "") + n; }
      el.textContent = (d ? d + "d " : "") + p(h) + ":" + p(m) + ":" + p(sec);
    });
  }

  // ------------------------------------------------------------------ in-place refresh (GET the same page)
  function refresh() {
    if (document.hidden || drawerOpen()) return;
    var bar = document.getElementById("progress");
    if (bar) bar.classList.add("on");
    fetch(location.href, { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      var main = doc.getElementById("main"), top = doc.getElementById("top");
      if (!main || !top) throw new Error("layout");
      var y = window.scrollY, scrollers = [];
      document.querySelectorAll("#main .tw").forEach(function (t) { scrollers.push([t.scrollTop, t.scrollLeft]); });
      charts.forEach(function (c) { c.destroy(); });
      charts = [];
      document.getElementById("main").replaceChildren.apply(document.getElementById("main"),
        Array.prototype.slice.call(main.childNodes).map(function (n) { return document.adoptNode(n); }));
      document.getElementById("top").replaceChildren.apply(document.getElementById("top"),
        Array.prototype.slice.call(top.childNodes).map(function (n) { return document.adoptNode(n); }));
      document.querySelectorAll("#main .tw").forEach(function (t, i) { if (scrollers[i]) { t.scrollTop = scrollers[i][0]; t.scrollLeft = scrollers[i][1]; } });
      window.scrollTo(0, y);
      initPage(document);
    }).catch(function () {
      var r = document.getElementById("refreshed");
      if (r) { r.classList.add("stale"); r.title = "بازخوانی ناموفق؛ دوباره تلاش می‌شود"; }
    }).then(function () { if (bar) bar.classList.remove("on"); });
  }

  // ------------------------------------------------------------------ wiring
  function bindTheme() {
    var btn = document.getElementById("theme");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") || "dark";
      var next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      set("siavash-theme", next);
      initCharts();
    });
  }
  function initPage(root) {
    bindTheme();
    bindRows(root);
    root.querySelectorAll("pre.json").forEach(colorJson);
    initCharts();
    tick();
  }

  document.addEventListener("DOMContentLoaded", function () {
    drawer = document.getElementById("drawer");
    document.getElementById("drawer-close").addEventListener("click", closeDrawer);
    document.getElementById("drawer-bg").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape") closeDrawer(); });
    initPage(document);
    setInterval(tick, 1000);
    var every = parseInt(document.body.getAttribute("data-refresh") || "0", 10);
    if (every > 0) setInterval(refresh, every * 1000);
  });
  // Chart.js is deferred: draw once it has loaded
  window.addEventListener("load", initCharts);
})();
