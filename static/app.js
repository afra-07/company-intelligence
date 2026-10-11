/* ============================================================
   Signalpost — app.js
   Frontend logic for the Company Intelligence Dashboard
   ============================================================ */

'use strict';

// ── State ─────────────────────────────────────────────────────────────────────
let currentProfile = null;
let allEvidence = [];

// Norwegian legal form codes that indicate government / public-sector entities
const GOVT_FORM_CODES = ['ORGL', 'STAT', 'KF', 'FKF', 'IKS', 'SF', 'SÆR', 'SAER', 'FLI', 'FYLK', 'KOMM'];

function isGovernmentEntity(profile) {
  if (!profile) return false;
  const lf = (profile.legal_form || '').toUpperCase();
  const sector = (profile.sector || '').toUpperCase();
  const match = lf.match(/\(([^)]+)\)/);
  if (match && GOVT_FORM_CODES.includes(match[1].toUpperCase())) return true;
  if (lf.includes('ORGANISASJONSLEDD') || lf.includes('STATEN') || lf.includes('KOMMUNE') || lf.includes('FYLKESKOMMUNE')) return true;
  if (sector.includes('OFFENTLIG') || sector.includes('STATLIG') || sector.includes('KOMMUNAL')) return true;
  return false;
}

function isSoleProprietorship(profile) {
  if (!profile) return false;
  const lf = (profile.legal_form || '').toUpperCase();
  return lf.includes('ENK') || lf.includes('ENKELTPERSONFORETAK');
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  loadStats();
  injectSvgDefs();
});

function initTheme() {
  const isLight = localStorage.getItem('theme') === 'light';
  if (isLight) {
    document.documentElement.classList.add('light-mode');
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = '🌙';
  }
}

function toggleTheme() {
  const isLight = document.documentElement.classList.toggle('light-mode');
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = isLight ? '🌙' : '☀️';
  localStorage.setItem('theme', isLight ? 'light' : 'dark');
}

function injectSvgDefs() {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden';
  svg.innerHTML = `<defs>
    <linearGradient id="ring-grad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#3b82f6"/>
      <stop offset="100%" stop-color="#06b6d4"/>
    </linearGradient>
  </defs>`;
  document.body.prepend(svg);
}

async function loadStats() {
  try {
    const res = await fetch('/stats');
    const data = await res.json();
    const el = document.getElementById('total-profiles-stat');
    if (el) el.textContent = `${data.total_profiles} profiles cached`;
  } catch (_) {}
}

// ── Search flow ───────────────────────────────────────────────────────────────
function fillOrg(num) {
  document.getElementById('org-input').value = num;
  document.getElementById('org-input').focus();
}

async function handleSearch(e) {
  e.preventDefault();
  const orgNum = document.getElementById('org-input').value.trim().replace(/\s+/g, '');

  if (!orgNum || !/^\d{9}$/.test(orgNum)) {
    shakeInput();
    return;
  }

  setLoading(true);
  showPipeline();
  hideProfile();
  hideError();

  try {
    const result = await runAgent(orgNum);
    if (result.success && result.profile) {
      currentProfile = result.profile;
      allEvidence = result.profile.evidence || [];
      renderProfile(result.profile, result.steps_executed, result.duration_seconds);
      showProfile();
    } else {
      showError('Company not found', result.error || 'No data could be retrieved for this organisation number.');
    }
  } catch (err) {
    showError('Request failed', err.message);
  } finally {
    setLoading(false);
    hidePipeline();
  }
}

async function runAgent(orgNum) {
  // Animate pipeline steps while waiting
  const steps = ['brreg_entity', 'brreg_roles', 'regnskapsregisteret', 'proff_scrape', 'website_discovery', 'generate_description', 'build_profile'];
  let stepIdx = 0;
  const stepInterval = setInterval(() => {
    if (stepIdx < steps.length) {
      setActiveStep(steps[stepIdx]);
      stepIdx++;
    }
  }, 700);

  try {
    const res = await fetch('/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ organization_number: orgNum, force_refresh: false }),
    });
    clearInterval(stepInterval);

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      return { success: false, error: err.detail || 'Server error' };
    }
    return await res.json();
  } catch (err) {
    clearInterval(stepInterval);
    throw err;
  }
}

// ── Profile rendering ─────────────────────────────────────────────────────────
function renderProfile(p, steps, duration) {
  // Company header
  setText('c-name', p.company_name || '—');
  setText('c-org', `#${p.organization_number}`);

  // Status badge
  const statusEl = document.getElementById('c-status');
  const s = (p.status || 'Unknown').toLowerCase();
  statusEl.textContent = p.status || '—';
  statusEl.className = 'badge';
  if (s === 'active') statusEl.classList.add('badge-active');
  else if (s.includes('bankrupt')) statusEl.classList.add('badge-bankrupt');
  else statusEl.classList.add('badge-liquidation');

  setText('c-form', p.legal_form || '—');

  // Industry + address + website
  setText('c-industry', p.industry_description || p.industry_code || '—');
  setText('c-addr', [p.address, p.municipality].filter(Boolean).join(' · ') || '—');

  const webEl = document.getElementById('c-web');
  if (p.website) {
    webEl.href = p.website;
    webEl.textContent = p.website.replace(/^https?:\/\//, '').replace(/\/$/, '');
    document.getElementById('c-web-wrap').style.display = 'flex';
  } else {
    document.getElementById('c-web-wrap').style.display = 'none';
  }

  // Description
  const descEl = document.getElementById('c-desc');
  if (p.description) {
    descEl.textContent = p.description;
    descEl.style.display = 'block';
  } else {
    descEl.style.display = 'none';
  }

  // Confidence ring
  const conf = Math.round((p.confidence_score || 0) * 100);
  const circumference = 2 * Math.PI * 32;
  const offset = circumference - (conf / 100) * circumference;
  const ring = document.getElementById('ring-fill');
  ring.style.strokeDasharray = circumference;
  setTimeout(() => { ring.style.strokeDashoffset = offset; }, 100);
  setText('conf-pct', `${conf}%`);

  // ── Stats rendering with contextual missing-data reasons ─────────────────
  const isGovt = isGovernmentEntity(p);
  const isEnk = isSoleProprietorship(p);
  const noFinancials = !p.revenue && !p.net_income && !p.total_assets;

  let finCardNote = '';
  if (noFinancials) {
    if (isGovt) {
      finCardNote = 'Public sector (exempt)';
    } else if (isEnk) {
      finCardNote = 'ENK exempt from filing';
    } else if (p.financials_status === 'no_data') {
      finCardNote = 'Unparsed IFRS format';
    } else {
      finCardNote = 'Not in Regnskapsregisteret';
    }
  }

  setValue('sv-revenue', p.revenue, 'nok');
  setNote('sn-revenue', !p.revenue ? finCardNote : '');

  setValue('sv-profit', p.net_income, 'nok');
  setNote('sn-profit', !p.net_income ? finCardNote : '');

  setValue('sv-assets', p.total_assets, 'nok');
  setNote('sn-assets', !p.total_assets ? finCardNote : '');

  setValue('sv-employees', p.employees, 'int');
  setNote('sn-employees', (p.employees === null || p.employees === undefined) ? 'Not in Aa-registeret' : '');

  setValue('sv-ceo', p.ceo);
  setNote('sn-ceo', !p.ceo ? 'Not listed in Brreg roles' : '');

  setValue('sv-founded', p.registered_date ? p.registered_date.slice(0, 4) : null);
  setNote('sn-founded', !p.registered_date ? 'Not registered' : '');

  if (p.financial_year) {
    document.getElementById('sy-year').textContent = `FY ${p.financial_year}`;
  } else {
    document.getElementById('sy-year').textContent = '';
  }

  // ── Financials unavailability banner ─────────────────────────────────────
  let noticeIcon = '', noticeTitle = '', noticeBody = '', bannerType = '';

  if (isGovt && noFinancials) {
    bannerType  = 'govt';
    noticeIcon  = '🏛️';
    noticeTitle = 'Public-Sector / Government Entity — Financials Exempt';
    noticeBody  = 'Public-sector organisations, directorates, and government bodies (e.g., Riksrevisjonen, ministries, courts, and municipalities) operate on parliamentary budget allocations and are legally exempt from filing commercial accounts with Regnskapsregisteret.';
  } else if (isEnk && noFinancials) {
    bannerType  = 'info';
    noticeIcon  = '👤';
    noticeTitle = 'Sole Proprietorship (Enkeltpersonforetak) — No Public Accounts';
    noticeBody  = 'Sole proprietorships (ENK) in Norway are legally exempt from filing annual financial statements with Regnskapsregisteret unless their turnover exceeds statutory auditing thresholds. Financials are reported privately via individual tax returns.';
  } else if (!isGovt && noFinancials && p.financials_status === 'no_data') {
    bannerType  = '';
    noticeIcon  = '⚠️';
    noticeTitle = 'Specialized Non-Standard Accounting Structure';
    noticeBody  = 'Annual accounts were found in Regnskapsregisteret, but the filing uses a non-standard banking/insurance IFRS format where standard operating revenue cannot be extracted automatically. You can inspect the full raw statement in the JSON viewer below.';
  } else if (!isGovt && noFinancials) {
    bannerType  = 'info';
    noticeIcon  = '📋';
    noticeTitle = 'Annual Accounts Not Available in Regnskapsregisteret';
    noticeBody  = `No annual accounts were returned by the Brønnøysund Regnskapsregister API for organisation #${p.organization_number}. Note that commercial banks, large listed corporations (filing via EU ESAP / XBRL portals), holding entities, and foreign branch offices (NUF) often do not have standard electronic NGAAP statements in this open API. Check their official annual report or investor relations portal.`;
  }

  const finNotice = document.getElementById('fin-unavail-notice');
  if (finNotice) {
    if (noticeTitle) {
      document.getElementById('fin-notice-icon').textContent = noticeIcon;
      document.getElementById('fin-notice-title').textContent = noticeTitle;
      document.getElementById('fin-notice-body').textContent = noticeBody;
      finNotice.className = `fin-unavail-banner ${bannerType}`.trim();
    } else {
      finNotice.className = 'fin-unavail-banner hidden';
    }
  }


  // Board & management
  renderBoard(p.ceo, p.board_members);

  // Pipeline trace
  renderPipelineTrace(steps || [], duration);

  // Evidence
  renderEvidence(p.evidence || []);

  // JSON
  document.getElementById('json-pre').textContent = JSON.stringify(p, null, 2);

  // Stagger card animations
  document.querySelectorAll('.stat-card').forEach((el, i) => {
    el.style.animationDelay = `${i * 0.06}s`;
  });
}

function setValue(id, val, fmt) {
  const el = document.getElementById(id);
  if (!el) return;
  if (val === null || val === undefined) { el.textContent = '—'; return; }
  if (fmt === 'nok') {
    el.textContent = formatNOK(val);
  } else if (fmt === 'int') {
    el.textContent = Number(val).toLocaleString('no-NO');
  } else {
    el.textContent = val;
  }
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val || '—';
}

function setNote(id, text) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = text || '';
    el.style.display = text ? 'block' : 'none';
  }
}

function formatNOK(val) {
  if (!val && val !== 0) return '—';
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (abs >= 1e9) return `${sign}NOK ${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}NOK ${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}NOK ${(abs / 1e3).toFixed(0)}K`;
  return `${sign}NOK ${abs.toLocaleString('no-NO')}`;
}

// ── Board rendering ───────────────────────────────────────────────────────────
function renderBoard(ceo, boardMembers) {
  const list = document.getElementById('board-list');
  list.innerHTML = '';
  const card = document.getElementById('board-card');

  const members = [];
  if (ceo) members.push({ name: ceo, role: 'CEO / Daglig leder' });
  (boardMembers || []).forEach(m => {
    const match = m.match(/^(.+?)\s*\((.+)\)$/);
    if (match) members.push({ name: match[1].trim(), role: match[2].trim() });
    else members.push({ name: m, role: 'Board member' });
  });

  if (!members.length) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';

  members.forEach(({ name, role }) => {
    const initials = name.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
    const div = document.createElement('div');
    div.className = 'board-item';
    div.innerHTML = `
      <div class="board-avatar">${initials}</div>
      <div>
        <div class="board-name">${escHtml(name)}</div>
        <div class="board-role">${escHtml(role)}</div>
      </div>`;
    list.appendChild(div);
  });
}

// ── Pipeline trace rendering ──────────────────────────────────────────────────
const STEP_LABELS = {
  cache_hit:                { label: 'Cache hit — returned instantly', dot: 'ok' },
  brreg_entity:             { label: 'Brreg entity fetched', dot: 'ok' },
  brreg_roles:              { label: 'Management roles fetched', dot: 'ok' },
  regnskapsregisteret:      { label: 'Financial statements fetched', dot: 'ok' },
  proff_scrape:             { label: 'Proff.no scraped', dot: 'ok' },
  proff_verified:           { label: 'Proff.no source verified ✓', dot: 'ok' },
  proff_no_llm_verify:      { label: 'Proff.no scraped (no LLM key — skipped verification)', dot: 'warn' },
  proff_rejected_mismatch:  { label: 'Proff.no rejected — company mismatch', dot: 'warn' },
  website_discovery:        { label: 'Company website discovered', dot: 'ok' },
  website_verified:         { label: 'Website identity verified ✓', dot: 'ok' },
  website_no_llm_extract:   { label: 'Website found (no LLM key — content extraction skipped)', dot: 'warn' },
  news_search:              { label: 'News search performed', dot: 'ok' },
  generate_description:     { label: 'Description generated via LLM', dot: 'ok' },
  build_profile:            { label: 'Profile built & saved', dot: 'ok' },
  profile_created:          { label: 'New profile created', dot: 'ok' },
  profile_merged:           { label: 'Existing profile updated', dot: 'ok' },
};

function renderPipelineTrace(steps, duration) {
  const container = document.getElementById('pipeline-trace');
  container.innerHTML = '';
  steps.forEach(step => {
    const meta = STEP_LABELS[step] || { label: step, dot: 'ok' };
    const div = document.createElement('div');
    div.className = 'trace-step';
    div.innerHTML = `<div class="trace-dot ${meta.dot}"></div><span>${meta.label}</span>`;
    container.appendChild(div);
  });

  const dur = document.getElementById('duration-val');
  if (dur) dur.textContent = duration ? `${duration}s` : '—';
}

// ── Evidence rendering ────────────────────────────────────────────────────────
function renderEvidence(evidence) {
  const grid = document.getElementById('evidence-grid');
  grid.innerHTML = '';
  allEvidence = evidence;

  // Filter out internal/raw evidence
  const shown = evidence.filter(e => !e.fact_key.startsWith('_'));

  if (!shown.length) {
    grid.innerHTML = '<p style="color:var(--text3);font-size:0.85rem">No evidence collected.</p>';
    return;
  }

  shown.forEach(ev => {
    const card = document.createElement('div');
    card.className = 'ev-card';
    card.dataset.method = ev.method;

    const confPct = Math.round((ev.confidence || 0) * 100);
    const valStr = Array.isArray(ev.fact_value)
      ? ev.fact_value.join(', ')
      : String(ev.fact_value ?? '—');

    card.innerHTML = `
      <div class="ev-card-top">
        <span class="ev-field">${escHtml(ev.fact_key)}</span>
        <span class="ev-method ${ev.method}">${ev.method.replace('_', ' ')}</span>
      </div>
      <div class="ev-value">${escHtml(valStr.slice(0, 120))}</div>
      <div class="ev-source">
        <a href="${escHtml(ev.source_url)}" target="_blank" title="${escHtml(ev.source_url)}">
          ${escHtml(ev.source_name)}
        </a>
      </div>
      <div class="ev-confidence-bar">
        <div class="ev-confidence-fill" style="width:${confPct}%"></div>
      </div>
      <div class="ev-date">${ev.date_checked} · ${confPct}% confidence</div>`;

    grid.appendChild(card);
  });
}

function filterEvidence(method, btn) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  document.querySelectorAll('.ev-card').forEach(card => {
    if (method === 'all' || card.dataset.method === method) {
      card.classList.remove('hidden');
    } else {
      card.classList.add('hidden');
    }
  });
}

// ── JSON toggle ───────────────────────────────────────────────────────────────
function toggleJson() {
  const pre = document.getElementById('json-pre');
  const toggle = document.getElementById('json-toggle');
  pre.classList.toggle('hidden');
  toggle.textContent = pre.classList.contains('hidden') ? '▼ Show' : '▲ Hide';
}

// ── Batch processing ──────────────────────────────────────────────────────────
async function runBatch() {
  const raw = document.getElementById('batch-input').value.trim();
  const orgs = raw.split(/\s+/).map(s => s.trim()).filter(s => /^\d{9}$/.test(s));

  if (!orgs.length) {
    alert('Enter at least one valid 9-digit organisation number.');
    return;
  }
  if (orgs.length > 50) {
    alert('Maximum 50 organisations per batch.');
    return;
  }

  const resultsDiv = document.getElementById('batch-results');
  resultsDiv.innerHTML = `<p style="color:var(--text2);font-size:0.85rem">Processing ${orgs.length} companies…</p>`;

  try {
    const res = await fetch('/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ organization_numbers: orgs, concurrency: 5 }),
    });
    const results = await res.json();
    renderBatchResults(results);
    loadStats();
  } catch (err) {
    resultsDiv.innerHTML = `<p style="color:var(--rose)">Error: ${escHtml(err.message)}</p>`;
  }
}

function renderBatchResults(results) {
  const div = document.getElementById('batch-results');
  div.innerHTML = '';
  
  // Add a header row for the batch table
  if (results.length > 0) {
    const header = document.createElement('div');
    header.className = 'batch-result-row batch-header';
    header.style.fontWeight = 'bold';
    header.style.borderBottom = '1px solid var(--border)';
    header.style.marginBottom = '0.5rem';
    header.innerHTML = `
      <span class="br-org">Org No.</span>
      <span class="br-name" style="flex:1">Company</span>
      <span class="br-fin" style="width:120px; text-align:right">Revenue</span>
      <span class="br-fin" style="width:120px; text-align:right">Income</span>
      <span class="br-fin" style="width:120px; text-align:right">Assets</span>
      <span class="br-status" style="width:80px; text-align:right">Status</span>
      <span class="br-link" style="width:80px"></span>
    `;
    div.appendChild(header);
  }

  results.forEach(r => {
    const row = document.createElement('div');
    row.className = 'batch-result-row';
    const p = r.profile || {};
    const name = p.company_name || '—';
    const rev = formatNOK(p.revenue);
    const inc = formatNOK(p.net_income);
    const ast = formatNOK(p.total_assets);
    
    row.innerHTML = `
      <span class="br-org">${r.organization_number}</span>
      <span class="br-name" style="flex:1">${escHtml(name)}</span>
      <span class="br-fin" style="width:120px; text-align:right">${rev}</span>
      <span class="br-fin" style="width:120px; text-align:right">${inc}</span>
      <span class="br-fin" style="width:120px; text-align:right">${ast}</span>
      <span class="br-status ${r.success ? 'ok' : 'err'}" style="width:80px; text-align:right">${r.success ? '✓ OK' : '✗ Failed'}</span>
      ${r.success ? `<span class="br-link" style="width:80px; text-align:right" onclick="loadCached('${r.organization_number}')">View →</span>` : '<span style="width:80px"></span>'}`;
    div.appendChild(row);
  });
}

async function loadCached(orgNum) {
  document.getElementById('org-input').value = orgNum;
  window.scrollTo({ top: 0, behavior: 'smooth' });

  try {
    const res = await fetch(`/profile/${orgNum}`);
    if (!res.ok) return;
    const profile = await res.json();
    currentProfile = profile;
    allEvidence = profile.evidence || [];
    renderProfile(profile, [], null);
    showProfile();
  } catch (_) {}
}

// ── UI state helpers ──────────────────────────────────────────────────────────
function setLoading(on) {
  const btn = document.getElementById('search-btn');
  const input = document.getElementById('org-input');
  btn.disabled = on;
  input.disabled = on;
  if (on) {
    btn.querySelector('.btn-text').textContent = 'Analysing…';
  } else {
    btn.querySelector('.btn-text').textContent = 'Analyse';
  }
}

function showPipeline() {
  document.getElementById('pipeline-section').classList.remove('hidden');
  // Reset all steps
  document.querySelectorAll('.p-step').forEach(s => {
    s.classList.remove('active', 'done');
  });
}

function hidePipeline() {
  // Mark all steps done
  document.querySelectorAll('.p-step').forEach(s => {
    s.classList.remove('active');
    s.classList.add('done');
  });
  setTimeout(() => {
    document.getElementById('pipeline-section').classList.add('hidden');
  }, 800);
}

function setActiveStep(stepKey) {
  const stepMap = {
    brreg_entity: 'brreg_entity',
    brreg_roles: 'brreg_roles',
    regnskapsregisteret: 'regnskapsregisteret',
    proff_scrape: 'proff_scrape',
    website_discovery: 'website_discovery',
    generate_description: 'generate_description',
    build_profile: 'build_profile',
  };
  document.querySelectorAll('.p-step').forEach(el => {
    const s = el.dataset.step;
    const steps_order = Object.keys(stepMap);
    const targetIdx = steps_order.indexOf(stepKey);
    const thisIdx = steps_order.indexOf(s);
    if (thisIdx < targetIdx) {
      el.classList.remove('active');
      el.classList.add('done');
    } else if (thisIdx === targetIdx) {
      el.classList.add('active');
      el.classList.remove('done');
    }
  });
  document.getElementById('pipeline-msg').textContent = `Running: ${stepKey.replace(/_/g, ' ')}…`;
}

function showProfile() {
  document.getElementById('profile-section').classList.remove('hidden');
  document.getElementById('profile-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function hideProfile() {
  document.getElementById('profile-section').classList.add('hidden');
}

function showError(title, msg) {
  document.getElementById('error-title').textContent = title;
  document.getElementById('error-msg').textContent = msg || '';
  document.getElementById('error-section').classList.remove('hidden');
}

function hideError() {
  document.getElementById('error-section').classList.add('hidden');
}

function resetUI() {
  hideError();
  hideProfile();
  document.getElementById('org-input').value = '';
  document.getElementById('org-input').focus();
}

function shakeInput() {
  const wrap = document.querySelector('.search-wrap');
  wrap.style.animation = 'none';
  wrap.offsetHeight; // reflow
  wrap.style.animation = 'shake 0.4s ease';
  setTimeout(() => { wrap.style.animation = ''; }, 400);
}

// Inline shake keyframe
const shakeStyle = document.createElement('style');
shakeStyle.textContent = `
@keyframes shake {
  0%,100% { transform: translateX(0); }
  20% { transform: translateX(-6px); }
  40% { transform: translateX(6px); }
  60% { transform: translateX(-4px); }
  80% { transform: translateX(4px); }
}`;
document.head.appendChild(shakeStyle);

// ── Utility ───────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
