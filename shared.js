/* ─────────────────────────────────────────────────────────────
   BOVA11 Shared JavaScript
   Theme toggle, formatting helpers, Chart.js defaults.
   All generated pages should include: <script src="shared.js"></script>
   ───────────────────────────────────────────────────────────── */

/* ── Theme Init ────────────────────────────────────────────── */
/* Runs BEFORE body renders. Always sync <html> to localStorage.
   Critical: must REMOVE data-theme="dark" when theme is light,
   otherwise pages with hardcoded data-theme="dark" stay dark. */
(function() {
  try {
    var saved = localStorage.getItem('bova11-theme');
    if (saved === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  } catch (e) {}
})();

/* ── Theme Toggle ──────────────────────────────────────────── */
function toggleTheme() {
  var root = document.documentElement;
  var isDark = root.getAttribute('data-theme') === 'dark';
  if (isDark) {
    root.removeAttribute('data-theme');
    try { localStorage.setItem('bova11-theme', 'light'); } catch (e) {}
  } else {
    root.setAttribute('data-theme', 'dark');
    try { localStorage.setItem('bova11-theme', 'dark'); } catch (e) {}
  }
  window.dispatchEvent(new CustomEvent('bova11:themechange', { detail: { dark: !isDark } }));
}

/* ── Formatting Helpers ────────────────────────────────────── */
function fmt(v, d) {
  if (d === undefined) d = 4;
  if (v === null || v === undefined || isNaN(v)) return 'N/A';
  return Number(v).toLocaleString('pt-BR', {
    minimumFractionDigits: d,
    maximumFractionDigits: d
  });
}

function fmtInt(v) {
  if (v === null || v === undefined || isNaN(v)) return 'N/A';
  return Math.round(Number(v)).toLocaleString('pt-BR');
}

function fmtPct(v, d) {
  if (d === undefined) d = 1;
  if (v === null || v === undefined || isNaN(v)) return 'N/A';
  return fmt(v, d) + '%';
}

function tone(v, posThresh, negThresh) {
  if (posThresh === undefined) posThresh = 0.25;
  if (negThresh === undefined) negThresh = -0.25;
  if (v > posThresh) return 'pos';
  if (v < negThresh) return 'neg';
  return 'neu';
}

/* ── Chart.js Global Defaults ──────────────────────────────── */
function bova11ChartDefaults() {
  if (typeof Chart === 'undefined') return;
  var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  var t2 = isDark ? '#8b949e' : '#6B6960';
  var t3 = isDark ? '#636c76' : '#9C9A91';
  var borderColor = isDark ? '#30363d' : 'rgba(0,0,0,0.14)';

  Chart.defaults.color = t2;
  Chart.defaults.borderColor = borderColor;
  Chart.defaults.font.family = "'Instrument Sans', system-ui, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.tooltip.backgroundColor = isDark ? '#161b22' : '#fff';
  Chart.defaults.plugins.tooltip.titleColor = isDark ? '#c9d1d9' : '#1A1A18';
  Chart.defaults.plugins.tooltip.bodyColor = isDark ? '#8b949e' : '#6B6960';
  Chart.defaults.plugins.tooltip.borderColor = borderColor;
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.line.tension = 0.2;
  Chart.defaults.responsive = true;
  Chart.defaults.animation = false;
}

if (typeof Chart !== 'undefined') {
  bova11ChartDefaults();
  window.addEventListener('bova11:themechange', function() {
    bova11ChartDefaults();
  });
}

/* ── Iframe Sync ───────────────────────────────────────────── */
/* Inside iframe: hide standalone theme button (parent handles it).
   Also listen for postMessage from parent for instant theme sync
   without full iframe reload. */
(function() {
  try {
    if (window.self !== window.top) {
      var btn = document.getElementById('theme-toggle')
            || document.getElementById('theme-btn');
      if (btn) btn.style.display = 'none';

      window.addEventListener('message', function(e) {
        if (e.data && e.data.type === 'bova11-theme') {
          if (e.data.dark) {
            document.documentElement.setAttribute('data-theme', 'dark');
          } else {
            document.documentElement.removeAttribute('data-theme');
          }
          try { localStorage.setItem('bova11-theme', e.data.dark ? 'dark' : 'light'); } catch (err) {}
          window.dispatchEvent(new CustomEvent('bova11:themechange', { detail: { dark: e.data.dark } }));
        }
      });
    }
  } catch (e) {}
})();
