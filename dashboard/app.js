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
// Session state (no browser storage — held in JS for the session).
//   units        : 'imperial' | 'metric'  (display-only; default imperial,
//                  since [units].system isn't surfaced by the API)
//   feedOverride : when true, treat external as offline (manual FEED toggle)
//   themeOverride: 'auto' | 'day' | 'night'  (manual SKY toggle)
// lastCurrent / lastHistory cache the last responses so the unit toggle can
// re-render with no re-fetch.
// ─────────────────────────────────────────────────────────────────

const HISTORY_REFRESH_MS = 60_000;
const SM_TO_KM = 1.60934;
const KM_TO_NM = 0.539957;

let units = 'imperial';
let feedOverride = false;
let themeOverride = 'auto';
let lastCurrent = null;
let lastHistory = null;
let trendChart = null;

const isMetric = () => units === 'metric';

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
  lastCurrent = data;
  renderAll(data);
}

// Render everything from a /current payload. Split out from the fetch so the
// units / feed / theme toggles can re-render the cached payload with no
// re-fetch. LOCAL panels render unconditionally; only the external path is
// gated by feed state — that asymmetry is the offline-first UX.
function renderAll(data) {
  if (!data) return;
  timezone = data.astronomy?.timezone || 'UTC';
  const outdoor = data.sensors?.outdoor;
  const effExternal = feedOverride ? null : data.external;

  renderHeader(data);
  renderDensityAltitude(outdoor?.derived, data.airport);
  renderAltimeter(outdoor?.derived);
  renderTemp(outdoor);
  renderWindRunway(data);
  renderExternal(effExternal, outdoor);
  renderDayNight(data.astronomy);
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
  if (isMetric()) {
    setText('qnh', fmt(d.altimeter_setting_hpa, 0)); setText('u-qnh', 'hPa');
    setText('stp', fmt(d.pressure_station_hpa, 0)); setText('u-stp', 'hPa');
  } else {
    setText('qnh', fmt(d.altimeter_setting_inhg, 2)); setText('u-qnh', 'inHg');
    setText('stp', fmt(d.pressure_station_inhg, 2)); setText('u-stp', 'inHg');
  }
  setText('tendency', EM);  // pressure tendency is history-derived — later
}

function renderTemp(sr) {
  const d = sr?.derived || {};
  const raw = sr?.raw || {};
  const tF = d.temperature_f, tC = d.temperature_c, dF = d.dewpoint_f, dC = d.dewpoint_c;
  if (isMetric()) {
    setText('oat', fmt(tC, 1)); setText('u-oat', '°C');
    setText('dew', fmt(dC, 1)); setText('u-dew', '°C');
    setText('spread', (present(tC) && present(dC)) ? fmt(tC - dC, 1) : EM); setText('u-spread', '°C');
  } else {
    setText('oat', fmt(tF, 1)); setText('u-oat', '°F');
    setText('dew', fmt(dF, 1)); setText('u-dew', '°F');
    setText('spread', (present(tF) && present(dF)) ? fmt(tF - dF, 1) : EM); setText('u-spread', '°F');
  }
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

function ageLabel(s) {
  if (!present(s)) return '— ago';
  const x = Math.round(s);
  if (x < 90) return `${x}s ago`;
  if (x < 3600) return `${Math.round(x / 60)}m ago`;
  if (x < 86400) return `${Math.round(x / 3600)}h ago`;
  return `${Math.round(x / 86400)}d ago`;
}

function hhmm(iso) {
  if (!iso) return '--:--';
  try {
    return new Intl.DateTimeFormat('en-GB', {
      hour12: false, hour: '2-digit', minute: '2-digit', timeZone: timezone,
    }).format(new Date(iso));
  } catch { return '--:--'; }
}

// ─────────────────────────────────────────────────────────────────
// External / METAR (INTERNET-sourced, violet). The ONLY panel that dims /
// shows "no feed"/stale when the feed is null or stale. Local panels are on
// a separate render path and are never touched here.
// ─────────────────────────────────────────────────────────────────

function renderExternal(ext, _outdoor) {
  const panel = $('metar-panel');
  const xchk = $('altim-xcheck');
  const ledFeed = $('led-feed');

  // Top-level feed indicator.
  let label, ledClass;
  if (!ext) { label = 'OFFLINE'; ledClass = 'off'; }
  else if (ext.stale) { label = 'STALE'; ledClass = 'warn'; }
  else { label = 'ONLINE'; ledClass = 'ext'; }
  if (ledFeed) ledFeed.className = 'dot ' + ledClass;
  setText('sw-feed', label);

  if (!ext) {
    if (panel) { panel.classList.add('offline'); panel.classList.remove('stale'); }
    setText('flight-cat', EM); if ($('flight-cat')) $('flight-cat').className = 'catbadge';
    if ($('metar-layers')) $('metar-layers').innerHTML = '';
    setText('metar-ceiling', EM);
    setText('vis', EM); setText('metar-altim', EM);
    setText('metar-src', '');
    const off = $('metar-offcopy');
    if (off) { off.hidden = false; off.textContent = brandingCopy.states?.outdoor_offline || 'No feed — internet-sourced data unavailable.'; }
    if (xchk) { xchk.textContent = 'no feed'; xchk.className = 'chip muted offline'; }
    return;
  }

  if (panel) { panel.classList.remove('offline'); panel.classList.toggle('stale', !!ext.stale); }
  if ($('metar-offcopy')) $('metar-offcopy').hidden = true;

  const cat = ext.flight_category;
  const fc = $('flight-cat');
  if (fc) { fc.textContent = cat || EM; fc.className = 'catbadge' + (cat ? ' cat-' + cat.toLowerCase() : ''); }

  const layers = Array.isArray(ext.sky_layers) ? ext.sky_layers : [];
  if ($('metar-layers')) {
    $('metar-layers').innerHTML = layers.map(l => {
      const base = present(l.base_ft_agl) ? `${Math.round(l.base_ft_agl).toLocaleString()} ft AGL` : EM;
      const isCeil = present(ext.ceiling_ft_agl) && l.base_ft_agl === ext.ceiling_ft_agl
        && ['BKN', 'OVC', 'OVX'].includes((l.cover || '').toUpperCase());
      return `<div class="layer"><span>${escSvg(l.cover || EM)}</span>`
        + `<span class="lc">${base}${isCeil ? ' ← ceiling' : ''}</span></div>`;
    }).join('');
  }
  setText('metar-ceiling', present(ext.ceiling_ft_agl)
    ? `Ceiling ${Math.round(ext.ceiling_ft_agl).toLocaleString()} ft AGL` : 'Ceiling unlimited');

  if (isMetric() && present(ext.visibility_sm)) { setText('vis', fmt(ext.visibility_sm * SM_TO_KM, 0)); setText('u-vis', 'km'); }
  else { setText('vis', fmt(ext.visibility_sm, 0)); setText('u-vis', 'SM'); }

  if (isMetric()) { setText('metar-altim', fmt(ext.altimeter_hpa, 0)); setText('u-metar-altim', 'hPa'); }
  else { setText('metar-altim', fmt(ext.altimeter_inhg, 2)); setText('u-metar-altim', 'inHg'); }

  // Mandatory attribution (ADR-0005): station · distance · age + the warning.
  const stn = ext.station_id || ext.source || 'feed';
  const distNm = present(ext.distance_km) ? `${Math.round(ext.distance_km * KM_TO_NM)} NM` : '— NM';
  if ($('metar-src')) {
    $('metar-src').innerHTML = `via <span class="ext-tag">${escSvg(stn)} · ${distNm}</span> · obs ${ageLabel(ext.age_seconds)}<br>`
      + `<span class="warn">⚠ conditions AT the reporting station — not necessarily over your field</span>`;
  }

  // Altimeter cross-check chip — internet-sourced; sits in the local Altimeter
  // panel and dims with the feed, while QNH/station stay bright.
  if (xchk) {
    if (present(ext.altimeter_diff_inhg)) {
      const diff = ext.altimeter_diff_inhg;
      xchk.textContent = `Δ ${diff >= 0 ? '+' : ''}${diff.toFixed(2)} inHg`;
      xchk.className = 'chip ' + (Math.abs(diff) <= 0.02 ? 'ok' : 'cau');
    } else { xchk.textContent = EM; xchk.className = 'chip muted'; }
  }
}

// ─────────────────────────────────────────────────────────────────
// Day / night + twilight arc — driven by live astronomy (is_daytime),
// not the clock. SKY toggle overrides the theme.
// ─────────────────────────────────────────────────────────────────

function progress(aIso, bIso) {
  const t = Date.now(), ta = new Date(aIso).getTime(), tb = new Date(bIso).getTime();
  return tb > ta ? (t - ta) / (tb - ta) : 0;
}
const clamp01 = (x) => Math.max(0, Math.min(1, x));

function renderDayNight(astro) {
  const sun = astro?.sun || null;
  const moon = astro?.moon || null;
  const isDay = themeOverride === 'auto' ? (sun?.is_daytime ?? true) : (themeOverride === 'day');

  document.body.classList.toggle('night', !isDay);
  setText('sky-title', isDay ? 'Day · Sun' : 'Night · Moon');

  const body = $('arc-body'), glow = $('arc-bodyglow'), stars = $('arc-stars');
  let bx = 160, by = 40;
  if (sun && sun.sunrise && sun.sunset) {
    const fr = clamp01(progress(sun.sunrise, sun.sunset));
    bx = 20 + fr * 280;
    by = 108 - 4 * 68 * fr * (1 - fr);  // quadratic arc matching the dashed path
  }
  if (body) {
    if (isDay) { body.setAttribute('fill', '#ffd27a'); body.setAttribute('r', '8'); body.setAttribute('cx', bx.toFixed(0)); body.setAttribute('cy', by.toFixed(0)); }
    else { body.setAttribute('fill', '#cfd9e2'); body.setAttribute('r', '6'); body.setAttribute('cx', '252'); body.setAttribute('cy', '120'); }
  }
  if (glow) { glow.setAttribute('opacity', isDay ? '0.18' : '0'); glow.setAttribute('cx', bx.toFixed(0)); glow.setAttribute('cy', by.toFixed(0)); }
  if (stars) {
    if (!isDay) {
      stars.innerHTML = [[60, 40], [120, 28], [180, 48], [240, 34], [280, 60], [150, 64], [95, 55]]
        .map(p => `<circle cx="${p[0]}" cy="${p[1]}" r="1.1" fill="#9fb3c4"/>`).join('');
      stars.setAttribute('opacity', '.8');
    } else { stars.innerHTML = ''; stars.setAttribute('opacity', '0'); }
  }

  setText('arc-rise', sun?.sunrise ? hhmm(sun.sunrise) : '--:--');
  setText('arc-set', sun?.sunset ? hhmm(sun.sunset) : '--:--');
  setText('sun-pos', sun && present(sun.azimuth_deg)
    ? `AZ ${Math.round(sun.azimuth_deg)}° · ALT ${fmt(sun.altitude_deg, 0)}°` : EM);
  setText('day-length', present(sun?.day_length_seconds) ? `${(sun.day_length_seconds / 3600).toFixed(1)}h` : EM);
  setText('twi-civil', sun?.dusk ? hhmm(sun.dusk) : (sun?.dawn ? hhmm(sun.dawn) : '—'));
  setText('twi-nautical', sun?.nautical_dusk ? hhmm(sun.nautical_dusk) : '—');
  setText('twi-astro', sun?.astronomical_dusk ? hhmm(sun.astronomical_dusk) : '—');
  setText('moon-twi', moon ? `${moon.phase_icon || '🌑'} ${fmt(moon.illumination_pct, 0)}%` : EM);
}

// ─────────────────────────────────────────────────────────────────
// Trend chart (Chart.js, vendored). Plots the LOGGED series only — OAT and
// station pressure — never a client-side derivation. Empty history → empty
// chart, no crash.
// ─────────────────────────────────────────────────────────────────

function initTrend() {
  const el = $('trend-chart');
  if (!el || typeof Chart === 'undefined') return;
  Chart.defaults.color = '#6f8294';
  Chart.defaults.font.family = "'JetBrains Mono', monospace";
  Chart.defaults.font.size = 9;
  trendChart = new Chart(el, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'OAT', data: [], borderColor: '#ffb24a', borderWidth: 2, pointRadius: 0, tension: 0.35, yAxisID: 'yT' },
        { label: 'Pressure', data: [], borderColor: '#2fd4cf', borderWidth: 2, pointRadius: 0, tension: 0.35, yAxisID: 'yP' },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false, grid: { display: false } },
        yT: { position: 'left', grid: { color: '#16222e' }, ticks: { maxTicksLimit: 4 } },
        yP: { position: 'right', grid: { display: false }, ticks: { maxTicksLimit: 4 } },
      },
    },
  });
}

async function refreshHistory() {
  let data;
  try {
    data = await fetchJson('/api/v1/history/outdoor?hours=24&include=weather');
  } catch (e) {
    console.warn('history fetch failed:', e);
    return;
  }
  lastHistory = data.rows || [];
  plotTrend();
}

function plotTrend() {
  if (!trendChart) return;
  const rows = lastHistory || [];
  const empty = $('trend-empty');
  if (empty) empty.hidden = rows.length > 0;
  trendChart.data.labels = rows.map(r => r.timestamp);
  trendChart.data.datasets[0].data = rows.map(r => (isMetric() ? r.temperature_c : r.temperature_f) ?? null);
  trendChart.data.datasets[1].data = rows.map(r =>
    present(r.pressure_station_hpa) ? (isMetric() ? r.pressure_station_hpa : r.pressure_station_hpa * 0.02953) : null);
  trendChart.update('none');
}

// ─────────────────────────────────────────────────────────────────
// Toggles (session-only; no storage). Re-render the cached payload.
// ─────────────────────────────────────────────────────────────────

function toggleFeed() {
  feedOverride = !feedOverride;
  setText('sw-feed', feedOverride ? 'OFFLINE' : 'ONLINE');
  renderAll(lastCurrent);
}

function toggleDN() {
  themeOverride = themeOverride === 'auto' ? 'night' : (themeOverride === 'night' ? 'day' : 'auto');
  setText('sw-dn', themeOverride.toUpperCase());
  renderAll(lastCurrent);
}

function toggleUnits() {
  units = isMetric() ? 'imperial' : 'metric';
  setText('sw-un', isMetric() ? 'MET' : 'IMP');
  renderAll(lastCurrent);
  plotTrend();
}

// ─────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────

function wireToggles() {
  $('sw-feed-btn')?.addEventListener('click', toggleFeed);
  $('sw-dn-btn')?.addEventListener('click', toggleDN);
  $('sw-un-btn')?.addEventListener('click', toggleUnits);
}

function start() {
  loadBranding();      // fire-and-forget; slots fill when it resolves
  renderClock();
  initTrend();
  wireToggles();
  refreshCurrent();
  refreshHistory();
  setInterval(refreshCurrent, CURRENT_REFRESH_MS);
  setInterval(refreshHistory, HISTORY_REFRESH_MS);
  setInterval(renderClock, 1000);
}

document.addEventListener('DOMContentLoaded', start);
