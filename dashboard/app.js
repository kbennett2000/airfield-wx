// airfield-wx — live data
//
// All numbers come from /api/v1/current (every CURRENT_REFRESH_MS) and
// /api/v1/history/outdoor?hours=<window> (on window change + every
// HISTORY_REFRESH_MS). The dashboard never computes derivations
// client-side — the API is the single source of truth.

const API_BASE = '';                       // same-origin
const CURRENT_REFRESH_MS = 30_000;         // /current poll cadence
const HISTORY_REFRESH_MS = 60_000;         // /history poll cadence
const SUMMARY_REFRESH_MS = 60_000;         // /summary poll cadence

const TEXT_DIM  = '#7b8696';
const HAIRLINE  = '#232e3c';
const AMBER     = '#ffb547';
const AMBER_DIM = 'rgba(255, 181, 71, 0.12)';
const CYAN      = '#58c4d4';
const CYAN_DIM  = 'rgba(88, 196, 212, 0.10)';

// ─────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────

let timezone = 'UTC';
let currentWindowHours = (() => {
  // ?hours=N in the URL deep-links to a specific time window for the
  // history panel. Useful for screenshots and shareable links. Only
  // accepted values are the same ones the button bar offers.
  const allowed = new Set([1, 6, 12, 24, 168]);
  const fromUrl = parseInt(new URLSearchParams(window.location.search).get('hours'), 10);
  return allowed.has(fromUrl) ? fromUrl : 24;
})();
let currentSummaryPeriod = 'today';
let charts = {};

// ─────────────────────────────────────────────────────────────────
// DOM helpers
// ─────────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function setLed(id, state) {
  // state: 'on' | 'warn' | 'fault' | 'off'
  const el = $(id);
  if (!el) return;
  el.classList.remove('on', 'warn', 'fault');
  if (state !== 'off') el.classList.add(state);
}

function fmt(n, decimals = 1) {
  if (n === null || n === undefined || Number.isNaN(n)) return '--';
  return Number(n).toFixed(decimals);
}

function fmtInt(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '--';
  return Math.round(n).toLocaleString();
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '--';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  if (s < 86_400) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${m}m`;
  }
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3600);
  return `${d}d ${h}h`;
}

function fmtHours(seconds) {
  if (seconds === null || seconds === undefined) return '--';
  return (seconds / 3600).toFixed(1);
}

function fmtSigned(n, decimals = 1) {
  if (n === null || n === undefined || Number.isNaN(n)) return '--';
  const v = Number(n);
  return (v >= 0 ? '+' : '') + v.toFixed(decimals);
}

function fmtKB(bytes) {
  if (bytes === null || bytes === undefined) return '--';
  return (bytes / 1024).toFixed(1);
}

function fmtTimeOfDay(isoString) {
  // Server emits sun/moon event timestamps in the resolved station zone
  // (post-Phase-4.5). `timeZone: timezone` is still passed because the
  // browser may not be in the station's zone — this anchors the wall
  // clock to the station for any viewer.
  if (!isoString) return '--';
  const d = new Date(isoString);
  return d.toLocaleTimeString('en-US', {
    hour12: false, hour: '2-digit', minute: '2-digit', timeZone: timezone
  });
}

function rssiBand(rssi) {
  if (rssi === null || rssi === undefined) return '--';
  if (rssi > -50) return 'excellent';
  if (rssi > -65) return 'strong';
  if (rssi > -75) return 'fair';
  return 'weak';
}

function luxBand(lux) {
  if (lux === null || lux === undefined) return '--';
  if (lux < 1)       return 'night';
  if (lux < 50)      return 'twilight';
  if (lux < 1000)    return 'overcast indoor';
  if (lux < 10_000)  return 'overcast daylight';
  if (lux < 50_000)  return 'full daylight';
  return 'direct sun';
}

// ─────────────────────────────────────────────────────────────────
// Fetch
// ─────────────────────────────────────────────────────────────────

async function fetchJson(path) {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

// ─────────────────────────────────────────────────────────────────
// Clock — server time is the source of truth, but ticks locally
// every second between fetches so the readout doesn't look frozen.
// Each /current response re-anchors; a backgrounded tab resyncs on
// the next foreground fetch.
// ─────────────────────────────────────────────────────────────────

let clockAnchor = null;  // { serverMs: number, localMs: number }

function anchorServerTime(serverTimeIso) {
  clockAnchor = {
    serverMs: new Date(serverTimeIso).getTime(),
    localMs:  performance.now(),
  };
  renderClock();
}

function renderClock() {
  if (!clockAnchor) return;
  const elapsed = performance.now() - clockAnchor.localMs;
  const d = new Date(clockAnchor.serverMs + elapsed);
  setText('clock', d.toLocaleTimeString('en-US', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
    timeZone: timezone,
  }));
  setText('clock-date', d.toLocaleDateString('en-US', {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
    timeZone: timezone,
  }).toUpperCase());
}

// ─────────────────────────────────────────────────────────────────
// Polar sky plot
// ─────────────────────────────────────────────────────────────────

function renderPolar(sun, moon) {
  const sunGroup = $('polar-sun');
  const moonGroup = $('polar-moon');
  sunGroup.innerHTML = '';
  moonGroup.innerHTML = '';

  if (sun && sun.altitude_deg !== null && sun.azimuth_deg !== null) {
    const { x, y, below } = polarXY(sun.altitude_deg, sun.azimuth_deg);
    const opacity = below ? 0.35 : 1;
    sunGroup.setAttribute('opacity', opacity);
    sunGroup.innerHTML = `
      <circle cx="${x}" cy="${y}" r="22" fill="url(#sunGlow)"/>
      <circle cx="${x}" cy="${y}" r="7"  fill="#ffd57a"/>
      <circle cx="${x}" cy="${y}" r="3"  fill="#fff"/>
      <text x="${x + 7}" y="${y + 6}" font-family="JetBrains Mono" font-size="9" fill="#ffb547" font-weight="500">SUN</text>
    `;
  }

  if (moon && moon.altitude_deg !== null && moon.azimuth_deg !== null) {
    const { x, y, below } = polarXY(moon.altitude_deg, moon.azimuth_deg);
    const opacity = below ? 0.45 : 1;
    moonGroup.setAttribute('opacity', opacity);
    const belowMark = below ? `<text x="${x - 32}" y="${y + 8}" font-family="JetBrains Mono" font-size="7" fill="#58c4d4">↓ below</text>` : '';
    moonGroup.innerHTML = `
      <circle cx="${x}" cy="${y}" r="14" fill="url(#moonGlow)"/>
      <circle cx="${x}" cy="${y}" r="5"  fill="#cfe4ee"/>
      <text x="${x - 25}" y="${y - 6}" font-family="JetBrains Mono" font-size="9" fill="#58c4d4" font-weight="500">MOON</text>
      ${belowMark}
    `;
  }
}

function polarXY(altDeg, azDeg) {
  // SVG group is translated to center (130, 130); we return offsets from center.
  // Altitude rings: 0° at r=120, 90° at r=0. Below-horizon clamped to horizon ring.
  const below = altDeg < 0;
  const effectiveAlt = below ? 0 : altDeg;
  const r = ((90 - effectiveAlt) / 90) * 120;
  const az = (azDeg * Math.PI) / 180;
  // Az 0° = N (top), 90° = E (right). x = r*sin(az), y = -r*cos(az).
  const x = r * Math.sin(az);
  const y = -r * Math.cos(az);
  return { x: +x.toFixed(2), y: +y.toFixed(2), below };
}

// ─────────────────────────────────────────────────────────────────
// Day arc
// ─────────────────────────────────────────────────────────────────

function renderDayArc(sun, serverTimeIso) {
  const content = $('day-arc-content');
  content.innerHTML = '';
  if (!sun) return;

  const x = (iso) => xForTimeOfDay(iso);
  const now = serverTimeIso;

  const sunriseX = sun.sunrise ? x(sun.sunrise) : null;
  const sunsetX  = sun.sunset  ? x(sun.sunset)  : null;
  const dawnX    = sun.dawn    ? x(sun.dawn)    : null;
  const duskX    = sun.dusk    ? x(sun.dusk)    : null;
  const noonX    = sun.solar_noon ? x(sun.solar_noon) : null;
  const nowX     = x(now);

  let svg = '';

  if (sunriseX !== null && sunsetX !== null) {
    svg += `<rect x="${sunriseX}" y="33" width="${sunsetX - sunriseX}" height="4" rx="2" fill="url(#dayGrad)" opacity="0.85"/>`;
  }

  // Twilight bands + photography windows — faint ticks (no labels, to avoid
  // crowding the dawn/dusk/sunrise/sunset/noon markers drawn on top).
  const tick = (iso, color) => {
    if (!iso) return '';
    const tx = x(iso);
    return `<line x1="${tx}" y1="30" x2="${tx}" y2="40" stroke="${color}" stroke-width="1"/>`;
  };
  svg += tick(sun.astronomical_dawn, '#2f3b56') + tick(sun.astronomical_dusk, '#2f3b56');
  svg += tick(sun.nautical_dawn, '#3c4f72') + tick(sun.nautical_dusk, '#3c4f72');
  svg += tick(sun.blue_hour_dawn, '#58c4d4') + tick(sun.blue_hour_dusk, '#58c4d4');
  svg += tick(sun.golden_hour_dawn, '#ffd57a') + tick(sun.golden_hour_dusk, '#ffd57a');

  if (dawnX !== null) {
    svg += `
      <line x1="${dawnX}" y1="26" x2="${dawnX}" y2="44" stroke="#5a6878" stroke-width="1"/>
      <text x="${dawnX}" y="16" text-anchor="middle" font-family="JetBrains Mono" font-size="8" fill="#7b8696" letter-spacing="1">DAWN</text>
      <text x="${dawnX}" y="58" text-anchor="middle" font-family="JetBrains Mono" font-size="9" fill="#c7cfdb">${fmtTimeOfDay(sun.dawn)}</text>
    `;
  }
  if (sunriseX !== null) {
    svg += `
      <line x1="${sunriseX}" y1="25" x2="${sunriseX}" y2="45" stroke="#ffb547" stroke-width="1.5"/>
      <circle cx="${sunriseX}" cy="35" r="3" fill="#ffb547"/>
      <text x="${sunriseX}" y="68" text-anchor="middle" font-family="JetBrains Mono" font-size="9" fill="#ffb547">${fmtTimeOfDay(sun.sunrise)}</text>
    `;
  }
  if (noonX !== null) {
    svg += `
      <line x1="${noonX}" y1="22" x2="${noonX}" y2="48" stroke="#ffd57a" stroke-width="1.5"/>
      <circle cx="${noonX}" cy="35" r="4" fill="#ffd57a"/>
      <text x="${noonX}" y="16" text-anchor="middle" font-family="JetBrains Mono" font-size="8" fill="#ffb547" letter-spacing="1">NOON</text>
      <text x="${noonX}" y="58" text-anchor="middle" font-family="JetBrains Mono" font-size="9" fill="#c7cfdb">${fmtTimeOfDay(sun.solar_noon)}</text>
    `;
  }
  if (sunsetX !== null) {
    svg += `
      <line x1="${sunsetX}" y1="25" x2="${sunsetX}" y2="45" stroke="#ffb547" stroke-width="1.5"/>
      <circle cx="${sunsetX}" cy="35" r="3" fill="#ffb547"/>
      <text x="${sunsetX}" y="68" text-anchor="middle" font-family="JetBrains Mono" font-size="9" fill="#ffb547">${fmtTimeOfDay(sun.sunset)}</text>
    `;
  }
  if (duskX !== null) {
    svg += `
      <line x1="${duskX}" y1="26" x2="${duskX}" y2="44" stroke="#5a6878" stroke-width="1"/>
      <text x="${duskX}" y="16" text-anchor="middle" font-family="JetBrains Mono" font-size="8" fill="#7b8696" letter-spacing="1">DUSK</text>
      <text x="${duskX}" y="58" text-anchor="middle" font-family="JetBrains Mono" font-size="9" fill="#c7cfdb">${fmtTimeOfDay(sun.dusk)}</text>
    `;
  }

  svg += `
    <g>
      <line x1="${nowX}" y1="20" x2="${nowX}" y2="50" stroke="#fff" stroke-width="1.5"/>
      <polygon points="${nowX},20 ${nowX - 5},12 ${nowX + 5},12" fill="#fff"/>
      <text x="${nowX}" y="9" text-anchor="middle" font-family="JetBrains Mono" font-size="8" fill="#fff" letter-spacing="1" font-weight="500">NOW</text>
    </g>
  `;

  content.innerHTML = svg;
}

function xForTimeOfDay(iso) {
  // Project the local time-of-day onto the 0..480 SVG x-axis.
  const d = new Date(iso);
  const parts = new Intl.DateTimeFormat('en-US', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
    timeZone: timezone,
  }).formatToParts(d);
  const h = +parts.find(p => p.type === 'hour').value;
  const m = +parts.find(p => p.type === 'minute').value;
  const s = +parts.find(p => p.type === 'second').value;
  const seconds = h * 3600 + m * 60 + s;
  return (seconds / 86_400) * 480;
}

// ─────────────────────────────────────────────────────────────────
// /api/v1/branding — load once on page boot
// Populates static slots, picks a random rotating tagline per load,
// and stashes the offline/loading/error copy for later use by panel
// renderers and the data-status banner.
// ─────────────────────────────────────────────────────────────────

let brandingCopy = {
  states: {},
  error: { generic: '' },
};

async function loadBranding() {
  let data;
  try {
    data = await fetchJson('/api/v1/branding');
  } catch (e) {
    console.warn('branding fetch failed:', e);
    return;
  }

  if (data?.browser_title?.text) {
    document.title = data.browser_title.text;
  }

  // Tagline: random from rotating[] when populated, else static fallback.
  const rotating = Array.isArray(data?.taglines?.rotating) ? data.taglines.rotating : [];
  const pick = rotating.length > 0
    ? rotating[Math.floor(Math.random() * rotating.length)]
    : (data?.header?.tagline || '');
  if (pick) setText('branding-tagline', pick);

  if (data?.header?.title)             setText('branding-title', data.header.title);
  if (data?.header?.subtitle)          setText('branding-subtitle', data.header.subtitle);
  if (data?.header?.system_mark)       setText('branding-system-mark', data.header.system_mark);
  if (data?.footer?.text)              setText('branding-footer', data.footer.text);
  if (data?.footer?.system_line)       setText('branding-system-line', data.footer.system_line);
  if (data?.states?.outdoor_offline)   setText('outdoor-branding', data.states.outdoor_offline);
  if (data?.states?.indoor_offline)    setText('indoor-branding',  data.states.indoor_offline);
  if (data?.states?.basement_offline)  setText('basement-branding', data.states.basement_offline);

  brandingCopy = {
    states: data?.states || {},
    error:  data?.error  || { generic: '' },
  };

  // Show the loading banner until the first /current succeeds.
  if (brandingCopy.states.loading) {
    showDataStatus(brandingCopy.states.loading, false);
  }
}

function showDataStatus(text, isError) {
  const el = $('data-status');
  if (!el) return;
  el.textContent = text;
  el.classList.toggle('error', !!isError);
  el.hidden = false;
}

function hideDataStatus() {
  const el = $('data-status');
  if (el) el.hidden = true;
}

// ─────────────────────────────────────────────────────────────────
// /api/v1/current — populate every panel
// ─────────────────────────────────────────────────────────────────

async function refreshCurrent() {
  let data;
  try {
    data = await fetchJson('/api/v1/current');
  } catch (e) {
    console.warn('current fetch failed:', e);
    setLed('led-db', 'fault');
    if (brandingCopy.error?.generic) {
      showDataStatus(brandingCopy.error.generic, true);
    }
    return;
  }
  setLed('led-db', 'on');
  hideDataStatus();

  timezone = data.astronomy?.timezone || 'UTC';
  anchorServerTime(data.server_time);

  setText('server-tz', timezone);
  setText('server-addr', window.location.host);

  const sensors = data.sensors || {};
  applyOutdoor(sensors.outdoor);
  applyIndoor(sensors.indoor);
  applyBasement(sensors.basement);
  applyAstronomy(data.astronomy, data.server_time);
  applyRegional(data.external);
  applyFeelsLike(sensors.outdoor, data.external);
  applyThermo(sensors.outdoor);
  applySky(sensors.outdoor);
}

function applyOutdoor(sr) {
  const present = !!sr;
  setLed('led-outdoor', present && sr.online ? 'on' : (present ? 'fault' : 'off'));
  setLed('led-outdoor-head', present && sr.online ? 'on' : (present ? 'fault' : 'off'));
  if (!present) {
    setText('outdoor-headsub', 'no data');
    return;
  }

  setText('outdoor-headsub', `${ageLabel(sr.age_seconds)}`);

  const d = sr.derived || {};
  const raw = sr.raw || {};
  const loc = sr.location || {};
  const dev = sr.device || {};

  setText('out-temp-f', fmt(d.temperature_f, 1));
  setText('out-temp-c', fmt(d.temperature_c, 1));
  // feels-like handled by applyFeelsLike (adaptive: apparent temp when online)
  setText('out-hum', fmt(raw.humidity_pct, 1));
  setText('out-abshum', fmt(d.absolute_humidity_g_m3, 1));
  setText('out-dew-f', fmt(d.dewpoint_f, 1));
  setText('out-dew-c', fmt(d.dewpoint_c, 1));
  setText('out-press-sl-inhg', fmt(d.pressure_sealevel_inhg, 2));
  setText('out-press-sl-hpa', fmt(d.pressure_sealevel_hpa, 1));
  setText('out-press-st-inhg', fmt(d.pressure_station_inhg, 2));
  setText('out-press-st-hpa', fmt(d.pressure_station_hpa, 1));
  setText('out-alt-m', fmtInt(loc.altitude_m));

  // Light
  setText('lux-value', fmtInt(raw.lux));
  setText('lux-band', luxBand(raw.lux));
  setText('light-visible', fmtInt(raw.visible));
  setText('light-ir', fmtInt(raw.ir));
  setText('light-full', fmtInt(raw.full));

  // GPS
  setLed('led-gps', loc.lat !== undefined && loc.lat !== null ? 'on' : 'fault');
  setLed('led-gps-head', loc.lat !== undefined && loc.lat !== null ? 'on' : 'fault');
  setText('gps-headsub', `NEO-6M · ${loc.satellites ?? '--'} SAT`);
  if (loc.lat !== null && loc.lon !== null) {
    const ns = loc.lat >= 0 ? 'N' : 'S';
    const ew = loc.lon >= 0 ? 'E' : 'W';
    setText('gps-decimal', `${Math.abs(loc.lat).toFixed(5)}° ${ns}   ${Math.abs(loc.lon).toFixed(5)}° ${ew}`);
  }
  setText('gps-dms', loc.dms ?? '--');
  setText('gps-maidenhead', loc.maidenhead ?? '--');
  setText('gps-alt-m', loc.altitude_m !== null ? `${loc.altitude_m.toLocaleString()} m` : '--');
  setText('gps-alt-ft', loc.altitude_ft !== null ? `${loc.altitude_ft.toLocaleString(undefined, { maximumFractionDigits: 1 })} ft` : '--');
  setText('gps-sats', loc.satellites ?? '--');
  setText('gps-tz', timezone);
  setText('gps-tz-offset', tzOffsetLabel());

  // Telemetry
  setText('telem-sub', `IP ${sr.role ? sr.role.toUpperCase() : ''}`);
  setText('tel-rssi', fmtInt(dev.rssi_dbm));
  setText('tel-rssi-band', rssiBand(dev.rssi_dbm));
  setText('tel-heap', fmtKB(dev.free_heap_bytes));
  setText('tel-uptime', fmtDuration(dev.uptime_s));
  setText('tel-offset', sr.calibration?.temp_offset_c ?? '--');
  setText('tel-age', fmt(sr.age_seconds, 0));
  // 'tel-records' filled in by refreshHistory()
}

function applyIndoor(sr) {
  const panel = $('panel-indoor');
  const present = !!sr;
  setLed('led-indoor', present && sr.online ? 'on' : (present ? 'fault' : 'off'));
  setLed('led-indoor-head', present && sr.online ? 'on' : (present ? 'fault' : 'off'));
  if (!present) {
    setText('indoor-headsub', 'no data');
    panel.classList.add('offline');
    return;
  }
  panel.classList.toggle('offline', !sr.online);
  setText('indoor-headsub', ageLabel(sr.age_seconds));

  const d = sr.derived || {};
  const raw = sr.raw || {};
  setText('in-temp-f', fmt(d.temperature_f, 1));
  setText('in-temp-c', fmt(d.temperature_c, 1));
  setText('in-hum', fmt(raw.humidity_pct, 1));
  setText('in-abshum', fmt(d.absolute_humidity_g_m3, 1));
  setText('in-dew-f', fmt(d.dewpoint_f, 1));
  setText('in-press-inhg', fmt(d.pressure_station_inhg, 2));
}

function applyBasement(sr) {
  const panel = $('panel-basement');
  const offlineTag = $('basement-offline-tag');
  const present = !!sr;
  setLed('led-basement', present && sr.online ? 'on' : (present ? 'fault' : 'off'));
  setLed('led-basement-head', present && sr.online ? 'on' : (present ? 'fault' : 'off'));

  if (!present) {
    panel.classList.add('offline');
    offlineTag.style.display = 'inline-flex';
    setText('basement-headsub', 'no data');
    return;
  }

  const offline = !sr.online;
  panel.classList.toggle('offline', offline);
  offlineTag.style.display = offline ? 'inline-flex' : 'none';

  setText('basement-headsub', ageLabel(sr.age_seconds));
  setText('bsmt-temp-sub', offline ? `last seen ${fmtDuration(sr.age_seconds)} ago` : `${fmt(sr.derived?.temperature_c, 1)} °C`);
  setText('bsmt-hum-label', offline ? 'Last Known Humidity' : 'Humidity');
  setText('bsmt-press-label', offline ? 'Last Known Pressure' : 'Pressure');

  const d = sr.derived || {};
  const raw = sr.raw || {};
  setText('bsmt-temp-f', offline ? '--' : fmt(d.temperature_f, 1));
  setText('bsmt-temp-c', fmt(d.temperature_c, 1));
  setText('bsmt-hum', fmt(raw.humidity_pct, 1));
  setText('bsmt-abshum', fmt(d.absolute_humidity_g_m3, 1));
  setText('bsmt-press-inhg', fmt(d.pressure_station_inhg, 2));
}

function applyAstronomy(a, serverTimeIso) {
  if (!a) return;
  setText('astro-tz', a.timezone || '--');
  renderPolar(a.sun, a.moon);
  renderDayArc(a.sun, serverTimeIso);

  if (a.sun) {
    setText('astro-daylen', fmtHours(a.sun.day_length_seconds));
    if (a.sun.seconds_to_sunset !== null && a.sun.seconds_to_sunset !== undefined) {
      setText('astro-countdown-label', 'To Sunset');
      setText('astro-countdown', fmtHours(a.sun.seconds_to_sunset));
    } else if (a.sun.seconds_to_sunrise !== null && a.sun.seconds_to_sunrise !== undefined) {
      setText('astro-countdown-label', 'To Sunrise');
      setText('astro-countdown', fmtHours(a.sun.seconds_to_sunrise));
    } else {
      setText('astro-countdown', '--');
    }
    setText('astro-sunpos', `Az ${fmt(a.sun.azimuth_deg, 0)}° · Alt ${fmt(a.sun.altitude_deg, 1)}°`);

    setText('astro-season', a.sun.season ?? '--');
    setText('astro-nextevent', eventLabel(a.sun.next_solar_event));
    setText('astro-nextevent-label', `Next Event · ${countdownDays(a.sun.seconds_to_next_solar_event)}`);
    setText('astro-daylendelta', a.sun.day_length_change_seconds !== null && a.sun.day_length_change_seconds !== undefined
      ? `${fmtSigned(a.sun.day_length_change_seconds, 0)} s` : '--');
    const sunriseAz = a.sun.sunrise_azimuth_deg !== null && a.sun.sunrise_azimuth_deg !== undefined ? `${fmt(a.sun.sunrise_azimuth_deg, 0)}°` : '--';
    const sunsetAz = a.sun.sunset_azimuth_deg !== null && a.sun.sunset_azimuth_deg !== undefined ? `${fmt(a.sun.sunset_azimuth_deg, 0)}°` : '--';
    const shadow = a.sun.shadow_multiplier !== null && a.sun.shadow_multiplier !== undefined ? `${fmt(a.sun.shadow_multiplier, 1)}×` : '—';
    setText('sun-extra', `SUNRISE AZ ${sunriseAz} · SUNSET AZ ${sunsetAz} · SHADOW ${shadow}`);
  }

  if (a.moon) {
    setText('moon-glyph', a.moon.phase_icon ?? '🌑');
    setText('moon-phase', `${a.moon.phase_name ?? '--'} · ${fmt(a.moon.illumination_pct, 1)}% illuminated`);
    const rise = a.moon.moonrise ? `RISE ${fmtTimeOfDay(a.moon.moonrise)}` : 'RISE --';
    const set = a.moon.moonset ? `SET ${fmtTimeOfDay(a.moon.moonset)}` : 'SET --';
    const dist = a.moon.distance_km !== null && a.moon.distance_km !== undefined
      ? `DIST ${a.moon.distance_km.toLocaleString(undefined, { maximumFractionDigits: 0 })} km`
      : 'DIST --';
    const az = a.moon.azimuth_deg !== null ? `AZ ${fmt(a.moon.azimuth_deg, 0)}°` : 'AZ --';
    const alt = a.moon.altitude_deg !== null ? `ALT ${fmt(a.moon.altitude_deg, 1)}°` : 'ALT --';
    setText('moon-meta', `${rise} · ${set} · ${dist} · ${az} · ${alt}`);
    setText('moon-next', `NEW ${fmtDate(a.moon.next_new_moon)} · FULL ${fmtDate(a.moon.next_full_moon)}`);
  }
}

function eventLabel(key) {
  return ({
    march_equinox: 'March Equinox',
    june_solstice: 'June Solstice',
    september_equinox: 'Sept Equinox',
    december_solstice: 'Dec Solstice',
  })[key] || (key ?? '--');
}

function countdownDays(seconds) {
  if (seconds === null || seconds === undefined) return '';
  const days = Math.round(seconds / 86_400);
  return `${days}d`;
}

function fmtDate(iso) {
  if (!iso) return '--';
  return new Date(iso).toLocaleDateString('en-US', {
    day: '2-digit', month: 'short', timeZone: timezone,
  });
}

// ─────────────────────────────────────────────────────────────────
// Regional (internet-sourced) — the `external` block. Treated exactly
// like a sensor that can be unplugged: present → LED on (or warn if
// stale), null → dimmed panel + NO FEED, mirroring the basement panel.
// ─────────────────────────────────────────────────────────────────

const REGIONAL_VALUE_IDS = [
  'reg-wind-mph', 'reg-wind-dir', 'reg-wind-kmh', 'reg-gust-mph', 'reg-beaufort',
  'reg-beaufort-desc', 'reg-apparent-f', 'reg-windchill-f', 'reg-thsw-f',
  'reg-cloud', 'reg-uv', 'reg-vis', 'reg-precip', 'reg-et0',
];

function regionalSourceLabel(ext) {
  if (ext.provider === 'open-meteo') return 'Open-Meteo';
  if (ext.station_id) return ext.station_id;
  return ext.provider || 'feed';
}

function applyRegional(ext) {
  const panel = $('panel-regional');
  const offlineTag = $('regional-offline-tag');
  const conf = $('regional-confidence');
  const present = !!ext;

  const led = present ? (ext.stale ? 'warn' : 'on') : 'off';
  setLed('led-net', led);
  setLed('led-regional-head', led);

  if (!present) {
    panel.classList.add('offline');
    offlineTag.style.display = 'inline-flex';
    conf.style.display = 'none';
    setText('regional-headsub', 'no feed');
    renderWindCompass(null);
    REGIONAL_VALUE_IDS.forEach(id => setText(id, '--'));
    return;
  }

  panel.classList.remove('offline');
  offlineTag.style.display = 'none';
  conf.style.display = ext.confidence === 'low' ? 'inline-flex' : 'none';

  const dist = (ext.distance_km !== null && ext.distance_km !== undefined)
    ? ` · ${fmt(ext.distance_km, 0)} km` : '';
  setText('regional-headsub', `via ${regionalSourceLabel(ext)}${dist} · ${ageLabel(ext.age_seconds)}`);

  renderWindCompass(ext);
  setText('reg-wind-mph', fmt(ext.wind_speed_mph, 0));
  setText('reg-wind-dir', ext.wind_direction_cardinal ?? '--');
  setText('reg-wind-kmh', fmt(ext.wind_speed_kmh, 0));
  setText('reg-gust-mph', fmt(ext.wind_gust_mph, 0));
  setText('reg-beaufort', ext.beaufort_force ?? '--');
  setText('reg-beaufort-desc', ext.beaufort_description ?? '--');
  setText('reg-apparent-f', fmt(ext.apparent_temperature_f, 0));
  setText('reg-windchill-f', fmt(ext.wind_chill_f, 0));
  setText('reg-thsw-f', fmt(ext.thsw_index_f, 0));
  setText('reg-cloud', fmt(ext.cloud_cover_pct, 0));
  setText('reg-uv', fmt(ext.uv_index, 1));
  setText('reg-vis', fmt(ext.visibility_km, 0));
  setText('reg-precip', fmt(ext.precip_mm, 1));
  setText('reg-et0', fmt(ext.et0_mm_hour, 3));
}

function renderWindCompass(ext) {
  const needle = $('wind-needle');
  if (!needle) return;
  setText('wind-compass-speed', ext ? fmt(ext.wind_speed_mph, 0) : '--');
  setText('wind-compass-card', ext?.wind_direction_cardinal ?? '--');
  if (!ext || ext.wind_direction_deg === null || ext.wind_direction_deg === undefined) {
    needle.innerHTML = '';
    return;
  }
  // Arrow sits in the outer ring and points inward from the bearing the wind
  // blows FROM (meteorological convention). Rotated clockwise: 0°=N at top.
  needle.innerHTML = `
    <g transform="rotate(${ext.wind_direction_deg} 80 80)">
      <line x1="80" y1="22" x2="80" y2="40" stroke="#ffb547" stroke-width="2"/>
      <polygon points="80,12 73,27 87,27" fill="#ffb547"/>
    </g>`;
}

function applyFeelsLike(sr, ext) {
  // Adaptive: the wind-aware apparent temperature when the feed is online,
  // else the local heat-index feels-like. A source tag shows which.
  if (ext && ext.apparent_temperature_f !== null && ext.apparent_temperature_f !== undefined) {
    setText('out-feels-f', fmt(ext.apparent_temperature_f, 0));
    setText('out-feels-src', 'apparent');
    return;
  }
  const d = sr?.derived || {};
  setText('out-feels-f', fmt(d.feels_like_f, 1));
  setText('out-feels-src', sr ? 'heat index' : '');
}

function applyThermo(sr) {
  const d = sr?.derived || {};
  setText('th-wetbulb-f', fmt(d.wet_bulb_f, 1));
  setText('th-wetbulb-c', fmt(d.wet_bulb_c, 1));
  setText('th-humidex-c', fmt(d.humidex_c, 1));
  setText('th-frost-c', fmt(d.frost_point_c, 1));
  setText('th-vpd', fmt(d.vapor_pressure_deficit_kpa, 2));
  setText('th-mixing', fmt(d.mixing_ratio_g_kg, 1));
  setText('th-specific', fmt(d.specific_humidity_g_kg, 1));
  setText('th-vp', fmt(d.vapor_pressure_hpa, 1));
  setText('th-svp', fmt(d.saturation_vapor_pressure_hpa, 1));
  setText('th-density', fmt(d.air_density_kg_m3, 3));
  setText('th-densalt', fmtInt(d.density_altitude_ft));
  setText('th-pressalt', fmtInt(d.pressure_altitude_ft));
  setText('th-cloudbase', fmtInt(d.cloud_base_ft));
}

function applySky(sr) {
  const sky = sr?.derived?.sky;
  if (!sky) {
    ['sky-uv', 'sky-cloud', 'sky-irr', 'sky-sunalt'].forEach(id => setText(id, '--'));
    setText('sky-condition', '--');
    return;
  }
  setText('sky-uv', fmt(sky.uv_index_estimate, 1));
  setText('sky-cloud', (sky.cloud_cover_pct !== null && sky.cloud_cover_pct !== undefined)
    ? fmt(sky.cloud_cover_pct, 0) : '--');
  setText('sky-irr', fmtInt(sky.solar_irradiance_w_m2));
  setText('sky-sunalt', fmt(sky.sun_altitude_deg, 0));
  setText('sky-condition', sky.sky_condition ?? '--');
}

function ageLabel(s) {
  if (s === null || s === undefined) return '--';
  if (s < 90) return `${Math.round(s)}s AGO`;
  return `${fmtDuration(s)} AGO`;
}

function tzOffsetLabel() {
  try {
    const now = new Date();
    const parts = new Intl.DateTimeFormat('en-US', { timeZoneName: 'shortOffset', timeZone: timezone }).formatToParts(now);
    const tzPart = parts.find(p => p.type === 'timeZoneName');
    const namePart = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short', timeZone: timezone }).formatToParts(now).find(p => p.type === 'timeZoneName');
    return `${namePart ? namePart.value : ''} · ${tzPart ? tzPart.value : ''}`;
  } catch {
    return '';
  }
}

// ─────────────────────────────────────────────────────────────────
// History + charts
// ─────────────────────────────────────────────────────────────────

function lineConfig(color, fill) {
  return {
    type: 'line',
    data: { labels: [], datasets: [{ data: [], borderColor: color, backgroundColor: fill, borderWidth: 1.5, fill: true, tension: 0.35, pointRadius: 0 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false, grid: { display: false } },
        y: {
          ticks: { color: TEXT_DIM, font: { size: 9 }, maxTicksLimit: 4 },
          grid: { color: HAIRLINE, drawBorder: false },
          border: { display: false }
        }
      }
    }
  };
}

function initCharts() {
  Chart.defaults.color = TEXT_DIM;
  Chart.defaults.borderColor = HAIRLINE;
  Chart.defaults.font.family = "'JetBrains Mono', monospace";
  Chart.defaults.font.size = 10;

  charts.temp  = new Chart($('chartTemp'),  lineConfig(AMBER, AMBER_DIM));
  charts.hum   = new Chart($('chartHum'),   lineConfig(CYAN,  CYAN_DIM));
  charts.press = new Chart($('chartPress'), lineConfig(AMBER, AMBER_DIM));
  charts.dew   = new Chart($('chartDew'),   lineConfig(CYAN,  CYAN_DIM));
  charts.vis   = new Chart($('chartVis'),   lineConfig(AMBER, AMBER_DIM));
  charts.ir    = new Chart($('chartIR'),    lineConfig(CYAN,  CYAN_DIM));
}

async function refreshHistory() {
  let data;
  try {
    data = await fetchJson(`/api/v1/history/outdoor?hours=${currentWindowHours}&include=weather,light`);
  } catch (e) {
    console.warn('history fetch failed:', e);
    return;
  }

  const rows = data.rows || [];
  setText('hist-samples', formatHistLabel(rows.length, data.bucket_seconds || 0));
  setText('tel-records', rows.length.toLocaleString());

  const tempF = rows.map(r => cToF(r.temperature_c));
  const hum   = rows.map(r => r.humidity_pct);
  const press = rows.map(r => hpaToInHg(r.pressure_sealevel_hpa));
  const dewF  = rows.map(r => cToF(r.dewpoint_c));
  const vis   = rows.map(r => r.visible);
  const ir    = rows.map(r => r.ir);

  pushSeries(charts.temp,  tempF);
  pushSeries(charts.hum,   hum);
  pushSeries(charts.press, press);
  pushSeries(charts.dew,   dewF);
  pushSeries(charts.vis,   vis);
  pushSeries(charts.ir,    ir);

  // Update the chart-cell "current" labels from the last sample.
  const last = rows[rows.length - 1] || {};
  setText('chart-cur-temp',  last.temperature_c  !== undefined && last.temperature_c  !== null ? `${cToF(last.temperature_c).toFixed(1)} °F` : '--');
  setText('chart-cur-hum',   last.humidity_pct   !== undefined && last.humidity_pct   !== null ? `${last.humidity_pct.toFixed(1)} %` : '--');
  setText('chart-cur-press', last.pressure_sealevel_hpa !== undefined && last.pressure_sealevel_hpa !== null ? `${hpaToInHg(last.pressure_sealevel_hpa).toFixed(2)} inHg` : '--');
  setText('chart-cur-dew',   last.dewpoint_c     !== undefined && last.dewpoint_c     !== null ? `${cToF(last.dewpoint_c).toFixed(1)} °F` : '--');
  setText('chart-cur-vis',   last.visible !== undefined && last.visible !== null ? Math.round(last.visible).toLocaleString() : '--');
  setText('chart-cur-ir',    last.ir !== undefined && last.ir !== null ? Math.round(last.ir).toLocaleString() : '--');
}

function formatHistLabel(count, bucketSeconds) {
  const n = count.toLocaleString();
  if (!bucketSeconds) {
    return `${n} ${count === 1 ? 'SAMPLE' : 'SAMPLES'}`;
  }
  let avg;
  if (bucketSeconds >= 3600 && bucketSeconds % 3600 === 0) {
    const h = bucketSeconds / 3600;
    avg = `${h}-HR AVG`;
  } else if (bucketSeconds % 60 === 0) {
    const m = bucketSeconds / 60;
    avg = `${m}-MIN AVG`;
  } else {
    avg = `${bucketSeconds}-S AVG`;
  }
  return `${n} ${count === 1 ? 'BUCKET' : 'BUCKETS'} (${avg})`;
}

function pushSeries(chart, values) {
  // Filter out nulls — Chart.js can plot them as gaps, but our visual
  // expectation is solid lines, so drop nulls instead.
  const series = values.filter(v => v !== null && v !== undefined);
  chart.data.labels = series.map((_, i) => i);
  chart.data.datasets[0].data = series;
  chart.update('none');
}

function cToF(c) {
  if (c === null || c === undefined) return null;
  return c * 9 / 5 + 32;
}

function hpaToInHg(hpa) {
  if (hpa === null || hpa === undefined) return null;
  return hpa * 0.02953;
}

// ─────────────────────────────────────────────────────────────────
// Time-window selector
// ─────────────────────────────────────────────────────────────────

function wireWindowBar() {
  // Scoped to #window-bar so the summary panel's period buttons (which share
  // the .window-btn class) are not wired to the history window.
  const btns = document.querySelectorAll('#window-bar .window-btn');
  btns.forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.hours, 10) === currentWindowHours);
  });
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentWindowHours = parseInt(btn.dataset.hours, 10);
      refreshHistory();
    });
  });
}

// ─────────────────────────────────────────────────────────────────
// /api/v1/summary/outdoor — today + trends (D-HISTORY)
// ─────────────────────────────────────────────────────────────────

async function refreshSummary() {
  let data;
  try {
    data = await fetchJson(`/api/v1/summary/outdoor?period=${currentSummaryPeriod}`);
  } catch (e) {
    console.warn('summary fetch failed:', e);
    return;
  }
  applySummary(data);
}

function applySummary(s) {
  const t = s.temperature_f || {};
  const h = s.humidity_pct || {};
  setText('summary-headsub', `${(s.sample_count ?? 0).toLocaleString()} SAMPLES`);
  setText('sum-temp-hi', fmt(t.max, 0));
  setText('sum-temp-lo', fmt(t.min, 0));
  setText('sum-temp-avg', fmt(t.avg, 0));
  setText('sum-diurnal', fmt(s.diurnal_range_c, 1));
  setText('sum-hum-lo', fmt(h.min, 0));
  setText('sum-hum-hi', fmt(h.max, 0));
  setText('sum-dew', fmt(s.dewpoint_avg_c, 1));
  setText('sum-temp-trend', fmtSigned(s.temperature_trend_c_per_hour, 2));

  const trend = s.pressure_trend;
  const arrow = trend === 'rising' ? '↑' : trend === 'falling' ? '↓' : '→';
  const arrowEl = $('sum-tendency-arrow');
  if (arrowEl) {
    arrowEl.textContent = arrow;
    arrowEl.className = `tendency-arrow ${trend || 'steady'}`;
  }
  setText('sum-tendency', trend || '--');
  setText('sum-tendency-val', fmtSigned(s.pressure_tendency_hpa_3h, 2));

  const dd = [s.heating_degree_days_f, s.cooling_degree_days_f, s.growing_degree_days_f]
    .map(v => (v === null || v === undefined) ? '--' : Math.round(v)).join(' / ');
  setText('sum-dd', dd);
  setText('sum-dli', fmt(s.light_integral_mol_m2, 1));
  setText('sum-et0', fmt(s.hargreaves_et0_mm, 1));
}

function wireSummaryWindowBar() {
  const btns = document.querySelectorAll('#summary-window-bar .window-btn');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentSummaryPeriod = btn.dataset.period;
      refreshSummary();
    });
  });
}

// ─────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────

function start() {
  initCharts();
  wireWindowBar();
  wireSummaryWindowBar();
  loadBranding();          // fire-and-forget; static slots populate when it resolves
  refreshCurrent();
  refreshHistory();
  refreshSummary();
  setInterval(refreshCurrent, CURRENT_REFRESH_MS);
  setInterval(refreshHistory, HISTORY_REFRESH_MS);
  setInterval(refreshSummary, SUMMARY_REFRESH_MS);
  setInterval(renderClock, 1000);
}

document.addEventListener('DOMContentLoaded', start);
