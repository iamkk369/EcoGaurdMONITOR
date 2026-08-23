/**
 * EcoGaurdMONITOR Dashboard — app.js
 * SSE-first (/api/stream) with REST fallback. Real backend data only.
 */

const API_BASE =
    window.location.origin === "null" || !window.location.origin || window.location.origin.startsWith("file:")
        ? "http://localhost:8000"
        : window.location.origin;

const app = (() => {
  // ── State ─────────────────────────────────────────────────────────────────
  const state = {
    stations: [],
    stationMeta: {},
    violations: [],
    pollutantCounts: {},
    aqiHistory: {},
    eventSource: null,
    sseConnected: false,
    backendOk: false,
    aqiChart: null,
    pollutantChart: null,
  };

  const MAX_LIVE_POINTS = 60;
  const MAX_VIOLATIONS = 50;

  Object.assign(state, {
    units: null,
    unitsAt: 0,
    readingsCache: {},
    recentAlerts: [],
    currentPage: 'home',
  });

  const $ = (id) => document.getElementById(id);
  const setText = (id, v) => { const el = $(id); if (el) el.textContent = v; };

  function esc(v) {
    return String(v ?? '').replace(/[&<>\"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function startClock() {
    const tick = () => setText('clockDisplay', new Date().toLocaleTimeString());
    tick();
    setInterval(tick, 1000);
  }

  function setConnectionStatus(mode) {
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

    const now = new Date().toLocaleTimeString();
    stations.forEach(s => {
      if (!state.aqiHistory[s.station_id]) state.aqiHistory[s.station_id] = [];
      const h = state.aqiHistory[s.station_id];
      h.push({ t: now, aqi: Number(s.aqi) || 0 });
      if (h.length > MAX_LIVE_POINTS) h.shift();
    });
    updateTrendChart();
  }

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
      tbody.innerHTML = '<tr><td colspan=\"8\" class=\"empty-row\">Waiting for live data…</td></tr>';
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
        '<td class=\"' + band.cls + '\"><strong>' + aqi.toFixed(0) + '</strong></td>' +
        '<td>' + esc(s.aqi_category || '—') + '</td>' +
        '<td><span class=\"badge' + (viol > 0 ? ' badge--red' : '') + '\">' + viol + '</span></td>' +
        '<td>' + (typeof s.compliance_rate === 'number' ? s.compliance_rate.toFixed(1) + '%' : '—') + '</td>' +
        '<td><span class=\"status-badge ' + band.cls + '\">' + band.label + '</span></td>' +
        '<td>' + (s.total_readings != null ? Number(s.total_readings).toLocaleString() : '—') + '</td>' +
        '</tr>';
    }).join('');
  }

  function addViolation(v) {
    if (!v || typeof v !== 'object') return;
    if (v.id != null && state.violations.some(x => x.id === v.id)) return;
    state.violations.unshift(v);
    if (state.violations.length > MAX_VIOLATIONS) state.violations.pop();

    const p = v.pollutant_type || 'UNKNOWN';
    state.pollutantCounts[p] = (state.pollutantCounts[p] || 0) + 1;

    updatePollutantChart();
  }

  function severityClass(sev) {
    const s = String(sev || 'LOW').toUpperCase();
    return ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].includes(s) ? s : 'LOW';
  }

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
          x: { ticks: { color: '#8B98A5', maxTicksLimit: 8 }, grid: { color: '#1B2430' } },
          y: { ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' }, min: 0, suggestedMax: 300 },
        },
        plugins: { legend: { labels: { color: '#E6EDF3', font: { size: 11 } } } },
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
        borderColor: '#22C55E',
        backgroundColor: 'rgba(34,197,94,0.10)',
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
        borderColor: '#38BDF8',
        backgroundColor: 'rgba(56,189,248,0.10)',
        fill: true,
        tension: 0.35,
        pointRadius: 2,
      }];
    }

    state.aqiChart.data.labels = labels;
    state.aqiChart.data.datasets = datasets;
    state.aqiChart.update('none');
  }

  // Static regulatory reference bundled with the project (data/pollutant_limits.json).
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

  function readingStatus(pollutant, value) {
    if (value == null || isNaN(Number(value))) return { label: 'No data', cls: 'st-moderate' };
    const ref = limitFor(pollutant);
    const v = Number(value);
    if (!ref || ref.limit == null) return { label: 'Monitored', cls: 'st-good' };
    let ratio;
    if (ref.min != null && ref.max != null) {
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
        updatePollutantChart();
        renderViolationsPage();
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
      return;
    }
    await Promise.all([fetchViolations(), fetchStationsMeta()]);
  }

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
          x: { ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' } },
          y: { ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' }, beginAtZero: true },
        },
        plugins: { legend: { display: false } },
      },
    });
  }

  function updatePollutantChart() {
    if (!state.pollutantChart) return;
    const entries = Object.entries(state.pollutantCounts)
      .sort((a, b) => b[1] - a[1]).slice(0, 10);
    const palette = ['#EF4444', '#F59E0B', '#38BDF8', '#22C55E', '#FB923C', '#0EA5E9', '#F97316', '#14B8A6'];
    state.pollutantChart.data.labels = entries.map(e => e[0]);
    state.pollutantChart.data.datasets[0].data = entries.map(e => e[1]);
    state.pollutantChart.data.datasets[0].backgroundColor = entries.map((_, i) => palette[i % palette.length]);
    state.pollutantChart.update('none');
  }

  function renderLiveRows(rows) {
    const tbody = $('liveAirBody');
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan=\"6\" class=\"empty-row\">No readings available from backend for this station.</td></tr>';
      return;
    }
    const latest = {};
    rows.forEach(r => { if (!latest[r.pollutant_type]) latest[r.pollutant_type] = r; });
    tbody.innerHTML = Object.values(latest).map(r => {
      const st = readingStatus(r.pollutant_type, r.value);
      const ref = limitFor(r.pollutant_type);
      const lim = ref ? fmtVal(ref.limit, ref.unit) : '—';
      const ts = r.timestamp && !isNaN(new Date(r.timestamp)) ? new Date(r.timestamp).toLocaleString() : '—';
      return '<tr>' +
        '<td><strong>' + esc((ref && ref.desc) || r.pollutant_type || '—') + '</strong> <span class=\"muted-sm\">' + esc(r.pollutant_type || '') + '</span></td>' +
        '<td class=\"' + st.cls + '\"><strong>' + esc(fmtVal(r.value, null)) + '</strong> ' + esc(r.unit || '') + '</td>' +
        '<td>' + esc(r.unit || '—') + '</td>' +
        '<td>' + lim + '</td>' +
        '<td><span class=\"status-badge ' + st.cls + '\">' + st.label + '</span></td>' +
        '<td>' + ts + '</td>' +
        '</tr>';
    }).join('');
  }

  function renderReadingRows(tbodyId, rows) {
    const tbody = $(tbodyId);
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan=\"7\" class=\"empty-row\">No readings available from backend.</td></tr>';
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
        '<td class=\"' + st.cls + '\"><strong>' + esc(fmtVal(r.value, null)) + '</strong> ' + esc(r.unit || '') + '</td>' +
        '<td>' + esc(r.unit || '—') + '</td>' +
        '<td>' + lim + '</td>' +
        '<td>' + pct + '</td>' +
        '<td><span class=\"status-badge ' + st.cls + '\">' + st.label + '</span></td>' +
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
      $('liveAirBody').innerHTML = '<tr><td colspan=\"6\" class=\"empty-row\">Backend unavailable — waiting for data…</td></tr>';
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

    const stations = state.stations || [];
    if (stations.length) {
      const avg = stations.reduce((a, s) => a + (Number(s.aqi) || 0), 0) / stations.length;
      const maxS = stations.reduce((a, b) => ((Number(b.aqi) || 0) >= (Number(a.aqi) || 0) ? b : a));
      setText('airCurAqi', String(Math.round(avg)));
      setText('airMaxAqi', Math.round(Number(maxS.aqi) || 0) + ' (' + maxS.station_id + ')');
      setText('airCat', statusBand(maxAqiOf(stations)).label);
    } else {
      ['airCurAqi', 'airMaxAqi', 'airCat'].forEach(id => setText(id, '—'));
    }

    const airViols = state.violations.filter(v => !WATER_POLLUTANTS.includes(String(v.pollutant_type || '').toUpperCase()));
    setText('airViolCount', String(airViols.length));

    const targets = $('airStationSelect').value ? [$('airStationSelect').value] : units.map(u => u.name);
    const all = [];
    for (const sid of targets) {
      const rows = await getStationReadings(sid, force);
      const seen = {};
      rows.forEach(r => {
        const p = String(r.pollutant_type || '').toUpperCase();
        if (WATER_POLLUTANTS.includes(p)) return;
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
            x: { ticks: { color: '#8B98A5', maxTicksLimit: 8 }, grid: { color: '#1B2430' } },
            y: { min: 0, suggestedMax: 300, ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' } },
          },
          plugins: { legend: { labels: { color: '#E6EDF3', font: { size: 11 } } } },
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
            x: { ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' } },
            y: { beginAtZero: true, ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' } },
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
      datasets = [{ label: sel, data: hv.map(p => p.aqi), borderColor: '#38BDF8',
        backgroundColor: 'rgba(56,189,248,0.10)', fill: true, tension: 0.35, pointRadius: 2 }];
    } else {
      const allStations = Object.keys(state.aqiHistory);
      if (allStations.length) {
        labels = state.aqiHistory[allStations[0]].map(p => p.t);
        datasets = [{ label: 'Avg AQI (all stations)', data: state.aqiHistory[allStations[0]].map(p => p.aqi),
          borderColor: '#22C55E', backgroundColor: 'rgba(34,197,94,0.10)', fill: true, tension: 0.35, pointRadius: 2 }];
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
    const palette = ['#22C55E', '#38BDF8', '#F59E0B', '#EF4444', '#0EA5E9', '#14B8A6', '#F97316', '#84CC16'];
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
        if (!WATER_POLLUTANTS.includes(p)) return;
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
      tbody.innerHTML = '<tr><td colspan=\"8\" class=\"empty-row\">Backend unavailable — station registry not reachable.</td></tr>';
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
        '<td class=\"' + band.cls + '\">' + (isNaN(aqi) ? '—' : aqi.toFixed(0)) + '</td>' +
        '<td><span class=\"badge' + ((snap.active_violations || 0) > 0 ? ' badge--red' : '') + '\">' + (snap.active_violations != null ? snap.active_violations : '—') + '</span></td>' +
        '<td>' + (typeof snap.compliance_rate === 'number' ? snap.compliance_rate.toFixed(1) + '%' : '—') + '</td>' +
        '<td><span class=\"status-badge ' + band.cls + '\">' + band.label + '</span></td>' +
        '<td>' + (snap.total_readings != null ? Number(snap.total_readings).toLocaleString() : '—') + '</td>' +
        '</tr>';
    }).join('');
  }

  let mapChart = null;

  async function loadMapPage(force) {
    const units = await ensureUnits(force);
    const geo = units.filter(u => typeof u.latitude === 'number' && typeof u.longitude === 'number');
    setText('mapCoordNote', geo.length
      ? 'Plotting real station coordinates returned by the backend (' + geo.length + ' of ' + units.length + ' stations). Coordinate plot — not a GPS street map.'
      : 'Location coordinates unavailable from the backend for these stations.');

    const tbody = $('mapTableBody');
    if (!units.length) {
      tbody.innerHTML = '<tr><td colspan=\"6\" class=\"empty-row\">Backend unavailable — station registry not reachable.</td></tr>';
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
        '<td class=\"' + band.cls + '\">' + aqi.toFixed(0) + '</td>' +
        '<td><span class=\"status-badge ' + band.cls + '\">' + band.label + '</span></td>' +
        '</tr>';
    }).join('');

    if (!geo.length || !window.Chart) return;

    const pointSets = [
      { color: '#22C55E', label: 'Good',      max: 50 },
      { color: '#F59E0B', label: 'Moderate',  max: 100 },
      { color: '#FB923C', label: 'Unhealthy', max: 200 },
      { color: '#EF4444', label: 'High Risk', max: 300 },
      { color: '#B91C1C', label: 'Critical',  max: Infinity },
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
          x: { title: { display: true, text: 'Longitude', color: '#8B98A5' }, ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' } },
          y: { title: { display: true, text: 'Latitude', color: '#8B98A5' }, ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' } },
        },
        plugins: {
          legend: { labels: { color: '#E6EDF3', font: { size: 11 } } },
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
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan=\"7\" class=\"empty-row\">No violations match the current filters.</td></tr>';
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
          '<td><span class=\"sev sev-' + sev + '\">' + sev + '</span></td>' +
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
            return '<div class=\"feed-item feed-item--' + sev.toLowerCase() + '\">' +
              '<div class=\"feed-item-header\"><span class=\"feed-station\">' + esc(a.station_id || '—') + '</span>' +
              '<span class=\"sev sev-' + sev + '\">' + sev + '</span></div>' +
              '<div class=\"feed-body\">' + esc(a.pollutant || '') + ' · ' + ts + '</div></div>';
          }).join('')
        : '<p class=\"feed-empty\">No alerts recorded yet.</p>';
    }
  }

  async function loadViolationsPage(force) {
    if (!state.units) await ensureUnits(force);
    await fetchViolations();
    renderViolationsPage();
  }

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
        borderColor: '#38BDF8',
        backgroundColor: 'rgba(56,189,248,0.10)',
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
        datasets = [{ label: 'Avg AQI — live session', data: avgData, borderColor: '#22C55E',
                      backgroundColor: 'rgba(34,197,94,0.10)', fill: true, tension: 0.35, pointRadius: 2 }];
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
            x: { ticks: { color: '#8B98A5', maxTicksLimit: 8 }, grid: { color: '#1B2430' } },
            y: { min: 0, suggestedMax: 300, ticks: { color: '#8B98A5' }, grid: { color: '#1B2430' } },
          },
          plugins: { legend: { labels: { color: '#E6EDF3', font: { size: 11 } } } },
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
        tbody.innerHTML = '<tr><td colspan=\"6\" class=\"empty-row\">No risk assessments available yet.</td></tr>';
        return;
      }
      tbody.innerHTML = data.assessments.map(a => {
        const level = String(a.risk_level || '').toUpperCase();
        const cls = level === 'HAZARDOUS' ? 'st-critical' : level === 'UNHEALTHY' ? 'st-unhealthy'
          : level === 'MODERATE' ? 'st-moderate' : 'st-good';
        return '<tr>' +
          '<td><strong>' + esc(a.station_id || '—') + '</strong></td>' +
          '<td class=\"' + cls + '\">' + (a.aqi != null ? Number(a.aqi).toFixed(0) : '—') + '</td>' +
          '<td><span class=\"status-badge ' + cls + '\">' + esc(level || '—') + '</span></td>' +
          '<td>' + esc(a.description || '—') + '</td>' +
          '<td><span class=\"badge' + ((a.active_violations || 0) > 0 ? ' badge--red' : '') + '\">' + (a.active_violations != null ? a.active_violations : '—') + '</span></td>' +
          '<td>' + (typeof a.compliance_rate === 'number' ? a.compliance_rate.toFixed(1) + '%' : '—') + '</td>' +
          '</tr>';
      }).join('');
    } catch (err) {
      console.warn('risk assessment unavailable (HTTP ' + (err.status || '?') + ')');
      setText('hrOverall', '—');
      if (tbody) tbody.innerHTML = err.status === 429
        ? '<tr><td colspan=\"6\" class=\"empty-row\">Rate limited by backend — please retry in a moment.</td></tr>'
        : '<tr><td colspan=\"6\" class=\"empty-row\">Risk assessment unavailable from backend.</td></tr>';
    }
  }

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
        '<td class=\"' + st.cls + '\">' + (row ? esc(fmtVal(row.value, row.unit)) : '—') + '</td>' +
        '<td>' + fmtVal(ref.limit, ref.unit) + (ref.min != null ? ' (range ' + ref.min + '–' + ref.max + ')' : '') + '</td>' +
        '<td><span class=\"status-badge ' + st.cls + '\">' + (row ? st.label : 'No data') + '</span></td>' +
        '<td class=\"muted-sm\" style=\"white-space:normal;min-width:220px\">' + esc(ref.concern) + '</td>' +
        '</tr>';
    }).join('');
  }

  // ══ STEP 3: IBM Granite AI layer ════════════════════════════════════════
  // The backend currently exposes real Granite output only through the
  // violation pipeline (report_text on /api/violations). Conversational,
  // on-demand analysis requires the POST /api/ai/chat endpoint — the frontend
  // integration point below is ready and degrades honestly until it exists.
  const AI_CHAT_ENDPOINT = '/api/ai/chat';
  let aiEndpointAvailable = null;   // null = unknown, true/false after first attempt
  let ecoaiSuggestionsBuilt = false;

  const ECOAI_SUGGESTIONS = [
    'Which station currently has the highest pollution?',
    'Which pollutants have the most violations?',
    'What are today\'s major environmental risks?',
    'Which stations need attention?',
    'What does the current AQI indicate?',
    'Which pollutants are harmful to public health?',
    'What are the recent violations?',
    'Give me a summary of current monitoring conditions.',
  ];

  function renderAiTimeRange() {
    const sel = $('aiRangeSelect');
    const note = $('aiHistNote');
    if (!sel || !note) return;
    note.hidden = sel.value === 'live';
  }

  async function loadAiAnalysisPage(force) {
    if (!state.violations.length) await fetchViolations();
    const stations = state.stations || [];
    const viols = state.violations || [];

    // Overview KPIs — real snapshot + violation records only
    setText('aiAqiAvg', stations.length
      ? String(Math.round(stations.reduce((a, s) => a + (Number(s.aqi) || 0), 0) / stations.length)) : '—');

    const sumViol = stations.reduce((a, s) => a + (Number(s.active_violations) || 0), 0);
    setText('aiViolTotal', String(sumViol > 0 ? sumViol : viols.length));

    let worst = null, bestC = null, cSum = 0, cN = 0;
    stations.forEach(st => {
      if (!worst || (Number(st.aqi) || 0) > (Number(worst.aqi) || 0)) worst = st;
      if (typeof st.compliance_rate === 'number') { cSum += st.compliance_rate; cN += 1; }
    });
    setText('aiWorstStation', worst ? worst.station_id : '—');

    const topP = Object.entries(state.pollutantCounts).sort((a, b) => b[1] - a[1])[0];
    setText('aiTopPollutant', topP ? topP[0] : '—');

    setText('aiRiskLevel', statusBand(maxAqiOf(stations)).label);
    setText('aiCompliance', cN ? (cSum / cN).toFixed(1) + '%' : '—');

    // Pollution analysis from real records
    const sevOrder = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];
    const counts = {};
    viols.forEach(v => {
      const k = severityClass(v.severity);
      counts[k] = (counts[k] || 0) + 1;
    });
    const maxC = Math.max(1, ...sevOrder.map(k => counts[k] || 0));
    const barsEl = $('aiSevBars');
    if (barsEl) {
      barsEl.innerHTML = sevOrder.map(k => {
        const n = counts[k] || 0;
        return '<div class="sev-bar">' +
          '<span class="sev-bar-label">' + k + '</span>' +
          '<span class="sev-bar-track"><span class="sev-bar-fill fill-' + k + '" style="width:' + Math.round(n / maxC * 100) + '%"></span></span>' +
          '<span class="sev-bar-count">' + n + '</span></div>';
      }).join('');
    }
    setText('aiSevDist', viols.length
      ? sevOrder.map(k => (counts[k] || 0)).join(' / ') + '  (L/M/H/C)'
      : '—');

    const byStation = {};
    viols.forEach(v => {
      const sid = v.station_id || 'UNKNOWN';
      byStation[sid] = (byStation[sid] || 0) + 1;
    });
    const affected = Object.keys(byStation);
    setText('aiAffected', affected.length ? String(affected.length) + ' station' + (affected.length > 1 ? 's' : '') : '—');
    setText('aiFreq', affected.length
      ? affected.sort((a, b) => byStation[b] - byStation[a]).slice(0, 4)
          .map(s => s + ' ×' + byStation[s]).join(', ')
      : '—');

    // AI Insight panel: REAL IBM Granite reports delivered via /api/violations
    const reports = viols.filter(v => v.report_text && String(v.report_text).trim());
    setText('aiInsightCount', String(reports.length));
    const body = $('aiInsightBody');
    if (body) {
      body.innerHTML = reports.length
        ? reports.slice(0, 3).map(v => {
            const ts = v.timestamp && !isNaN(new Date(v.timestamp))
              ? new Date(v.timestamp).toLocaleString() : '—';
            return '<div class="insight-card">' +
              '<div class="insight-src"><span class="insight-station">' + esc(v.station_id || '—') +
              ' · ' + esc(v.pollutant_type || '') + '</span>' +
              '<span class="insight-tag">GENERATED BY IBM GRANITE</span></div>' +
              '<div class="insight-body">' + esc(String(v.report_text).slice(0, 600)) + '</div>' +
              '<div class="insight-meta">' + ts + ' · severity ' + esc(severityClass(v.severity)) + '</div></div>';
          }).join('')
        : '<p class="feed-empty">No IBM Granite reports available yet. Reports appear here automatically when the violation-detection agent generates them.</p>';
    }

    renderAiTimeRange();
  }

  function chatTime() { return new Date().toLocaleTimeString(); }

  function addChatMsg(role, text) {
    const box = $('ecoaiChat');
    if (!box) return;
    const emptyMsg = box.querySelector('.feed-empty');
    if (emptyMsg) emptyMsg.remove();
    const div = document.createElement('div');
    div.className = 'msg msg-' + role;
    if (role === 'user') {
      div.textContent = text;
    } else if (role === 'ai') {
      const head = document.createElement('div');
      head.className = 'msg-head';
      head.textContent = 'ECOAI · IBM GRANITE';
      const body = document.createElement('span');
      body.textContent = text;
      div.appendChild(head); div.appendChild(body);
    } else {
      div.textContent = text;
    }
    const t = document.createElement('span');
    t.className = 'msg-time';
    t.textContent = chatTime();
    div.appendChild(t);
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  function setEcoaiBusy(busy) {
    const inp = $('ecoaiInput'), btn = $('ecoaiSend'), st = $('ecoaiStatus');
    if (inp) inp.disabled = busy;
    if (btn) btn.disabled = busy;
    document.querySelectorAll('.sugg-btn').forEach(b => { b.disabled = busy; });
    if (st) st.textContent = busy
      ? 'Contacting IBM Granite…'
      : aiEndpointAvailable === false
        ? 'AI service unavailable — the backend AI endpoint is not yet deployed. Monitoring data remains fully available on the other pages.'
        : 'Assistant ready. Questions are answered by IBM Granite through the backend AI layer when available.';
  }

  async function sendEcoaiMessage(question) {
    const inp = $('ecoaiInput');
    let q = typeof question === 'string' ? question : (inp ? inp.value : '');
    q = (q || '').trim();
    if (!q) return;
    if (inp && typeof question !== 'string') inp.value = '';
    addChatMsg('user', q);
    setEcoaiBusy(true);
    try {
      const res = await fetch(API_BASE + AI_CHAT_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q }),
      });
      if (res.status === 404) {
        aiEndpointAvailable = false;
        addChatMsg('system', 'AI service unavailable: the backend does not expose ' + AI_CHAT_ENDPOINT + ' yet. ' +
          'Real IBM Granite violation reports are already visible on the AI Analysis page.');
      } else if (res.status === 429) {
        addChatMsg('system', 'Rate limited by the backend. Please retry in a moment.');
      } else if (res.status === 401 || res.status === 403) {
        addChatMsg('system', 'AI request rejected by the backend (HTTP ' + res.status + ').');
      } else if (!res.ok) {
        addChatMsg('system', 'AI request failed (HTTP ' + res.status + ').');
      } else {
        const data = await res.json().catch(() => null);
        const ans = data && (data.answer || data.response || data.text || data.message);
        if (typeof ans === 'string' && ans.trim()) {
          aiEndpointAvailable = true;
          addChatMsg('ai', ans.trim());
        } else {
          addChatMsg('system', 'Received an empty response from the AI service.');
        }
      }
    } catch (err) {
      addChatMsg('system', 'Backend unreachable — unable to reach the AI service right now.');
    } finally {
      setEcoaiBusy(false);
    }
  }

  function initEcoaiPage() {
    const box = $('ecoaiChat');
    if (box && !box.childElementCount) {
      addChatMsg('system', 'EcoAI Assistant uses your existing monitoring context (stations, AQI, violations, alerts, health risk). ' +
        'Answers are generated by IBM Granite through the backend AI layer when it is available.');
    }
    const row = $('ecoaiSuggestions');
    if (row && !ecoaiSuggestionsBuilt) {
      ecoaiSuggestionsBuilt = true;
      ECOAI_SUGGESTIONS.forEach(q => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'sugg-btn';
        btn.textContent = q;
        btn.addEventListener('click', () => sendEcoaiMessage(q));
        row.appendChild(btn);
      });
    }
  }

  function clearEcoaiChat() {
    const box = $('ecoaiChat');
    if (box) box.innerHTML = '';
    ecoaiSuggestionsBuilt = false;
    initEcoaiPage();
  }

  function initNav() {
    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', () => {
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        item.classList.add('active');

        const page = item.dataset.page;
        document.querySelectorAll('.page').forEach(p => { p.hidden = true; });
        const el = $('page-' + page);
        if (el) el.hidden = false;
        $('sidebar').classList.remove('open');

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
          else if (page === 'ai-analysis') loadAiAnalysisPage();
          else if (page === 'ecoai') initEcoaiPage();
        } catch (err) {
          console.warn('page load failed:', err);
        }
      });
    });
    $('navToggle').addEventListener('click', () => $('sidebar').classList.toggle('open'));
  }

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
    renderAirAqiChart, loadAiAnalysisPage, renderAiTimeRange,
    initEcoaiPage, sendEcoaiMessage, clearEcoaiChat,
  };
})();
