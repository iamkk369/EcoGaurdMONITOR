/**
 * EcoGuardAI Dashboard — app.js (Step 1: Core Dashboard)
 * SSE-first (/api/stream) with REST fallback. Real backend data only.
 */

const API_BASE =
    window.location.origin === "null" || !window.location.origin || window.location.origin.startsWith("file:")
        ? "http://localhost:8000"
        : window.location.origin;

const app = (() => {
  // ── State ─────────────────────────────────────────────────────────────────
  const state = {
    stations: [],          // latest snapshot from DashboardAgent
    stationMeta: {},       // station_id -> { location } from /api/monitoring/stations
    violations: [],        // recent violations from REST + SSE
    pollutantCounts: {},   // pollutant -> violation count
    aqiHistory: {},        // station_id -> [{ t, aqi }] live session only
    eventSource: null,
    sseConnected: false,
    backendOk: false,
    aqiChart: null,
    pollutantChart: null,
  };

  const MAX_LIVE_POINTS = 60;
  const MAX_VIOLATIONS = 50;

  Object.assign(state, {
    units: null,           // station registry from /api/monitoring/stations
    unitsAt: 0,
    readingsCache: {},     // station_id -> { at, rows }
    recentAlerts: [],      // alerts seen via SSE snapshot/violation events
    currentPage: 'home',
  });

  const $ = (id) => document.getElementById(id);
  const setText = (id, v) => { const el = $(id); if (el) el.textContent = v; };

  function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ── Clock ─────────────────────────────────────────────────────────────────
  function startClock() {
    const tick = () => setText('clockDisplay', new Date().toLocaleTimeString());
    tick();
    setInterval(tick, 1000);
  }

  // ── Connection status UI (driven by real SSE/REST state) ──────────────────
  function setConnectionStatus(mode) {
    // mode: 'connected' | 'connecting' | 'error'
    [$('liveDot'), $('sidebarConnDot')].forEach(d => { if (d) d.className = 'dot dot--' + mode; });

    const label = mode === 'connected' ? 'LIVE DATA' : mode === 'error' ? 'OFFLINE' : 'CONNECTING';
    setText('liveText', label);
    setText('sidebarConnText',
      mode === 'connected' ? 'SSE connected' :
      mode === 'error' ? 'Disconnected — retrying…' : 'Connecting…');

    state.sseConnected = mode === 'connected';
    updateBanner();
  }

  function updateBanner() {
    const banner = $('connBanner');
    if (!banner) return;
    if (!state.backendOk && !state.sseConnected) {
      banner.hidden = false;
      setText('connBannerText', 'Backend unavailable — waiting for live data…');
    } else {
      banner.hidden = true;
    }
  }

  // ── Safe fetch wrapper (handles 429/404/network/malformed) ────────────────
  async function safeFetch(url) {
    let res;
    try {
      res = await fetch(url);
    } catch (err) {
      throw Object.assign(new Error('Network error'), { status: 0 });
    }
    if (res.status === 429) throw Object.assign(new Error('Rate limited'), { status: 429 });
    if (res.status === 404) throw Object.assign(new Error('Not found'), { status: 404 });
    if (!res.ok) throw Object.assign(new Error('HTTP ' + res.status), { status: res.status });
    try {
      return await res.json();
    } catch {
      throw Object.assign(new Error('Malformed JSON'), { status: res.status });
    }
  }

  // ── SSE ───────────────────────────────────────────────────────────────────
  function connectSSE() {
    if (state.eventSource) state.eventSource.close();
    let es;
    try {
      es = new EventSource(API_BASE + '/api/stream');
    } catch (e) {
      setConnectionStatus('error');
      setTimeout(connectSSE, 10000);
      return;
    }
    state.eventSource = es;

    es.onopen = () => setConnectionStatus('connected');
    es.onerror = () => {
      setConnectionStatus('error');
      if (es.readyState === EventSource.CLOSED) setTimeout(connectSSE, 10000);
    };

    es.onmessage = (event) => {
      if (!event.data) return;
      try {
        handleMessage(JSON.parse(event.data));
      } catch {
        // keepalive / malformed frame — ignore, never crash
      }
    };
  }

  function handleMessage(msg) {
    if (!msg || typeof msg !== 'object' || !msg.data || typeof msg.data !== 'object') return;
    const { type, data } = msg;
    if (type === 'snapshot' || type === 'stats.update' || type === 'dashboard.update') {
      if (Array.isArray(data.recent_alerts)) {
        state.recentAlerts = data.recent_alerts.slice(-20);
        renderAlertsFeed();
      }
      applySnapshot(data);
      appendLiveEvent('Dashboard snapshot updated', 'info');
    } else if (type === 'violation.new') {
      addViolation(data);
      const sev = severityClass(data.severity);
      state.recentAlerts.push({
        station_id: data.station_id,
        pollutant: data.pollutant_type,
        severity: data.severity || 'LOW',
        timestamp: data.timestamp || new Date().toISOString(),
      });
      if (state.recentAlerts.length > 20) state.recentAlerts.shift();
      renderAlertsFeed();
      appendLiveEvent(
        'Violation at ' + (data.station_id || '?') + ': ' + (data.pollutant_type || '?') +
        ' = ' + (data.measured_value != null ? data.measured_value : '?') + ' ' + (data.unit || '') +
        ' (' + sev + ')', sev.toLowerCase());
    }
  }

  function appendLiveEvent(text, kind) {
    const feed = $('liveEventFeed');
    if (!feed) return;
    const empty = feed.querySelector('.feed-empty');
    if (empty) empty.remove();
    const item = document.createElement('div');
    item.className = 'feed-item feed-item--' + (kind || 'low');
    const time = new Date().toLocaleTimeString();
    item.innerHTML = '<div class="feed-body"><strong>' + esc(time) + '</strong> — ' + esc(text) + '</div>';
    feed.insertBefore(item, feed.firstChild);
    while (feed.children.length > 25) feed.removeChild(feed.lastChild);
  }

  function renderAlertsFeed() {
    const feed = $('alertsFeed');
    setText('pageAlertCount', String(state.recentAlerts.length));
    setText('alertCountDash', String(state.recentAlerts.length));
    setText('notifCount', String(state.recentAlerts.length));
    const markup = state.recentAlerts.length
      ? state.recentAlerts.slice().reverse().map(a => {
          const sev = severityClass(a.severity);
          const ts = a.timestamp && !isNaN(new Date(a.timestamp)) ? new Date(a.timestamp).toLocaleString() : '—';
          return '<div class="feed-item feed-item--' + sev.toLowerCase() + '">' +
            '<div class="feed-item-header"><span class="feed-station">' + esc(a.station_id || '—') + '</span>' +
            '<span class="sev sev-' + sev + '">' + sev + '</span></div>' +
            '<div class="feed-body">' + esc(a.pollutant || '') + ' · ' + ts + '</div></div>';
        }).join('')
      : '<p class="feed-empty">No alerts recorded yet.</p>';
    if (feed) feed.innerHTML = markup;
    const dash = $('alertsDash');
    if (dash) dash.innerHTML = markup;
  }


  // ── Snapshot rendering (KPIs, table, charts) ──────────────────────────────
  function applySnapshot(snapshot) {
    state.backendOk = true;
    updateBanner();

    const stations = Array.isArray(snapshot.stations) ? snapshot.stations : [];
    state.stations = stations;

    setText('kpiStations', String(stations.length));
    setText('kpiViolations', snapshot.total_active_violations != null ? String(snapshot.total_active_violations) : '—');
    setText('kpiCompliance',
      typeof snapshot.overall_compliance_rate === 'number'
        ? snapshot.overall_compliance_rate.toFixed(1) + '%' : '—');

    const maxAqi = stations.reduce((m, s) => Math.max(m, Number(s.aqi) || 0), 0);
    setText('kpiMaxAqi', stations.length ? String(Math.round(maxAqi)) : '—');

    const totalAlerts = Array.isArray(snapshot.recent_alerts) ? snapshot.recent_alerts.length : 0;
    setText('kpiAlerts', String(totalAlerts));

    const highRisk = stations.filter(s => (Number(s.aqi) || 0) > 200).length;
    setText('kpiRiskZones', String(highRisk));

    const hs = statusBand(maxAqi);
    setText('healthSnapAqi', stations.length ? String(Math.round(maxAqi)) : '—');
    setText('healthSnapStatus', hs.label);
    setText('healthSnapBadge', hs.label);

    const upd = snapshot.updated_at ? new Date(snapshot.updated_at) : null;
    setText('lastUpdateLabel', upd && !isNaN(upd) ? 'Updated ' + upd.toLocaleTimeString() : '');

    renderStationTable(stations);
    renderAirSummary(stations);

    // Track live AQI history for the trend chart (session only — no fabrication)
    const now = new Date().toLocaleTimeString();
    stations.forEach(s => {
      if (!state.aqiHistory[s.station_id]) state.aqiHistory[s.station_id] = [];
      const h = state.aqiHistory[s.station_id];
      h.push({ t: now, aqi: Number(s.aqi) || 0 });
      if (h.length > MAX_LIVE_POINTS) h.shift();
    });
    updateTrendChart();
  }

  // ── Station table ─────────────────────────────────────────────────────────
  function statusBand(aqi) {
    if (aqi <= 50)  return { cls: 'st-good',      label: 'Good' };
    if (aqi <= 100) return { cls: 'st-moderate',  label: 'Moderate' };
    if (aqi <= 200) return { cls: 'st-unhealthy', label: 'Unhealthy' };
    if (aqi <= 300) return { cls: 'st-highrisk',  label: 'High Risk' };
    return                { cls: 'st-critical',   label: 'Critical' };
  }

  function stationLocation(stationId) {
    if (state.stationMeta[stationId] && state.stationMeta[stationId].location)
      return state.stationMeta[stationId].location;
    const prefix = String(stationId).split('-')[0].toUpperCase();
    const map = { VAPI: 'Vapi', VATVA: 'Vatva', ANKL: 'Ankleshwar', BHARUCH: 'Bharuch', SACHIN: 'Sachin' };
    return map[prefix] || prefix;
  }

  function renderStationTable(stations) {
    const tbody = $('stationTableBody');
    if (!tbody) return;
    if (!stations.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-row">Waiting for live data…</td></tr>';
      return;
    }

    const sel = $('trendStationSelect');
    const existing = new Set([...sel.options].map(o => o.value));
    stations.forEach(s => {
      if (!existing.has(s.station_id)) {
        const opt = document.createElement('option');
        opt.value = s.station_id;
        opt.textContent = s.station_id;
        sel.appendChild(opt);
      }
    });

    tbody.innerHTML = stations.map(s => {
      const aqi = Number(s.aqi) || 0;
      const band = statusBand(aqi);
      const viol = s.active_violations != null ? s.active_violations : 0;
      return '<tr>' +
        '<td><strong>' + esc(s.station_id) + '</strong></td>' +
        '<td>' + esc(stationLocation(s.station_id)) + '</td>' +
        '<td class="' + band.cls + '"><strong>' + aqi.toFixed(0) + '</strong></td>' +
        '<td>' + esc(s.aqi_category || '—') + '</td>' +
        '<td><span class="badge' + (viol > 0 ? ' badge--red' : '') + '">' + viol + '</span></td>' +
        '<td>' + (typeof s.compliance_rate === 'number' ? s.compliance_rate.toFixed(1) + '%' : '—') + '</td>' +
        '<td><span class="status-badge ' + band.cls + '">' + band.label + '</span></td>' +
        '<td>' + (s.total_readings != null ? Number(s.total_readings).toLocaleString() : '—') + '</td>' +
        '</tr>';
    }).join('');
  }

  function renderAirSummary(stations) {
    if (!stations.length) {
      ['airAqi', 'airStatus', 'airPollutants'].forEach(id => setText(id, '—'));
      return;
    }
    const worst = stations.reduce((a, b) => ((Number(b.aqi) || 0) >= (Number(a.aqi) || 0) ? b : a));
    setText('airAqi', String(Math.round(Number(worst.aqi) || 0)));
    setText('airStatus', statusBand(Number(worst.aqi) || 0).label);
    const top = Object.entries(state.pollutantCounts)
      .sort((a, b) => b[1] - a[1]).slice(0, 3).map(e => e[0]);
    setText('airPollutants', top.length ? top.join(', ') : '—');
  }

  // ── Violations (REST + SSE) ───────────────────────────────────────────────
  function addViolation(v) {
    if (!v || typeof v !== 'object') return;
    // Deduplicate against REST-loaded violations by id when available
    if (v.id != null && state.violations.some(x => x.id === v.id)) return;
    state.violations.unshift(v);
    if (state.violations.length > MAX_VIOLATIONS) state.violations.pop();

    const p = v.pollutant_type || 'UNKNOWN';
    state.pollutantCounts[p] = (state.pollutantCounts[p] || 0) + 1;

    renderViolationTable();
    updatePollutantChart();
    renderAirSummary(state.stations);
  }

  function severityClass(sev) {
    const s = String(sev || 'LOW').toUpperCase();
    return ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].includes(s) ? s : 'LOW';
  }

  function renderViolationTable() {
    const tbody = $('violationTableBody');
    if (!tbody) return;
    setText('violationCount', String(state.violations.length));
    if (!state.violations.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-row">No violations detected.</td></tr>';
      return;
    }
    tbody.innerHTML = state.violations.slice(0, 20).map(v => {
      const sev = severityClass(v.severity);
      const ts = v.timestamp && !isNaN(new Date(v.timestamp))
        ? new Date(v.timestamp).toLocaleString() : '—';
      const mv = v.measured_value != null ? v.measured_value : '—';
      const lv = v.limit_value != null ? v.limit_value : '—';
      const unit = v.unit ? ' ' + esc(v.unit) : '';
      return '<tr>' +
        '<td><strong>' + esc(v.station_id || '—') + '</strong></td>' +
        '<td>' + esc(v.pollutant_type || '—') + '</td>' +
        '<td>' + esc(mv) + unit + '</td>' +
        '<td>' + esc(lv) + unit + '</td>' +
        '<td><span class="sev sev-' + sev + '">' + sev + '</span></td>' +
        '<td>' + ts + '</td>' +
        '</tr>';
    }).join('');
  }

  // Derive water quality values ONLY from actual backend violation records.
  function renderWaterSummary() {
    const latest = {};
    state.violations.forEach(v => {
      const p = String(v.pollutant_type || '').toUpperCase();
      if (['BOD', 'COD', 'TSS', 'TDS', 'PH'].includes(p) && v.measured_value != null && !latest[p]) {
        latest[p] = { val: v.measured_value, unit: v.unit };
      }
    });
    if (latest.BOD) setText('wqBod', latest.BOD.val + (latest.BOD.unit ? ' ' + latest.BOD.unit : ''));
    if (latest.COD) setText('wqCod', latest.COD.val + (latest.COD.unit ? ' ' + latest.COD.unit : ''));
    if (latest.TDS) setText('wqTds', latest.TDS.val + (latest.TDS.unit ? ' ' + latest.TDS.unit : ''));
    if (latest.TSS) setText('wqTss', latest.TSS.val + (latest.TSS.unit ? ' ' + latest.TSS.unit : ''));
    if (latest.PH)  setText('wqPh', String(latest.PH.val));
  }

  // ── AQI Trend Chart ───────────────────────────────────────────────────────
  function initTrendChart() {
    const ctx = $('aqiTrendChart').getContext('2d');
    state.aqiChart = new Chart(ctx, {
      type: 'line',
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        scales: {
          x: { ticks: { color: '#66736B', maxTicksLimit: 8 }, grid: { color: '#F0F0F0' } },
          y: { ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' }, min: 0, suggestedMax: 300 },
        },
        plugins: { legend: { labels: { color: '#17201B', font: { size: 11 } } } },
      },
    });
  }

  function updateTrendChart() {
    if (!state.aqiChart) return;
    const selStation = $('trendStationSelect') ? $('trendStationSelect').value : '';
    const range = $('trendRangeSelect') ? $('trendRangeSelect').value : 'live';
    const note = $('rangeNote');

    // Backend exposes no historical AQI endpoint yet — non-live ranges show a
    // clear notice instead of fabricated data.
    if (note) note.hidden = range === 'live';
    if (range !== 'live') {
      state.aqiChart.data.labels = [];
      state.aqiChart.data.datasets = [];
      state.aqiChart.update('none');
      return;
    }

    let labels = [];
    let datasets = [];

    if (!selStation) {
      const allStations = Object.keys(state.aqiHistory);
      if (!allStations.length) return;
      labels = state.aqiHistory[allStations[0]].map(p => p.t);
      const maxLen = Math.max(...allStations.map(s => state.aqiHistory[s].length));
      const avgData = [];
      for (let i = 0; i < maxLen; i++) {
        const vals = allStations.map(s => state.aqiHistory[s][i] && state.aqiHistory[s][i].aqi)
          .filter(v => v !== undefined);
        avgData.push(vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null);
      }
      datasets = [{
        label: 'Avg AQI (all stations)',
        data: avgData,
        borderColor: '#3fb950',
        backgroundColor: 'rgba(63,185,80,0.08)',
        fill: true,
        tension: 0.35,
        pointRadius: 2,
      }];
    } else {
      const history = state.aqiHistory[selStation] || [];
      labels = history.map(p => p.t);
      datasets = [{
        label: selStation,
        data: history.map(p => p.aqi),
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.08)',
        fill: true,
        tension: 0.35,
        pointRadius: 2,
      }];
    }

    state.aqiChart.data.labels = labels;
    state.aqiChart.data.datasets = datasets;
    state.aqiChart.update('none');
  }

  // ══ STEP 2: shared data layer ═══════════════════════════════════════════
  // Static regulatory reference bundled with EcoGuardAI (data/pollutant_limits.json).
  const POLLUTANT_LIMITS = {
    SO2:{limit:80,unit:'µg/m³',std:'NAAQS 2009',desc:'Sulphur Dioxide',concern:'Respiratory irritation; aggravates asthma and bronchitis.'},
    NOX:{limit:80,unit:'µg/m³',std:'NAAQS 2009',desc:'Oxides of Nitrogen',concern:'Smog precursor; inflames airways, worsens lung disease.'},
    PM25:{limit:60,unit:'µg/m³',std:'NAAQS 2009',desc:'Particulate Matter < 2.5 µm',concern:'Fine particles penetrate deep into lungs and bloodstream.'},
    PM10:{limit:100,unit:'µg/m³',std:'NAAQS 2009',desc:'Particulate Matter < 10 µm',concern:'Coarse particles irritate airways and reduce lung function.'},
    CO:{limit:4000,unit:'µg/m³',std:'NAAQS 2009',desc:'Carbon Monoxide',concern:'Reduces oxygen delivery to organs and tissues.'},
    O3:{limit:100,unit:'µg/m³',std:'NAAQS 2009',desc:'Ozone',concern:'Ground-level ozone triggers breathing problems.'},
    NH3:{limit:400,unit:'µg/m³',std:'NAAQS 2009',desc:'Ammonia',concern:'Irritant to eyes, throat and respiratory tract.'},
    BENZENE:{limit:5,unit:'µg/m³',std:'NAAQS 2009',desc:'Benzene',concern:'Long-term exposure is linked to blood disorders.'},
    PH:{min:6.5,max:8.5,limit:8.5,unit:'pH',std:'IS:10500',desc:'pH Level',concern:'Outside 6.5–8.5 harms aquatic life and water usability.'},
    BOD:{limit:30,unit:'mg/L',std:'CPCB Effluent',desc:'Biological Oxygen Demand',concern:'High BOD depletes dissolved oxygen in rivers.'},
    COD:{limit:250,unit:'mg/L',std:'CPCB Effluent',desc:'Chemical Oxygen Demand',concern:'Indicator of organic pollution load in effluent.'},
    TSS:{limit:100,unit:'mg/L',std:'CPCB Effluent',desc:'Total Suspended Solids',concern:'Clogs waterways, reduces light for aquatic plants.'},
    TDS:{limit:2100,unit:'mg/L',std:'IS:10500',desc:'Total Dissolved Solids',concern:'Excess TDS affects taste and water potability.'},
    FLUORIDE:{limit:2.0,unit:'mg/L',std:'IS:10500',desc:'Fluoride',concern:'Excess fluoride affects teeth and bones over time.'},
    PB:{limit:0.05,unit:'mg/L',std:'IS:10500',desc:'Lead',concern:'Neurotoxic metal; especially harmful to children.'},
    CR:{limit:0.1,unit:'mg/L',std:'IS:10500',desc:'Chromium (Total)',concern:'Toxic to skin, kidneys and liver at high exposure.'},
    CR6:{limit:0.05,unit:'mg/L',std:'IS:10500',desc:'Hexavalent Chromium',concern:'Carcinogenic form of chromium.'},
    HG:{limit:0.001,unit:'mg/L',std:'IS:10500',desc:'Mercury',concern:'Accumulates in food chain; damages nervous system.'},
    AS:{limit:0.05,unit:'mg/L',std:'IS:10500',desc:'Arsenic',concern:'Chronic exposure linked to skin and organ damage.'},
    CD:{limit:0.01,unit:'mg/L',std:'IS:10500',desc:'Cadmium',concern:'Kidney damage and bone weakening at chronic doses.'},
    NI:{limit:0.2,unit:'mg/L',std:'IS:10500',desc:'Nickel',concern:'Skin sensitisation; possible carcinogen.'},
    ZN:{limit:5.0,unit:'mg/L',std:'IS:10500',desc:'Zinc',concern:'Excess zinc impairs water taste and aquatic life.'},
    CU:{limit:3.0,unit:'mg/L',std:'IS:10500',desc:'Copper',concern:'Elevated levels cause gastrointestinal irritation.'},
    FE:{limit:3.0,unit:'mg/L',std:'IS:10500',desc:'Iron',concern:'Staining, taste issues; indicates corrosion.'},
  };
  const WATER_POLLUTANTS = ['PH','BOD','COD','TSS','TDS','FLUORIDE'];

  function limitFor(pollutant) {
    return POLLUTANT_LIMITS[String(pollutant || '').toUpperCase()] || null;
  }

  // Status of a single reading vs its permitted limit (frontend presentation only).
  function readingStatus(pollutant, value) {
    if (value == null || isNaN(Number(value))) return { label: 'No data', cls: 'st-moderate' };
    const ref = limitFor(pollutant);
    const v = Number(value);
    if (!ref || ref.limit == null) return { label: 'Monitored', cls: 'st-good' };
    let ratio;
    if (ref.min != null && ref.max != null) {           // range parameter e.g. pH
      ratio = v >= ref.min && v <= ref.max ? 0 : Math.abs(v - (ref.min + ref.max) / 2) / ((ref.max - ref.min) / 2);
    } else {
      ratio = v / ref.limit;
    }
    if (ratio <= 1)   return { label: 'Within Limit', cls: 'st-good' };
    if (ratio <= 1.5) return { label: 'Low Exceedance', cls: 'st-moderate' };
    if (ratio <= 2)   return { label: 'Unhealthy', cls: 'st-unhealthy' };
    return               { label: 'Critical', cls: 'st-critical' };
  }

  function fmtVal(v, unit) {
    if (v == null || isNaN(Number(v))) return '—';
    return Number(v) + (unit ? ' ' + unit : '');
  }


  // Cached registry fetch — /api/monitoring/stations (60s TTL)
  async function ensureUnits(force) {
    if (!force && state.units && Date.now() - state.unitsAt < 60000) return state.units;
    try {
      const units = await safeFetch(API_BASE + '/api/monitoring/stations');
      if (Array.isArray(units)) { state.units = units; state.unitsAt = Date.now(); }
    } catch (err) {
      console.warn('stations registry unavailable (HTTP ' + (err.status || '?') + ')');
    }
    return state.units || [];
  }

  // Cached readings fetch — /api/monitoring/stations/{id}/readings (60s TTL)
  async function getStationReadings(stationId, force) {
    const cached = state.readingsCache[stationId];
    if (!force && cached && Date.now() - cached.at < 60000) return cached.rows;
    try {
      const rows = await safeFetch(API_BASE + '/api/monitoring/stations/' + encodeURIComponent(stationId) + '/readings?limit=100');
      if (Array.isArray(rows)) {
        state.readingsCache[stationId] = { at: Date.now(), rows: rows };
        return rows;
      }
      return [];
    } catch (err) {
      console.warn('readings unavailable for ' + stationId + ' (HTTP ' + (err.status || '?') + ')');
      return cached ? cached.rows : [];
    }
  }

  function fillSelect(sel, items, allLabel) {
    if (!sel) return;
    const prev = sel.value;
    sel.innerHTML = '<option value="">' + (allLabel || 'All Stations') + '</option>';
    items.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.value;
      opt.textContent = s.label;
      sel.appendChild(opt);
    });
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  }

  function stationOptions(units) {
    return units.map(u => ({ value: u.name, label: u.name + (u.location ? ' — ' + u.location : '') }));
  }

  async function fetchStationsMeta() {
    try {
      const units = await safeFetch(API_BASE + '/api/monitoring/stations');
      if (Array.isArray(units)) {
        units.forEach(u => {
          if (u && u.name) state.stationMeta[u.name] = { location: u.location || null };
        });
        renderStationTable(state.stations);
      }
    } catch (err) {
      console.warn('stations meta unavailable (HTTP ' + (err.status || '?') + ') — using station IDs');
    }
  }

  async function fetchViolations() {
    try {
      const data = await safeFetch(API_BASE + '/api/violations?limit=50');
      if (Array.isArray(data)) {
        state.violations = data;
        data.forEach(v => {
          const p = v.pollutant_type || 'UNKNOWN';
          state.pollutantCounts[p] = (state.pollutantCounts[p] || 0) + 1;
        });
        renderViolationTable();
        updatePollutantChart();
        renderAirSummary(state.stations);
      }
    } catch (err) {
      if (err.status === 429) console.warn('Violations fetch rate-limited — will retry later');
      else if (err.status === 404) console.warn('Violations endpoint not found');
      else console.warn('Violations fetch failed:', err.message);
    }
  }

  async function refreshAll() {
    try {
      const aqiData = await safeFetch(API_BASE + '/api/monitoring/aqi');
      state.backendOk = true;
      updateBanner();
      if (aqiData && Array.isArray(aqiData.stations) && !state.sseConnected) {
        applySnapshot({
          stations: aqiData.stations.map(s => ({
            station_id: s.station_id,
            aqi: s.aqi,
            aqi_category: s.category,
            active_violations: null,
            compliance_rate: null,
            total_readings: null,
          })),
          updated_at: aqiData.updated_at,
        });
      }
    } catch (err) {
      if (!state.sseConnected) {
        state.backendOk = false;
        updateBanner();
        setConnectionStatus('error');
      }
      console.warn('AQI REST refresh failed (HTTP ' + (err.status || '?') + ')');
      return; // don't pile on more requests while backend is unreachable/rate-limited
    }
    await Promise.all([fetchViolations(), fetchStationsMeta()]);
  }

  // ── Pollutant Breakdown Chart ─────────────────────────────────────────────
  function initPollutantChart() {
    const ctx = $('pollutantChart').getContext('2d');
    state.pollutantChart = new Chart(ctx, {
      type: 'bar',
      data: { labels: [], datasets: [{ label: 'Violations', data: [], backgroundColor: [] }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        scales: {
          x: { ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' } },
          y: { ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' }, beginAtZero: true },
        },
        plugins: { legend: { display: false } },
      },
    });
  }

  function updatePollutantChart() {
    if (!state.pollutantChart) return;
    const entries = Object.entries(state.pollutantCounts)
      .sort((a, b) => b[1] - a[1]).slice(0, 10);
    const palette = ['#f85149', '#f0883e', '#d29922', '#58a6ff', '#3fb950', '#bc8cff', '#79c0ff', '#ffa657'];
    state.pollutantChart.data.labels = entries.map(e => e[0]);
    state.pollutantChart.data.datasets[0].data = entries.map(e => e[1]);
    state.pollutantChart.data.datasets[0].backgroundColor = entries.map((_, i) => palette[i % palette.length]);
    state.pollutantChart.update('none');
    renderWaterSummary();
  }

  // ══ STEP 2: page loaders ════════════════════════════════════════════════
  function renderLiveRows(rows) {
    const tbody = $('liveAirBody');
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-row">No readings available from backend for this station.</td></tr>';
      return;
    }
    // Latest reading per pollutant
    const latest = {};
    rows.forEach(r => { if (!latest[r.pollutant_type]) latest[r.pollutant_type] = r; });
    tbody.innerHTML = Object.values(latest).map(r => {
      const st = readingStatus(r.pollutant_type, r.value);
      const ref = limitFor(r.pollutant_type);
      const lim = ref ? fmtVal(ref.limit, ref.unit) : '—';
      const ts = r.timestamp && !isNaN(new Date(r.timestamp)) ? new Date(r.timestamp).toLocaleString() : '—';
      return '<tr>' +
        '<td><strong>' + esc((ref && ref.desc) || r.pollutant_type || '—') + '</strong> <span class="muted-sm">' + esc(r.pollutant_type || '') + '</span></td>' +
        '<td class="' + st.cls + '"><strong>' + esc(fmtVal(r.value, null)) + '</strong> ' + esc(r.unit || '') + '</td>' +
        '<td>' + esc(r.unit || '—') + '</td>' +
        '<td>' + lim + '</td>' +
        '<td><span class="status-badge ' + st.cls + '">' + st.label + '</span></td>' +
        '<td>' + ts + '</td>' +
        '</tr>';
    }).join('');
  }

  // Render reading rows for a 7-col table (station, pollutant, value, unit, limit, %, status)
  function renderReadingRows(tbodyId, rows) {
    const tbody = $(tbodyId);
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-row">No readings available from backend.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => {
      const st = readingStatus(r.pollutant_type, r.value);
      const ref = limitFor(r.pollutant_type);
      const lim = ref ? fmtVal(ref.limit, ref.unit) : '—';
      const pct = (ref && ref.limit && r.value != null && !isNaN(Number(r.value)))
        ? Math.round((Number(r.value) / ref.limit) * 100) + '%' : '—';
      return '<tr>' +
        '<td><strong>' + esc(r.station_id || '') + '</strong></td>' +
        '<td>' + esc((ref && ref.desc) || r.pollutant_type || '—') + '</td>' +
        '<td class="' + st.cls + '"><strong>' + esc(fmtVal(r.value, null)) + '</strong> ' + esc(r.unit || '') + '</td>' +
        '<td>' + esc(r.unit || '—') + '</td>' +
        '<td>' + lim + '</td>' +
        '<td>' + pct + '</td>' +
        '<td><span class="status-badge ' + st.cls + '">' + st.label + '</span></td>' +
        '</tr>';
    }).join('');
  }

  async function loadLivePage(force) {
    const units = await ensureUnits(force);
    fillSelect($('liveStationSelect'), stationOptions(units), 'Select station…');
    let sid = $('liveStationSelect').value;
    if (!sid && units.length) {
      $('liveStationSelect').value = units[0].name;
      sid = units[0].name;
    }
    if (!sid) {
      $('liveAirBody').innerHTML = '<tr><td colspan="6" class="empty-row">Backend unavailable — waiting for data…</td></tr>';
    } else {
      renderLiveRows(await getStationReadings(sid, force));
    }
    [$('livePageDot')].forEach(d => { if (d) d.className = 'dot ' + (state.sseConnected ? 'dot--connected' : state.backendOk ? 'dot--connecting' : 'dot--error'); });
    setText('livePageText', state.sseConnected ? 'LIVE' : state.backendOk ? 'REST ONLY' : 'OFFLINE');
  }

  async function loadAirPage(force) {
    const units = await ensureUnits(force);
    fillSelect($('airStationSelect'), stationOptions(units));
    fillSelect($('airAqiStation'), stationOptions(units));

    // KPIs from the live AQI snapshot (real DashboardAgent data)
    const stations = state.stations || [];
    if (stations.length) {
      const avg = stations.reduce((a, s) => a + (Number(s.aqi) || 0), 0) / stations.length;
      const maxS = stations.reduce((a, b) => ((Number(b.aqi) || 0) >= (Number(a.aqi) || 0) ? b : a));
      setText('airCurAqi', String(Math.round(avg)));
      setText('airMaxAqi', Math.round(Number(maxS.aqi) || 0) + ' (' + maxS.station_id + ')');
      setText('airCat', statusBand(Number(maxAqiOf(stations)) || 0).label);
    } else {
      ['airCurAqi', 'airMaxAqi', 'airCat'].forEach(id => setText(id, '—'));
    }

    // Violation count from real violation records (air pollutants only)
    const airViols = state.violations.filter(v => !WATER_POLLUTANTS.includes(String(v.pollutant_type || '').toUpperCase()));
    setText('airViolCount', String(airViols.length));

    // Readings per station (sequential to stay gentle on the rate limiter)
    const targets = $('airStationSelect').value ? [$('airStationSelect').value] : units.map(u => u.name);
    const all = [];
    for (const sid of targets) {
      const rows = await getStationReadings(sid, force);
      const seen = {};
      rows.forEach(r => {
        const p = String(r.pollutant_type || '').toUpperCase();
        if (WATER_POLLUTANTS.includes(p)) return;         // keep AIR pollutants only
        if (seen[p]) return;
        seen[p] = true;
        all.push(r);
      });
    }
    renderReadingRows('airTableBody', all);
    ensureAirCharts();
    renderAirAqiChart();
    renderAirPollutantChart();
  }

  // ── Air Quality charts (real data, lazy init) ─────────────────────────────
  function ensureAirCharts() {
    if (!state.airAqiChart) {
      const el = $('airAqiChart');
      if (!el) return;
      state.airAqiChart = new Chart(el.getContext('2d'), {
        type: 'line',
        data: { labels: [], datasets: [] },
        options: {
          responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
          scales: {
            x: { ticks: { color: '#66736B', maxTicksLimit: 8 }, grid: { color: '#F0F0F0' } },
            y: { min: 0, suggestedMax: 300, ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' } },
          },
          plugins: { legend: { labels: { color: '#17201B', font: { size: 11 } } } },
        },
      });
    }
    if (!state.airPollutantChart) {
      const el2 = $('airPollutantChart');
      if (!el2) return;
      state.airPollutantChart = new Chart(el2.getContext('2d'), {
        type: 'bar',
        data: { labels: [], datasets: [{ label: 'Violations', data: [], backgroundColor: [] }] },
        options: {
          responsive: true, maintainAspectRatio: false, animation: { duration: 300 },
          scales: {
            x: { ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' } },
            y: { beginAtZero: true, ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' } },
          },
          plugins: { legend: { display: false } },
        },
      });
    }
  }

  function renderAirAqiChart() {
    if (!state.airAqiChart) return;
    const sel = ($('airAqiStation') || {}).value || '';
    let labels = [], datasets = [];
    if (sel) {
      const hv = state.aqiHistory[sel] || [];
      labels = hv.map(p => p.t);
      datasets = [{ label: sel, data: hv.map(p => p.aqi), borderColor: '#3B82F6',
        backgroundColor: 'rgba(59,130,246,0.08)', fill: true, tension: 0.35, pointRadius: 2 }];
    } else {
      const allStations = Object.keys(state.aqiHistory);
      if (allStations.length) {
        labels = state.aqiHistory[allStations[0]].map(p => p.t);
        datasets = [{ label: 'Avg AQI (all stations)', data: state.aqiHistory[allStations[0]].map(p => p.aqi),
          borderColor: '#16A34A', backgroundColor: 'rgba(22,163,74,0.08)', fill: true, tension: 0.35, pointRadius: 2 }];
      }
    }
    state.airAqiChart.data.labels = labels;
    state.airAqiChart.data.datasets = datasets;
    state.airAqiChart.update('none');
  }

  function renderAirPollutantChart() {
    if (!state.airPollutantChart) return;
    const entries = Object.entries(state.pollutantCounts)
      .filter(e => !WATER_POLLUTANTS.includes(e[0]))
      .sort((a, b) => b[1] - a[1]).slice(0, 8);
    const palette = ['#16A34A', '#3B82F6', '#F59E0B', '#EF4444', '#0EA5E9', '#8B5CF6', '#F97316', '#14B8A6'];
    state.airPollutantChart.data.labels = entries.map(e => e[0]);
    state.airPollutantChart.data.datasets[0].data = entries.map(e => e[1]);
    state.airPollutantChart.data.datasets[0].backgroundColor = entries.map((_, i) => palette[i % palette.length]);
    state.airPollutantChart.update('none');
  }

  async function loadWaterPage(force) {
    const units = await ensureUnits(force);
    const targets = units.map(u => u.name);
    const all = [];
    for (const sid of targets) {
      const rows = await getStationReadings(sid, force);
      const seen = {};
      rows.forEach(r => {
        const p = String(r.pollutant_type || '').toUpperCase();
        if (!WATER_POLLUTANTS.includes(p)) return;        // keep WATER parameters only
        if (seen[p]) return;
        seen[p] = true;
        all.push(r);
      });
    }
    renderReadingRows('waterTableBody', all);

    setText('wqParamCount', all.length ? String(new Set(all.map(r => r.pollutant_type)).size) : '—');
    const exceed = all.filter(r => readingStatus(r.pollutant_type, r.value).cls !== 'st-good').length;
    setText('waterExceed', all.length ? String(exceed) : '—');
    setText('waterCompliant', all.length ? String(all.length - exceed) : '—');
  }

  function maxAqiOf(stations) {
    return stations.reduce((m, s) => Math.max(m, Number(s.aqi) || 0), 0);
  }


  async function loadUnitsPage(force) {
    const units = await ensureUnits(force);
    const tbody = $('unitsTableBody');
    if (!tbody) return;
    if (!units.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-row">Backend unavailable — station registry not reachable.</td></tr>';
      return;
    }
    tbody.innerHTML = units.map(u => {
      const snap = (state.stations || []).find(s => s.station_id === u.name) || {};
      const aqi = Number(snap.aqi);
      const band = statusBand(isNaN(aqi) ? 0 : aqi);
      return '<tr>' +
        '<td><strong>' + esc(u.name || '—') + '</strong></td>' +
        '<td>' + esc(u.location || '—') + '</td>' +
        '<td>' + esc(u.industry_type || '—') + '</td>' +
        '<td class="' + band.cls + '">' + (isNaN(aqi) ? '—' : aqi.toFixed(0)) + '</td>' +
        '<td><span class="badge' + ((snap.active_violations || 0) > 0 ? ' badge--red' : '') + '">' + (snap.active_violations != null ? snap.active_violations : '—') + '</span></td>' +
        '<td>' + (typeof snap.compliance_rate === 'number' ? snap.compliance_rate.toFixed(1) + '%' : '—') + '</td>' +
        '<td><span class="status-badge ' + band.cls + '">' + band.label + '</span></td>' +
        '<td>' + (snap.total_readings != null ? Number(snap.total_readings).toLocaleString() : '—') + '</td>' +
        '</tr>';
    }).join('');
  }

  // ── Pollution Map: real coordinates from /api/monitoring/stations ─────────
  let mapChart = null;

  async function loadMapPage(force) {
    const units = await ensureUnits(force);
    const geo = units.filter(u => typeof u.latitude === 'number' && typeof u.longitude === 'number');
    setText('mapCoordNote', geo.length
      ? 'Plotting real station coordinates returned by the backend (' + geo.length + ' of ' + units.length + ' stations). Coordinate plot — not a GPS street map.'
      : 'Location coordinates unavailable from the backend for these stations.');

    const tbody = $('mapTableBody');
    if (!units.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-row">Backend unavailable — station registry not reachable.</td></tr>';
      return;
    }
    tbody.innerHTML = units.map(u => {
      const snap = (state.stations || []).find(s => s.station_id === u.name) || {};
      const aqi = Number(snap.aqi) || 0;
      const band = statusBand(aqi);
      return '<tr>' +
        '<td><strong>' + esc(u.name) + '</strong></td>' +
        '<td>' + esc(u.location || '—') + '</td>' +
        '<td>' + (u.latitude != null ? u.latitude : '—') + '</td>' +
        '<td>' + (u.longitude != null ? u.longitude : '—') + '</td>' +
        '<td class="' + band.cls + '">' + aqi.toFixed(0) + '</td>' +
        '<td><span class="status-badge ' + band.cls + '">' + band.label + '</span></td>' +
        '</tr>';
    }).join('');

    if (!geo.length || !window.Chart) return;

    const pointSets = [
      { color: '#2ea043', label: 'Good',      max: 50 },
      { color: '#d29922', label: 'Moderate',  max: 100 },
      { color: '#e05c31', label: 'Unhealthy', max: 200 },
      { color: '#f85149', label: 'High Risk', max: 300 },
      { color: '#ff6e6e', label: 'Critical',  max: Infinity },
    ].map(band => ({
      label: band.label,
      backgroundColor: band.color,
      borderColor: band.color,
      pointRadius: 7,
      data: geo.filter(u => {
        const snap = (state.stations || []).find(s => s.station_id === u.name) || {};
        return (Number(snap.aqi) || 0) <= band.max;
      }).map(u => {
        const snap = (state.stations || []).find(s => s.station_id === u.name) || {};
        return { x: u.longitude, y: u.latitude, label: u.name, aqi: Math.round(Number(snap.aqi) || 0) };
      }),
    })).filter(ds => ds.data.length);

    const ctx = $('pollutionMapChart').getContext('2d');
    if (mapChart) { mapChart.destroy(); mapChart = null; }
    mapChart = new Chart(ctx, {
      type: 'scatter',
      data: { datasets: pointSets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        scales: {
          x: { title: { display: true, text: 'Longitude', color: '#66736B' }, ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' } },
          y: { title: { display: true, text: 'Latitude', color: '#66736B' }, ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' } },
        },
        plugins: {
          legend: { labels: { color: '#17201B', font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: (c) => c.raw.label + ' — AQI ' + c.raw.aqi +
                ' (lat ' + c.parsed.y + ', lon ' + c.parsed.x + ')',
            },
          },
        },
      },
    });
  }

  // ── Violations & Alerts page ──────────────────────────────────────────────
  function renderViolationsPage() {
    const sevF = ($('violSevFilter') || {}).value || '';
    const stF = ($('violStationFilter') || {}).value || '';
    const pF = ($('violPollutantFilter') || {}).value || '';
    fillSelect($('violStationFilter'), stationOptions(state.units || []));
    fillSelect($('violPollutantFilter'),
      [...new Set((state.violations || []).map(v => v.pollutant_type).filter(Boolean))]
        .sort().map(p => ({ value: p, label: p })), 'All Pollutants');

    const rows = (state.violations || []).filter(v =>
      (!sevF || String(v.severity || '').toUpperCase() === sevF) &&
      (!stF || v.station_id === stF) &&
      (!pF || v.pollutant_type === pF));

    const tbody = $('violPageBody');
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-row">No violations match the current filters.</td></tr>';
    } else {
      tbody.innerHTML = rows.map(v => {
        const sev = severityClass(v.severity);
        const ratio = (v.measured_value != null && v.limit_value)
          ? (Number(v.measured_value) / Number(v.limit_value)).toFixed(2) + '×' : '—';
        const ts = v.timestamp && !isNaN(new Date(v.timestamp)) ? new Date(v.timestamp).toLocaleString() : '—';
        const unit = v.unit ? ' ' + esc(v.unit) : '';
        return '<tr>' +
          '<td><strong>' + esc(v.station_id || '—') + '</strong></td>' +
          '<td>' + esc(v.pollutant_type || '—') + '</td>' +
          '<td>' + esc(v.measured_value != null ? v.measured_value : '—') + unit + '</td>' +
          '<td>' + esc(v.limit_value != null ? v.limit_value : '—') + unit + '</td>' +
          '<td>' + ratio + '</td>' +
          '<td><span class="sev sev-' + sev + '">' + sev + '</span></td>' +
          '<td>' + ts + '</td>' +
          '</tr>';
      }).join('');
    }

    const feed = $('alertsFeed');
    if (feed) {
      const alerts = state.recentAlerts || [];
      setText('pageAlertCount', String(alerts.length));
      feed.innerHTML = alerts.length
        ? alerts.slice().reverse().map(a => {
            const sev = severityClass(a.severity);
            const ts = a.timestamp && !isNaN(new Date(a.timestamp)) ? new Date(a.timestamp).toLocaleString() : '—';
            return '<div class="feed-item feed-item--' + sev.toLowerCase() + '">' +
              '<div class="feed-item-header"><span class="feed-station">' + esc(a.station_id || '—') + '</span>' +
              '<span class="sev sev-' + sev + '">' + sev + '</span></div>' +
              '<div class="feed-body">' + esc(a.pollutant || '') + ' · ' + ts + '</div></div>';
          }).join('')
        : '<p class="feed-empty">No alerts recorded yet.</p>';
    }
  }

  async function loadViolationsPage(force) {
    if (!state.units) await ensureUnits(force);
    await fetchViolations();       // reuses the STEP 1 loader (safeFetch, 429-safe)
    renderViolationsPage();
  }

  // ── Historical Analysis page ──────────────────────────────────────────────
  let historyChart = null;

  function renderHistoryPage() {
    const range = $('histRangeSelect') ? $('histRangeSelect').value : 'live';
    const note = $('histNote');
    if (note) note.hidden = range === 'live';

    if (range !== 'live' || !window.Chart) return;

    const selStation = $('histStationSelect') ? $('histStationSelect').value : '';
    const history = selStation ? (state.aqiHistory[selStation] || []) : null;

    let labels = [], datasets = [];
    if (history) {
      labels = history.map(p => p.t);
      datasets = [{
        label: selStation,
        data: history.map(p => p.aqi),
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.08)',
        fill: true, tension: 0.35, pointRadius: 2,
      }];
    } else {
      const allStations = Object.keys(state.aqiHistory);
      if (allStations.length) {
        labels = state.aqiHistory[allStations[0]].map(p => p.t);
        const maxLen = Math.max(...allStations.map(s => state.aqiHistory[s].length));
        const avgData = [];
        for (let i = 0; i < maxLen; i++) {
          const vals = allStations.map(s => state.aqiHistory[s][i] && state.aqiHistory[s][i].aqi)
            .filter(v => v !== undefined);
          avgData.push(vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null);
        }
        datasets = [{ label: 'Avg AQI — live session', data: avgData, borderColor: '#3fb950',
                      backgroundColor: 'rgba(63,185,80,0.08)', fill: true, tension: 0.35, pointRadius: 2 }];
      }
    }

    const ctx = $('historyChart').getContext('2d');
    if (!historyChart) {
      historyChart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [] },
        options: {
          responsive: true, maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: '#66736B', maxTicksLimit: 8 }, grid: { color: '#F0F0F0' } },
            y: { min: 0, suggestedMax: 300, ticks: { color: '#66736B' }, grid: { color: '#F0F0F0' } },
          },
          plugins: { legend: { labels: { color: '#17201B', font: { size: 11 } } } },
        },
      });
    }
    historyChart.data.labels = labels;
    historyChart.data.datasets = datasets;
    historyChart.update('none');
  }

  async function loadHistoryPage() {
    const units = await ensureUnits(false);
    fillSelect($('histStationSelect'), stationOptions(units));
    renderHistoryPage();
  }

  // ── Health Risk page (uses /api/health/risk-assessment) ───────────────────
  async function loadHealthPage(force) {
    const tbody = $('healthTableBody');
    try {
      const data = await safeFetch(API_BASE + '/api/health/risk-assessment');
      if (!data || !Array.isArray(data.assessments)) throw new Error('Malformed response');

      setText('hrOverall', data.overall_risk || '—');
      setText('hrStations', String(data.assessments.length));
      const unhealthy = data.assessments.filter(a =>
        ['UNHEALTHY', 'HAZARDOUS'].includes(String(a.risk_level || '').toUpperCase())).length;
      setText('hrUnhealthy', String(unhealthy));
      const ts = data.timestamp && !isNaN(new Date(data.timestamp)) ? new Date(data.timestamp).toLocaleTimeString() : '—';
      setText('hrUpdated', ts);

      if (!data.assessments.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-row">No risk assessments available yet.</td></tr>';
        return;
      }
      tbody.innerHTML = data.assessments.map(a => {
        const level = String(a.risk_level || '').toUpperCase();
        const cls = level === 'HAZARDOUS' ? 'st-critical' : level === 'UNHEALTHY' ? 'st-unhealthy'
          : level === 'MODERATE' ? 'st-moderate' : 'st-good';
        return '<tr>' +
          '<td><strong>' + esc(a.station_id || '—') + '</strong></td>' +
          '<td class="' + cls + '">' + (a.aqi != null ? Number(a.aqi).toFixed(0) : '—') + '</td>' +
          '<td><span class="status-badge ' + cls + '">' + esc(level || '—') + '</span></td>' +
          '<td>' + esc(a.description || '—') + '</td>' +
          '<td><span class="badge' + ((a.active_violations || 0) > 0 ? ' badge--red' : '') + '">' + (a.active_violations != null ? a.active_violations : '—') + '</span></td>' +
          '<td>' + (typeof a.compliance_rate === 'number' ? a.compliance_rate.toFixed(1) + '%' : '—') + '</td>' +
          '</tr>';
      }).join('');
    } catch (err) {
      console.warn('risk assessment unavailable (HTTP ' + (err.status || '?') + ')');
      setText('hrOverall', '—');
      if (tbody) tbody.innerHTML = err.status === 429
        ? '<tr><td colspan="6" class="empty-row">Rate limited by backend — please retry in a moment.</td></tr>'
        : '<tr><td colspan="6" class="empty-row">Risk assessment unavailable from backend.</td></tr>';
    }
  }

  // ── Harmful Pollutants page ───────────────────────────────────────────────
  async function loadPollutantsPage(force) {
    const units = await ensureUnits(force);
    const latest = {};
    for (const u of units) {
      const rows = await getStationReadings(u.name, force);
      rows.forEach(r => {
        const p = String(r.pollutant_type || '').toUpperCase();
        if (!latest[p]) latest[p] = r;
      });
    }

    const tbody = $('pollutantsBody');
    if (!tbody) return;
    const HEAVY = ['PB','CR','CR6','HG','AS','CD','NI','ZN','CU','FE'];
    const keys = Object.keys(POLLUTANT_LIMITS);
    tbody.innerHTML = keys.map(p => {
      const ref = POLLUTANT_LIMITS[p];
      const row = latest[p];
      const st = readingStatus(p, row ? row.value : null);
      const type = WATER_POLLUTANTS.includes(p) ? 'Water' : HEAVY.includes(p) ? 'Heavy Metal' : 'Air';
      return '<tr>' +
        '<td><strong>' + esc(p) + '</strong></td>' +
        '<td>' + esc(type) + '</td>' +
        '<td class="' + st.cls + '">' + (row ? esc(fmtVal(row.value, row.unit)) : '—') + '</td>' +
        '<td>' + fmtVal(ref.limit, ref.unit) + (ref.min != null ? ' (range ' + ref.min + '–' + ref.max + ')' : '') + '</td>' +
        '<td><span class="status-badge ' + st.cls + '">' + (row ? st.label : 'No data') + '</span></td>' +
        '<td class="muted-sm" style="white-space:normal;min-width:220px">' + esc(ref.concern) + '</td>' +
        '</tr>';
    }).join('');
  }

  // ── Sidebar navigation ────────────────────────────────────────────────────
  function initNav() {
    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', () => {
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        item.classList.add('active');

        const page = item.dataset.page;
        const knownPages = ['home', 'dashboard', 'live-monitoring', 'air-quality', 'water-quality',
                            'industrial-units', 'pollution-map', 'violations', 'history', 'health-risk',
                            'harmful-pollutants', 'ai-analysis', 'ecoai', 'about'];
        document.querySelectorAll('.page').forEach(p => { p.hidden = true; });
        const el = $('page-' + page);
        if (el) el.hidden = false;
        $('sidebar').classList.remove('open');

        // Lazy-load page data
        try {
          if (page === 'live-monitoring') loadLivePage();
          else if (page === 'air-quality') loadAirPage();
          else if (page === 'water-quality') loadWaterPage();
          else if (page === 'industrial-units') loadUnitsPage();
          else if (page === 'pollution-map') loadMapPage();
          else if (page === 'violations') loadViolationsPage();
          else if (page === 'history') loadHistoryPage();
          else if (page === 'health-risk') loadHealthPage();
          else if (page === 'harmful-pollutants') loadPollutantsPage();
        } catch (err) {
          console.warn('page load failed:', err);
        }
      });
    });
    $('navToggle').addEventListener('click', () => $('sidebar').classList.toggle('open'));
  }

  // ── Init ──────────────────────────────────────────────────────────────────
  function init() {
    startClock();
    initNav();
    initTrendChart();
    initPollutantChart();
    setConnectionStatus('connecting');
    connectSSE();

    // Conservative REST fallback: initial load, then every 60s only if SSE is down.
    refreshAll();
    setInterval(() => { if (!state.sseConnected) refreshAll(); }, 60000);
  }

  document.addEventListener('DOMContentLoaded', init);

  return {
    refreshAll, updateTrendChart,
    loadLivePage, loadAirPage, loadWaterPage, loadUnitsPage,
    loadMapPage, loadViolationsPage, loadHistoryPage,
    loadHealthPage, loadPollutantsPage, renderViolationsPage, renderHistoryPage,
    renderAirAqiChart,
  };
})();

