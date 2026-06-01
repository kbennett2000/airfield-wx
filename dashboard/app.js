// airfield-wx — EFIS dashboard (Cycle 7a: local instrument panels).
//
// All numbers come from /api/v1/current (every CURRENT_REFRESH_MS); the
// airport block rides along on it. The dashboard never computes server-side
// derivations — the API is the single source of truth. 7a wires the LOCAL
// (cyan) panels + the persistent UNOFFICIAL banner; the external/METAR panels,
// offline dimming, units toggle, day/night arc, and trend chart are 7b.

const API_BASE = '';                 // same-origin
const CURRENT_REFRESH_MS = 30_000;   // /current poll cadence
const MS_TO_KT = 1.94384;            // m/s → knots (knots are fixed, aviation)
const EM = '—';                      // neutral placeholder for any null/NaN

// ─────────────────────────────────────────────────────────────────
// DOM + format helpers
// ─────────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function setLed(id, state) {
  // state: 'on' | 'warn' | 'fault' | 'off' | 'ext'
  const el = $(id);
  if (el) el.className = 'dot' + (state ? ' ' + state : '');
}

function present(n) {
  return n !== null && n !== undefined && !(typeof n === 'number' && Number.isNaN(n));
}

function fmt(n, decimals = 1) {
  return present(n) ? Number(n).toFixed(decimals) : EM;
}

function fmtInt(n) {
  return present(n) ? Math.round(Number(n)).toLocaleString() : EM;
}

function fmtSigned(n, decimals = 0) {
  if (!present(n)) return EM;
  const v = Number(n);
  return (v >= 0 ? '+' : '') + v.toFixed(decimals);
}

function escSvg(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function fetchJson(path) {
  const r = await fetch(API_BASE + path);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

// ─────────────────────────────────────────────────────────────────
// Clock — live Zulu (real UTC) + station-local, ticking every second.
// The station zone comes from the /current astronomy block.
// ─────────────────────────────────────────────────────────────────

let timezone = 'UTC';

function renderClock() {
  const now = new Date();
  const zulu = new Intl.DateTimeFormat('en-GB', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC',
  }).format(now);
  const z = $('zulu');
  if (z) z.innerHTML = `${zulu}<b>Z</b>`;
  try {
    const loc = new Intl.DateTimeFormat('en-GB', {
      hour12: false, hour: '2-digit', minute: '2-digit', timeZone: timezone,
    }).format(now);
    const tzName = new Intl.DateTimeFormat('en-US', { timeZoneName: 'short', timeZone: timezone })
      .formatToParts(now).find(p => p.type === 'timeZoneName')?.value || '';
    setText('local-clock', `LOCAL ${loc}${tzName ? ' · ' + tzName : ''}`);
  } catch {
    setText('local-clock', 'LOCAL —');
  }
}

// ─────────────────────────────────────────────────────────────────
// /api/v1/branding — load once (carried forward from Cycle 1; XSS-safe
// via textContent). Slots render as visible placeholders until filled.
// ─────────────────────────────────────────────────────────────────

let brandingCopy = { states: {}, error: { generic: '' } };

async function loadBranding() {
  let data;
  try {
    data = await fetchJson('/api/v1/branding');
  } catch (e) {
    console.warn('branding fetch failed:', e);
    return;
  }
  if (data?.browser_title?.text) document.title = data.browser_title.text;

  const rotating = Array.isArray(data?.taglines?.rotating) ? data.taglines.rotating : [];
  const pick = rotating.length > 0
    ? rotating[Math.floor(Math.random() * rotating.length)]
    : (data?.header?.tagline || '');
  if (pick) setText('branding-tagline', pick);

  if (data?.header?.title)        setText('branding-title', data.header.title);
  if (data?.header?.system_mark)  setText('branding-system-mark', data.header.system_mark);
  if (data?.footer?.text)         setText('branding-footer', data.footer.text);
  if (data?.footer?.system_line)  setText('branding-system-line', data.footer.system_line);

  brandingCopy = { states: data?.states || {}, error: data?.error || { generic: '' } };
  if (brandingCopy.states.loading) showDataStatus(brandingCopy.states.loading, false);
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
// /api/v1/current — render the local instrument panels
// ─────────────────────────────────────────────────────────────────

async function refreshCurrent() {
  let data;
  try {
    data = await fetchJson('/api/v1/current');
  } catch (e) {
    console.warn('current fetch failed:', e);
    if (brandingCopy.error?.generic) showDataStatus(brandingCopy.error.generic, true);
    return;
  }
  hideDataStatus();

  timezone = data.astronomy?.timezone || 'UTC';
  const outdoor = data.sensors?.outdoor;

  renderHeader(data);
  renderDensityAltitude(outdoor?.derived, data.airport);
  renderAltimeter(outdoor?.derived);
  renderTemp(outdoor);
  renderWindRunway(data);
  renderClock();
}

function sourceLabel(source) {
  return ({
    gps_nearest: 'auto-resolved from GPS',
    config_override: 'config override',
    manual: 'manual entry',
  })[source] || '—';
}

function renderHeader(data) {
  const outdoor = data.sensors?.outdoor;
  const airport = data.airport;

  if (airport && airport.ident) {
    const elev = present(airport.field_elevation_ft)
      ? `${Math.round(airport.field_elevation_ft).toLocaleString()} ft` : '—';
    setText('station-id', `${airport.ident}  ·  ${elev}`);
    setText('station-source', sourceLabel(airport.source));
  } else {
    setText('station-id', '—');
    setText('station-source', 'no field resolved');
  }

  setLed('led-outdoor', outdoor ? (outdoor.online ? 'on' : 'fault') : 'off');

  const hasWind = present(outdoor?.raw?.wind_speed_ms);
  setLed('led-anemometer', hasWind ? 'on' : 'off');

  const loc = outdoor?.location;
  const gpsOk = present(loc?.lat) && present(loc?.lon);
  setLed('led-gps', gpsOk ? 'on' : 'off');
  setText('led-gps-label', gpsOk ? `GPS fix · ${loc.satellites ?? '—'} sat` : 'GPS fix');
  // The Net-feed LED (ext/violet) stays at its default state — wired in 7b.
}

function renderDensityAltitude(d, airport) {
  d = d || {};
  const fieldElev = airport && present(airport.field_elevation_ft) ? airport.field_elevation_ft : null;

  setText('da', fmtInt(d.density_altitude_ft));
  setText('pa', fmtInt(d.pressure_altitude_ft));

  // ISA deviation = OAT − ISA temp at field elev (15°C − 1.9812°C per 1000 ft).
  let isaDev = null;
  if (present(d.temperature_c) && fieldElev !== null) {
    isaDev = d.temperature_c - (15 - 1.9812 * fieldElev / 1000);
  }
  setText('isa-dev', fmtSigned(isaDev, 0));

  let daAbove = null;
  if (present(d.density_altitude_ft) && fieldElev !== null) daAbove = d.density_altitude_ft - fieldElev;
  setText('da-above', fmtSigned(daAbove, 0));

  const bar = $('da-bar');
  if (bar) {
    if (present(d.density_altitude_ft)) {
      const lo = fieldElev !== null ? fieldElev : 0;
      const pct = Math.max(0, Math.min(100, ((d.density_altitude_ft - lo) / (10000 - lo)) * 100));
      bar.style.right = `${(100 - pct).toFixed(1)}%`;
    } else {
      bar.style.right = '100%';
    }
  }
  setText('da-scale-field', fieldElev !== null ? `FIELD ${Math.round(fieldElev).toLocaleString()}` : 'FIELD —');
  setText('da-scale-da', present(d.density_altitude_ft) ? `DA ${Math.round(d.density_altitude_ft).toLocaleString()}` : 'DA —');
}

function renderAltimeter(d) {
  d = d || {};
  setText('qnh', fmt(d.altimeter_setting_inhg, 2));
  setText('stp', fmt(d.pressure_station_inhg, 2));
  setText('tendency', EM);  // pressure tendency is history-derived — 7b/later
}

function renderTemp(sr) {
  const d = sr?.derived || {};
  const raw = sr?.raw || {};
  setText('oat', fmt(d.temperature_f, 1));
  setText('dew', fmt(d.dewpoint_f, 1));
  const spread = (present(d.temperature_f) && present(d.dewpoint_f)) ? d.temperature_f - d.dewpoint_f : null;
  setText('spread', fmt(spread, 1));
  setText('rh', fmt(raw.humidity_pct, 0));
}

// ─────────────────────────────────────────────────────────────────
// Wind & runway compass. Runways are drawn in the MAGNETIC frame (what a
// pilot flies); the wind arrow is converted true→magnetic with the WMM
// variation. Graceful: no wind → no arrow/favored, runways still draw;
// no airport → "no field" state.
// ─────────────────────────────────────────────────────────────────

function renderWindRunway(data) {
  const airport = data.airport;
  const outdoor = data.sensors?.outdoor;
  const raw = outdoor?.raw || {};
  const der = outdoor?.derived || {};

  const speedKt = present(raw.wind_speed_ms) ? raw.wind_speed_ms * MS_TO_KT : null;
  const gustKt = present(raw.wind_gust_ms) ? raw.wind_gust_ms * MS_TO_KT : null;
  const windTrue = present(der.wind_direction_true_deg) ? der.wind_direction_true_deg : null;
  const variation = airport && present(airport.magnetic_variation_deg) ? airport.magnetic_variation_deg : null;

  setText('wind-dir', windTrue !== null ? `${Math.round(windTrue)}°` : EM);
  setText('wind-speed', speedKt !== null ? String(Math.round(speedKt)) : EM);

  const gustEl = $('wind-gust');
  if (gustEl) {
    if (gustKt !== null) { gustEl.hidden = false; gustEl.textContent = `GUST ${Math.round(gustKt)} KT`; }
    else gustEl.hidden = true;
  }

  const sol = airport?.runway_solution;
  const favEl = $('rwy-fav');
  if (sol && sol.favored) {
    if (favEl) { favEl.hidden = false; favEl.textContent = `▸ FAVORED: RWY ${sol.favored}`; }
    const end = (sol.ends || []).find(e => e.ident === sol.favored);
    setText('hw', end && present(end.headwind_kt) ? String(Math.round(end.headwind_kt)) : EM);
    if (end && present(end.crosswind_kt)) {
      setText('xw', `${Math.round(end.crosswind_kt)}${end.crosswind_side ? ' ' + end.crosswind_side : ''}`);
    } else setText('xw', EM);
  } else {
    if (favEl) favEl.hidden = true;
    setText('hw', EM);
    setText('xw', EM);
  }

  const magnote = $('magnote');
  if (magnote) {
    if (windTrue !== null && variation !== null) {
      const mag = ((windTrue - variation) % 360 + 360) % 360;
      const vsign = variation >= 0 ? 'E' : 'W';
      magnote.innerHTML = `var <b>${Math.abs(variation).toFixed(1)}°${vsign}</b> · `
        + `${escSvg(airport.magnetic_model || 'WMM')} · wind ${Math.round(windTrue)}°T → `
        + `<b>${Math.round(mag)}°M</b>`;
    } else {
      magnote.textContent = EM;
    }
  }

  const nofield = $('nofield');
  if (nofield) nofield.hidden = !!(airport && airport.ident);

  const svg = $('compass');
  if (svg) svg.innerHTML = compassSvg(airport, windTrue, variation);
}

function compassSvg(airport, windTrue, variation) {
  const C = 90, R = 58;
  const polar = (brg, r) => {
    const a = (brg * Math.PI) / 180;
    return [C + r * Math.sin(a), C - r * Math.cos(a)];
  };

  let s = `
    <circle cx="90" cy="90" r="72" fill="none" stroke="#1b2937" stroke-width="1"/>
    <circle cx="90" cy="90" r="66" fill="none" stroke="#26394b" stroke-width="1"/>
    <g stroke="#33495c" stroke-width="1">
      <line x1="90" y1="18" x2="90" y2="26"/><line x1="162" y1="90" x2="154" y2="90"/>
      <line x1="90" y1="162" x2="90" y2="154"/><line x1="18" y1="90" x2="26" y2="90"/>
    </g>
    <g font-family="'IBM Plex Sans Condensed',sans-serif" font-size="9" fill="#6f8294" font-weight="600" text-anchor="middle">
      <text x="90" y="14">N</text><text x="170" y="93">E</text>
      <text x="90" y="176">S</text><text x="9" y="93">W</text>
    </g>`;

  const runways = (airport && Array.isArray(airport.runways)) ? airport.runways : [];
  for (const rw of runways) {
    let brg = present(rw.le_heading_mag_deg) ? rw.le_heading_mag_deg
      : (present(rw.le_heading_true_deg) && variation !== null ? rw.le_heading_true_deg - variation : null);
    if (brg === null) continue;
    const [x1, y1] = polar(brg, R);
    const [x2, y2] = polar(brg + 180, R);
    const [lx, ly] = polar(brg, R + 9);
    const [hx, hy] = polar(brg + 180, R + 9);
    s += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="#2a3a49" stroke-width="13" stroke-linecap="round"/>`;
    s += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="#4a637a" stroke-width="1.4" stroke-dasharray="4 5"/>`;
    s += `<g font-family="'JetBrains Mono',monospace" font-size="10" fill="#9fb3c4" font-weight="600" text-anchor="middle">`;
    if (rw.le_ident) s += `<text x="${lx.toFixed(1)}" y="${(ly + 3).toFixed(1)}">${escSvg(rw.le_ident)}</text>`;
    if (rw.he_ident) s += `<text x="${hx.toFixed(1)}" y="${(hy + 3).toFixed(1)}">${escSvg(rw.he_ident)}</text>`;
    s += `</g>`;
  }

  if (windTrue !== null && variation !== null) {
    const fromMag = ((windTrue - variation) % 360 + 360) % 360;
    const [tx, ty] = polar(fromMag, 64);
    const [hx, hy] = polar(fromMag, 24);
    s += `<defs><marker id="ah" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto">`
      + `<path d="M0,0 L7,3.5 L0,7 Z" fill="#2fd4cf"/></marker></defs>`;
    s += `<line x1="${tx.toFixed(1)}" y1="${ty.toFixed(1)}" x2="${hx.toFixed(1)}" y2="${hy.toFixed(1)}" stroke="#2fd4cf" stroke-width="2.4" marker-end="url(#ah)"/>`;
  }
  s += `<circle cx="90" cy="90" r="2.4" fill="#2fd4cf"/>`;
  return s;
}

// ─────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────

function start() {
  loadBranding();      // fire-and-forget; slots fill when it resolves
  renderClock();
  refreshCurrent();
  setInterval(refreshCurrent, CURRENT_REFRESH_MS);
  setInterval(renderClock, 1000);
}

document.addEventListener('DOMContentLoaded', start);
