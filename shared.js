/* ─────────────────────────────────────────────────────────────
   BOVA11 Shared JavaScript
   Theme toggle, formatting helpers, Chart.js defaults.
   All generated pages should include: <script src="shared.js"></script>
   ───────────────────────────────────────────────────────────── */

/* ── Theme Toggle ──────────────────────────────────────────── */
(function applySavedTheme() {
  try {
    var saved = localStorage.getItem('bova11-theme');
    if (saved === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    }
  } catch (e) {}
})();

function toggleTheme() {
  var root = document.documentElement;
  var isDark = root.getAttribute('data-theme') === 'dark';
  if (isDark) {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', 'dark');
  }
  try {
    localStorage.setItem('bova11-theme', isDark ? 'light' : 'dark');
  } catch (e) {}
  /* Dispatch event so pages can re-render charts if needed */
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

/* Returns sentiment CSS class based on threshold */
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
  var gridColor = isDark ? '#21262d' : 'rgba(0,0,0,0.06)';
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

/* Apply defaults on load */
if (typeof Chart !== 'undefined') {
  bova11ChartDefaults();
  /* Re-apply on theme change */
  window.addEventListener('bova11:themechange', function() {
    bova11ChartDefaults();
  });
}

/* ── Iframe Theme Sync ─────────────────────────────────────── */
/* When loaded inside an iframe (index.html dashboard), hide the
   standalone theme button since the parent handles theme toggling. */
(function() {
  try {
    if (window.self !== window.top) {
      var btn = document.getElementById('theme-toggle')
            || document.getElementById('theme-btn');
      if (btn) btn.style.display = 'none';
    }
  } catch (e) {}
})();
