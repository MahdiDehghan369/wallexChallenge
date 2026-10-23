// Read-only dashboard helpers: theme toggle, clickable rows, equity/drawdown charts (Chart.js).
(function () {
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  var saved = get("siavash-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function lineChart(canvas, labels, values, color, suffix) {
    var muted = css("--muted"), line = css("--line");
    return new Chart(canvas, {
      type: "line",
      data: { labels: labels, datasets: [{ data: values, borderColor: color, borderWidth: 2,
        pointRadius: 0, pointHoverRadius: 4, fill: false, tension: 0 }] },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false },
          tooltip: { rtl: false, callbacks: { label: function (c) {
            return c.parsed.y.toLocaleString("en-US") + suffix; } } } },
        scales: {
          x: { ticks: { color: muted, maxTicksLimit: 6, maxRotation: 0 }, grid: { display: false } },
          y: { position: "right", ticks: { color: muted, callback: function (v) {
            return Number(v).toLocaleString("en-US"); } }, grid: { color: line } }
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme");
    if (btn) btn.addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme");
      if (!cur) cur = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
      var next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      set("siavash-theme", next);
      location.reload();
    });
    document.querySelectorAll("tr[data-href]").forEach(function (tr) {
      tr.addEventListener("click", function () { location.href = tr.getAttribute("data-href"); });
    });
    var holder = document.getElementById("charts");
    if (holder && window.Chart) {
      var d = JSON.parse(holder.getAttribute("data-chart"));
      if (d.ts && d.ts.length > 1) {
        lineChart(document.getElementById("eq"), d.ts, d.equity, css("--series-1"), " TMN");
        lineChart(document.getElementById("dd"), d.ts, d.dd, css("--series-dd"), " %");
      }
    }
  });
})();
