/* airfield-wx public demo — data-source shim + scenario switcher.
 *
 * Loaded ONLY by the demo build, before app.js (both `defer`, so this runs
 * first). The shipping dashboard never loads this file, so the real server
 * still serves live data unchanged.
 *
 * It monkeypatches window.fetch so the dashboard's /api/v1/* calls resolve to
 * the frozen synthetic snapshot JSON instead of a live server. No backend, no
 * network — the demo cannot call out or accumulate state.
 */
(function () {
  'use strict';

  var SCENARIOS = ['vfr', 'mvfr', 'offline'];
  var LABELS = { vfr: 'Clear (VFR)', mvfr: 'Marginal (MVFR)', offline: 'Feed offline' };

  function activeScenario() {
    var h = (location.hash || '').replace('#', '');
    return SCENARIOS.indexOf(h) !== -1 ? h : 'vfr';
  }
  var scenario = activeScenario();

  // Map a dashboard API path to its frozen snapshot file (query string ignored).
  function mapPath(url) {
    if (url.indexOf('/api/v1/current') !== -1) return './snapshots/' + scenario + '/current.json';
    if (url.indexOf('/api/v1/branding') !== -1) return './snapshots/branding.json';
    if (url.indexOf('/api/v1/history') !== -1) return './snapshots/history.json';
    return null;
  }

  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('/api/v1/') !== -1) {
      var mapped = mapPath(url);
      if (mapped) return realFetch(mapped, init);
    }
    return realFetch(input, init);
  };

  // A small switcher so visitors can flip between the frozen scenarios.
  function buildSwitcher() {
    var bar = document.createElement('div');
    bar.className = 'demo-switch';

    var label = document.createElement('span');
    label.className = 'demo-switch-label';
    label.textContent = 'Demo scenario:';
    bar.appendChild(label);

    SCENARIOS.forEach(function (s) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = LABELS[s];
      b.className = 'demo-switch-btn' + (s === scenario ? ' active' : '');
      b.addEventListener('click', function () {
        if (s === scenario) return;
        location.hash = s;
        location.reload();
      });
      bar.appendChild(b);
    });

    var banner = document.querySelector('.demo-banner');
    if (banner && banner.parentNode) {
      banner.parentNode.insertBefore(bar, banner.nextSibling);
    } else {
      document.body.insertBefore(bar, document.body.firstChild);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', buildSwitcher);
  } else {
    buildSwitcher();
  }
})();
