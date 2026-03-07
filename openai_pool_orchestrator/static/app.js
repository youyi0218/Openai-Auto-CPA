
const $ = (id) => document.getElementById(id);
const DOM = {};
const state = {
  status: 'idle',
  autoScroll: true,
  logCount: 0,
  currentSteps: {},
  eventSource: null,
  countdownTimer: null,
  tokens: [],
  tokenSummary: {},
  tokenFilter: { status: 'all', keyword: '' },
  apiKey: '',
  authWarned: false,
};

const STEPS = [
  ['check_proxy', '检测代理'], ['create_email', '创建邮箱'], ['oauth_init', 'OAuth 初始化'], ['sentinel', 'Sentinel Token'],
  ['signup', '提交注册'], ['send_otp', '发送验证码'], ['wait_otp', '等待验证码'], ['verify_otp', '验证 OTP'],
  ['create_account', '创建账号'], ['workspace', '选择 Workspace'], ['get_token', '获取 Token'],
];

const VIEW_TITLES = { viewDashboard: '仪表盘', viewPool: '账号池', viewConfig: '系统配置' };
const TOAST_ICONS = { info: 'i', success: '✓', warn: '!', error: '×' };
const LEVEL_ICONS = { connected: '*', info: 'i', success: '✓', warn: '!', error: '×', token_saved: '+', sync_ok: '↗' };
const THEME_KEY = 'oai_auto_cpa_theme_v1';
const API_KEY_STORAGE = 'oai_auto_cpa_api_key_v1';
const rawFetch = window.fetch.bind(window);

document.addEventListener('DOMContentLoaded', init);

function init() {
  mapDom();
  initApiKey();
  initNav();
  initCollapsible();
  initTheme();
  initMailCheckboxes();
  bindEvents();
  renderSteps();
  connectSSE();

  Promise.allSettled([
    loadApiAuthStatus(), updateStatus(), loadTokens(), loadSyncConfig(), loadProxyPoolConfig(), loadPoolConfig(), loadMailConfig(), pollPoolStatus(), pollSub2ApiPoolStatus(),
  ]);

  setInterval(updateStatus, 3000);
  setInterval(loadTokens, 30000);
  setInterval(pollPoolStatus, 30000);
  setInterval(pollSub2ApiPoolStatus, 30000);
}

function mapDom() {
  [
    'pageTitle','statusBadge','statusText','themeToggleBtn',
    'headerSub2apiChip','headerSub2apiLabel','headerSub2apiDelta','headerSub2apiBar','headerCpaChip','headerCpaLabel','headerCpaDelta','headerCpaBar',
    'proxyInput','checkProxyBtn','saveProxyBtn','proxyStatus','multithreadCheck','threadCountInput','autoRegisterCheck','expectedTokenCountInput','localAutoMaintainCheck','localMaintainIntervalInput','btnStart','btnStop',
    'statSuccess','statFail','statTotal','progressFill','stepsTracker','autoScrollCheck','logBody','logCount','clearLogBtn',
    'sub2apiPoolTotal','sub2apiPoolNormal','sub2apiPoolError','sub2apiPoolThreshold','sub2apiPoolPercent','sub2apiPoolRefreshBtn','sub2apiPoolMaintainBtn','sub2apiPoolMaintainStatus',
    'poolTotal','poolCandidates','poolError','poolThreshold','poolPercent','poolRefreshBtn','poolMaintainBtn','poolMaintainStatus',
    'poolTokenCount','poolRecentTokenTotal','poolCopyRtBtn','poolPwSyncBtn','tokenFilterStatus','tokenFilterKeyword','tokenFilterApplyBtn','tokenFilterResetBtn','poolTokenList',
    'sub2apiBaseUrl','sub2apiEmail','sub2apiPassword','autoSyncCheck','sub2apiMinCandidates','sub2apiAutoMaintain','sub2apiInterval','sub2apiTestPoolBtn','saveSyncConfigBtn','syncStatus',
    'uploadMode','uploadModeSaveBtn','uploadModeStatus',
    'proxyPoolEnabled','proxyPoolProvider','proxyPoolApiUrlLabel','proxyPoolApiUrl','proxyPoolZenAuthRow','proxyPoolZenFilterRow','proxyPoolAuthMode','proxyPoolApiKey','proxyPoolCount','proxyPoolCountry','proxyPoolTestBtn','proxyPoolSaveBtn','proxyPoolStatus',
    'cpaBaseUrl','cpaToken','cpaMinCandidates','cpaUsedPercent','cpaAutoMaintain','cpaInterval','cpaTestBtn','cpaSaveBtn','cpaStatus',
    'apiAuthEnabled','apiAuthKeyInput','apiAuthApplyBtn','apiAuthSaveBtn','apiAuthStatus',
    'mailStrategySelect','mailTestBtn','mailSaveBtn','mailStatus',
  ].forEach((id) => DOM[id] = $(id));
}

function bindEvents() {
  on(DOM.checkProxyBtn, 'click', checkProxy);
  on(DOM.saveProxyBtn, 'click', saveProxy);
  on(DOM.btnStart, 'click', startTask);
  on(DOM.btnStop, 'click', stopTask);
  on(DOM.clearLogBtn, 'click', clearLog);
  on(DOM.autoScrollCheck, 'change', () => state.autoScroll = !!DOM.autoScrollCheck.checked);

  on(DOM.poolRefreshBtn, 'click', pollPoolStatus);
  on(DOM.poolMaintainBtn, 'click', triggerMaintenance);
  on(DOM.sub2apiPoolRefreshBtn, 'click', pollSub2ApiPoolStatus);
  on(DOM.sub2apiPoolMaintainBtn, 'click', triggerSub2ApiMaintenance);

  on(DOM.poolCopyRtBtn, 'click', copyAllRt);
  on(DOM.poolPwSyncBtn, 'click', batchSync);
  on(DOM.tokenFilterApplyBtn, 'click', applyTokenFilter);
  on(DOM.tokenFilterResetBtn, 'click', resetTokenFilter);
  on(DOM.tokenFilterKeyword, 'keydown', (e) => e.key === 'Enter' && applyTokenFilter());

  on(DOM.saveSyncConfigBtn, 'click', saveSyncConfig);
  on(DOM.uploadModeSaveBtn, 'click', saveUploadMode);

  on(DOM.proxyPoolProvider, 'change', onProxyPoolProviderChange);
  on(DOM.proxyPoolTestBtn, 'click', testProxyPoolFetch);
  on(DOM.proxyPoolSaveBtn, 'click', saveProxyPoolConfig);

  on(DOM.cpaSaveBtn, 'click', savePoolConfig);
  on(DOM.cpaTestBtn, 'click', testCpaConnection);
  on(DOM.sub2apiTestPoolBtn, 'click', testSub2ApiPoolConnection);
  on(DOM.apiAuthApplyBtn, 'click', applyApiKeyToSession);
  on(DOM.apiAuthSaveBtn, 'click', saveApiAuthConfig);

  on(DOM.mailSaveBtn, 'click', saveMailConfig);
  on(DOM.mailTestBtn, 'click', testMailConnection);

  if (DOM.poolTokenList) {
    DOM.poolTokenList.addEventListener('click', async (e) => {
      const cp = e.target.closest('.token-copy-btn');
      if (cp) return copyToken(decodeURIComponent(cp.dataset.payload || ''));
      const dl = e.target.closest('.token-delete-btn');
      if (dl) return deleteToken(decodeURIComponent(dl.dataset.filename || ''));
    });
  }
}

function on(el, evt, fn) { if (el) el.addEventListener(evt, fn); }
function esc(v) { return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;'); }
function cssEsc(v) { return String(v ?? '').replace(/[^a-zA-Z0-9_-]/g, '_'); }
function num(v, d = 0) { const n = Number(v); return Number.isFinite(n) ? n : d; }
function numOrNull(v) { const n = Number(v); return Number.isFinite(n) ? n : null; }

function initApiKey() {
  try { state.apiKey = localStorage.getItem(API_KEY_STORAGE) || ''; } catch { state.apiKey = ''; }
  if (DOM.apiAuthKeyInput && state.apiKey) DOM.apiAuthKeyInput.value = state.apiKey;
}

function setApiKey(key) {
  state.apiKey = String(key || '').trim();
  try {
    if (state.apiKey) localStorage.setItem(API_KEY_STORAGE, state.apiKey);
    else localStorage.removeItem(API_KEY_STORAGE);
  } catch {}
}

function apiEventSourceUrl(path) {
  const url = String(path || '');
  if (!state.apiKey || !url.startsWith('/api/')) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}api_key=${encodeURIComponent(state.apiKey)}`;
}

async function apiFetch(input, init = {}) {
  const url = typeof input === 'string' ? input : (input?.url || '');
  if (!url.startsWith('/api/')) return rawFetch(input, init);
  const headers = { ...(init.headers || {}) };
  if (state.apiKey) {
    headers.Authorization = `Bearer ${state.apiKey}`;
    headers['X-API-Key'] = state.apiKey;
  }
  const res = await rawFetch(input, { ...init, headers });
  if (res.status === 401 && !state.authWarned) {
    state.authWarned = true;
    if (DOM.apiAuthStatus) DOM.apiAuthStatus.textContent = 'API Key 校验失败，请输入正确密钥后点击“应用到当前页面”';
    showToast('API Key 校验失败', 'error');
  } else if (res.status !== 401) {
    state.authWarned = false;
  }
  return res;
}

function initNav() {
  const btns = [...document.querySelectorAll('.side-link')];
  const panels = [...document.querySelectorAll('.view-panel')];
  btns.forEach((b) => b.addEventListener('click', () => {
    const view = b.dataset.view;
    btns.forEach((x) => x.classList.toggle('active', x === b));
    panels.forEach((p) => p.classList.toggle('active', p.id === view));
    if (DOM.pageTitle) DOM.pageTitle.textContent = VIEW_TITLES[view] || '控制台';
  }));
}

function initCollapsible() {
  document.querySelectorAll('.collapsible-trigger').forEach((t) => t.addEventListener('click', () => {
    const box = t.closest('.collapsible');
    if (box) box.classList.toggle('open');
  }));
}

function initTheme() {
  let theme = 'light';
  try { theme = localStorage.getItem(THEME_KEY) || 'light'; } catch {}
  applyTheme(theme);
  on(DOM.themeToggleBtn, 'click', () => {
    const next = document.body.classList.contains('theme-light') ? 'dark' : 'light';
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch {}
  });
}

function applyTheme(theme) {
  const light = theme === 'light';
  document.body.classList.toggle('theme-light', light);
  if (!DOM.themeToggleBtn) return;
  const cur = light ? '明亮' : '暗色';
  const nxt = light ? '暗色' : '明亮';
  const lb = DOM.themeToggleBtn.querySelector('.theme-toggle-label');
  if (lb) lb.textContent = cur;
  DOM.themeToggleBtn.setAttribute('title', `切换到${nxt}主题`);
  DOM.themeToggleBtn.setAttribute('aria-label', `切换到${nxt}主题`);
}

async function loadApiAuthStatus() {
  try {
    const res = await rawFetch('/api/auth/status');
    const d = await res.json();
    if (!res.ok) return;
    if (DOM.apiAuthEnabled) DOM.apiAuthEnabled.checked = !!d.api_auth_enabled || !!d.enabled;
    if (DOM.apiAuthStatus) {
      if (d.enabled) {
        DOM.apiAuthStatus.textContent = `鉴权已启用，当前 key: ${d.api_key_preview || '已配置'}`;
      } else if (d.configured) {
        DOM.apiAuthStatus.textContent = '检测到已保存 key，但鉴权未启用';
      } else {
        DOM.apiAuthStatus.textContent = '鉴权未启用';
      }
    }
  } catch {}
}

function applyApiKeyToSession() {
  const key = (DOM.apiAuthKeyInput?.value || '').trim();
  setApiKey(key);
  if (DOM.apiAuthStatus) DOM.apiAuthStatus.textContent = key ? '已应用到当前页面请求头' : '已清空当前页面 API Key';
  connectSSE();
  showToast(key ? 'API Key 已应用到当前页面' : 'API Key 已清空', 'success');
}

async function saveApiAuthConfig() {
  if (!DOM.apiAuthSaveBtn) return;
  const enabled = !!DOM.apiAuthEnabled?.checked;
  const key = (DOM.apiAuthKeyInput?.value || '').trim();

  DOM.apiAuthSaveBtn.disabled = true;
  if (DOM.apiAuthStatus) DOM.apiAuthStatus.textContent = '保存中...';

  try {
    const res = await apiFetch('/api/auth/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled, api_key: key }),
    });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.apiAuthStatus) DOM.apiAuthStatus.textContent = d.detail || '保存失败';
      return showToast(d.detail || '保存失败', 'error');
    }
    if (d.api_key) {
      setApiKey(d.api_key);
      if (DOM.apiAuthKeyInput) DOM.apiAuthKeyInput.value = d.api_key;
    } else if (key) {
      setApiKey(key);
    }
    if (DOM.apiAuthStatus) DOM.apiAuthStatus.textContent = `已保存，当前 key: ${d.api_key_preview || '已配置'}`;
    connectSSE();
    showToast('鉴权配置已保存', 'success');
  } catch (e) {
    if (DOM.apiAuthStatus) DOM.apiAuthStatus.textContent = `保存失败: ${e.message}`;
    showToast(`保存失败: ${e.message}`, 'error');
  }

  DOM.apiAuthSaveBtn.disabled = false;
}

function connectSSE() {
  if (state.eventSource) state.eventSource.close();
  state.eventSource = new EventSource(apiEventSourceUrl('/api/logs'));
  state.eventSource.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch { return; }
    appendLog(ev);
    if (ev.level === 'token_saved') { loadTokens(); showToast(`新 Token 已保存: ${ev.message || ''}`, 'success'); }
    if (ev.level === 'sync_ok') showToast(`已自动同步: ${ev.message || ''}`, 'success');
    if (ev.step === 'start') { state.currentSteps = {}; renderSteps(); if (state.countdownTimer) clearInterval(state.countdownTimer); }
    if (ev.step === 'wait') {
      const m = String(ev.message || '').match(/(\d+)/);
      if (m) startCountdown(parseInt(m[1], 10));
    }
    if (ev.step) updateStep(ev.step, ev.level);
  };
  state.eventSource.onerror = () => setTimeout(connectSSE, 3000);
}

function appendLog(ev) {
  if (!DOM.logBody || !DOM.logCount) return;
  const lv = String(ev.level || 'info');
  const row = document.createElement('div');
  row.className = 'log-entry';
  row.innerHTML = `<span class="log-ts">${esc(ev.ts || '')}</span><span class="log-icon">${LEVEL_ICONS[lv] || '•'}</span><span class="log-msg ${esc(lv)}">${esc(ev.message || '')}</span><span class="log-step">${esc(ev.step || '')}</span>`;
  DOM.logBody.appendChild(row);
  state.logCount += 1;
  DOM.logCount.textContent = String(state.logCount);
  const list = DOM.logBody.querySelectorAll('.log-entry');
  if (list.length > 2000) list[0].remove();
  if (state.autoScroll) DOM.logBody.scrollTop = DOM.logBody.scrollHeight;
}

function clearLog() {
  if (DOM.logBody) DOM.logBody.innerHTML = '';
  state.logCount = 0;
  if (DOM.logCount) DOM.logCount.textContent = '0';
}

function renderSteps() {
  if (!DOM.stepsTracker) return;
  DOM.stepsTracker.innerHTML = STEPS.map(([id, label]) => `<div id="step-${id}" class="step-item ${state.currentSteps[id] || ''}">${esc(label)}</div>`).join('');
  updateProgress();
}

function updateStep(step, level) {
  if (!step) return;
  const cur = state.currentSteps[step] || '';
  if (['success','token_saved','sync_ok'].includes(level)) state.currentSteps[step] = 'success';
  else if (['error','fail'].includes(level)) state.currentSteps[step] = 'error';
  else if (['info','warn','connected'].includes(level) && cur !== 'success' && cur !== 'error') state.currentSteps[step] = 'active';
  const el = $(`step-${step}`);
  if (el) el.className = `step-item ${state.currentSteps[step] || ''}`;
  updateProgress();
}

function updateProgress() {
  if (!DOM.progressFill) return;
  const done = Object.values(state.currentSteps).filter((x) => x === 'success').length;
  DOM.progressFill.style.width = `${Math.round((done / STEPS.length) * 100)}%`;
}

function startCountdown(sec) {
  if (state.countdownTimer) clearInterval(state.countdownTimer);
  let r = sec;
  const rows = DOM.logBody ? DOM.logBody.querySelectorAll('.log-entry') : [];
  const msg = rows.length ? rows[rows.length - 1].querySelector('.log-msg') : null;
  state.countdownTimer = setInterval(() => {
    r -= 1;
    if (r <= 0) return clearInterval(state.countdownTimer);
    if (msg) msg.textContent = `等待中... 剩余 ${r} 秒`;
  }, 1000);
}

async function updateStatus() {
  try {
    const res = await apiFetch('/api/status');
    const d = await res.json();
    if (!res.ok) return;
    state.status = d.status || 'idle';
    const suc = num(d.success), fail = num(d.fail);
    if (DOM.statSuccess) DOM.statSuccess.textContent = String(suc);
    if (DOM.statFail) DOM.statFail.textContent = String(fail);
    if (DOM.statTotal) DOM.statTotal.textContent = String(suc + fail);
    if (DOM.statusBadge) DOM.statusBadge.className = `status-badge ${state.status}`;
    if (DOM.statusText) DOM.statusText.textContent = ({ idle: '空闲', running: '运行中', stopping: '停止中' }[state.status] || state.status);
    if (DOM.btnStart) DOM.btnStart.disabled = state.status !== 'idle';
    if (DOM.btnStop) DOM.btnStop.disabled = state.status === 'idle';
  } catch {}
}

async function checkProxy() {
  const proxy = (DOM.proxyInput?.value || '').trim();
  if (!proxy) return showToast('请先输入代理地址', 'error');
  if (DOM.checkProxyBtn) DOM.checkProxyBtn.disabled = true;
  if (DOM.proxyStatus) DOM.proxyStatus.textContent = '检测中...';
  try {
    const res = await apiFetch('/api/check-proxy', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ proxy }) });
    const d = await res.json();
    if (d.ok) {
      if (DOM.proxyStatus) DOM.proxyStatus.textContent = `可用，地区 ${d.loc || '--'}`;
      showToast('代理可用', 'success');
    } else {
      const m = d.error || d.detail || '检测失败';
      if (DOM.proxyStatus) DOM.proxyStatus.textContent = m;
      showToast(m, 'error');
    }
  } catch (e) {
    if (DOM.proxyStatus) DOM.proxyStatus.textContent = `检测失败: ${e.message}`;
    showToast(`检测失败: ${e.message}`, 'error');
  }
  if (DOM.checkProxyBtn) DOM.checkProxyBtn.disabled = false;
}

async function saveProxy() {
  const payload = {
    proxy: (DOM.proxyInput?.value || '').trim(),
    auto_register: !!DOM.autoRegisterCheck?.checked,
    desired_token_count: num(DOM.expectedTokenCountInput?.value),
    local_auto_maintain: !!DOM.localAutoMaintainCheck?.checked,
    local_maintain_interval_minutes: num(DOM.localMaintainIntervalInput?.value, 30),
  };
  if (DOM.saveProxyBtn) DOM.saveProxyBtn.disabled = true;
  try {
    const res = await apiFetch('/api/proxy/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const d = await res.json();
    if (!res.ok) return showToast(d.detail || '保存失败', 'error');
    showToast('代理配置已保存', 'success');
  } catch (e) {
    showToast(`保存失败: ${e.message}`, 'error');
  }
  if (DOM.saveProxyBtn) DOM.saveProxyBtn.disabled = false;
}

async function startTask() {
  const payload = { proxy: (DOM.proxyInput?.value || '').trim(), multithread: !!DOM.multithreadCheck?.checked, thread_count: num(DOM.threadCountInput?.value, 3) };
  if (DOM.btnStart) DOM.btnStart.disabled = true;
  try {
    const res = await apiFetch('/api/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const d = await res.json();
    if (!res.ok) return showToast(d.detail || '启动失败', 'error');
    state.currentSteps = {};
    renderSteps();
    showToast(`注册任务已启动 (${d.workers || payload.thread_count} 线程)`, 'success');
    updateStatus();
  } catch (e) {
    showToast(`启动失败: ${e.message}`, 'error');
  }
  if (DOM.btnStart) DOM.btnStart.disabled = false;
}

async function stopTask() {
  if (DOM.btnStop) DOM.btnStop.disabled = true;
  try {
    const res = await apiFetch('/api/stop', { method: 'POST' });
    const d = await res.json();
    if (!res.ok) return showToast(d.detail || '停止失败', 'error');
    showToast('任务正在停止', 'warn');
    updateStatus();
  } catch (e) {
    showToast(`停止失败: ${e.message}`, 'error');
  }
  if (DOM.btnStop) DOM.btnStop.disabled = false;
}
function tokenPlatforms(t) {
  const set = new Set();
  const top = Array.isArray(t?.uploaded_platforms) ? t.uploaded_platforms : [];
  const body = t?.content || {};
  const arr = Array.isArray(body.uploaded_platforms) ? body.uploaded_platforms : [];
  [...top, ...arr].forEach((p) => {
    p = String(p || '').toLowerCase().trim();
    if (p === 'cpa' || p === 'sub2api') set.add(p);
  });
  if (body.cpa_uploaded || body.cpa_synced) set.add('cpa');
  if (body.sub2api_uploaded || body.sub2api_synced || body.synced) set.add('sub2api');
  return ['cpa', 'sub2api'].filter((k) => set.has(k));
}

function quotaText(t) {
  const rem = numOrNull(t?.quota_remaining_percent);
  if (rem !== null) return `${rem.toFixed(1)}%`;
  const used = numOrNull(t?.content?.quota_used_percent);
  if (used !== null) return `${Math.max(0, 100 - used).toFixed(1)}%`;
  const st = String(t?.content?.quota_status || '').trim();
  return st || '--';
}

function fmtTime(v) {
  if (!v) return '--';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function filteredTokens() {
  const kw = state.tokenFilter.keyword.toLowerCase();
  return (state.tokens || []).filter((t) => {
    const p = tokenPlatforms(t);
    const synced = p.length > 0;
    const s = state.tokenFilter.status;
    if (s === 'synced' && !synced) return false;
    if (s === 'unsynced' && synced) return false;
    if (s === 'cpa' && !p.includes('cpa')) return false;
    if (s === 'sub2api' && !p.includes('sub2api')) return false;
    if (s === 'both' && !(p.includes('cpa') && p.includes('sub2api'))) return false;
    if (!kw) return true;
    return String(t.email || '').toLowerCase().includes(kw) || String(t.filename || '').toLowerCase().includes(kw);
  });
}

async function loadTokens() {
  try {
    const res = await apiFetch('/api/tokens');
    const d = await res.json();
    if (!res.ok) return;
    state.tokens = Array.isArray(d.tokens) ? d.tokens : [];
    state.tokenSummary = d.summary || {};
    renderTokenList();
  } catch {}
}

function applyTokenFilter() {
  state.tokenFilter.status = DOM.tokenFilterStatus?.value || 'all';
  state.tokenFilter.keyword = (DOM.tokenFilterKeyword?.value || '').trim();
  renderTokenList();
}

function resetTokenFilter() {
  state.tokenFilter = { status: 'all', keyword: '' };
  if (DOM.tokenFilterStatus) DOM.tokenFilterStatus.value = 'all';
  if (DOM.tokenFilterKeyword) DOM.tokenFilterKeyword.value = '';
  renderTokenList();
}

function renderTokenList() {
  if (!DOM.poolTokenList) return;
  const all = state.tokens || [];
  const list = filteredTokens();

  if (DOM.poolTokenCount) DOM.poolTokenCount.textContent = `${list.length}/${all.length}`;
  if (DOM.poolRecentTokenTotal) DOM.poolRecentTokenTotal.textContent = `近期总 Token: ${num(state.tokenSummary?.recent_total_tokens)}`;

  if (!list.length) {
    const msg = all.length ? '暂无符合筛选条件的数据' : '暂无 Token 数据';
    DOM.poolTokenList.innerHTML = `<div class="token-item"><div class="token-info"><div class="token-meta">${msg}</div></div></div>`;
    return;
  }

  DOM.poolTokenList.innerHTML = list.map((t) => {
    const p = tokenPlatforms(t);
    const badges = p.length
      ? p.map((x) => `<span class="platform-badge ${x}">${x === 'cpa' ? 'CPA' : 'Sub2Api'}</span>`).join('')
      : '<span class="platform-badge none">未上传</span>';
    const payload = encodeURIComponent(JSON.stringify(t?.content || {}));
    const file = encodeURIComponent(t?.filename || '');
    const usage = numOrNull(t?.recent_token_usage);
    const usageText = usage === null ? '--' : String(usage);

    return `<div class="token-item${p.length ? ' synced' : ''}" id="token-${cssEsc(t?.filename || '')}">
      <div class="token-info">
        <div class="token-email"><span class="token-email-text">${esc(t?.email || t?.filename || '')}</span></div>
        <div class="token-meta token-platforms">${badges}</div>
        <div class="token-meta">Plan: ${esc(t?.plan_type || t?.content?.plan_type || 'unknown')}</div>
        <div class="token-meta">剩余额度: ${esc(quotaText(t))} | 近期 Token: ${esc(usageText)}</div>
        <div class="token-meta">过期时间: ${esc(fmtTime(t?.expired))}</div>
      </div>
      <div class="token-actions">
        <button class="btn btn-ghost token-copy-btn" data-payload="${payload}">复制</button>
        <button class="btn btn-danger token-delete-btn" data-filename="${file}">删除</button>
      </div>
    </div>`;
  }).join('');
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try { await navigator.clipboard.writeText(text); return true; } catch {}
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

async function copyToken(payload) {
  const ok = await copyText(payload);
  showToast(ok ? 'Token 已复制到剪贴板' : '复制失败', ok ? 'success' : 'error');
}

async function copyAllRt() {
  const rts = filteredTokens().map((t) => t?.content?.refresh_token || '').filter(Boolean);
  if (!rts.length) return showToast('没有可用的 Refresh Token', 'error');
  const ok = await copyText(rts.join('\n'));
  showToast(ok ? `已复制 ${rts.length} 个 RT（当前筛选）` : '复制失败', ok ? 'success' : 'error');
}

async function deleteToken(filename) {
  if (!filename || !confirm(`确认删除 ${filename} ?`)) return;
  try {
    const res = await apiFetch(`/api/tokens/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    const d = await res.json();
    if (!res.ok) return showToast(d.detail || '删除失败', 'error');
    showToast('已删除', 'info');
    loadTokens();
  } catch (e) {
    showToast(`删除失败: ${e.message}`, 'error');
  }
}

async function loadSyncConfig() {
  try {
    const res = await apiFetch('/api/sync-config');
    const c = await res.json();
    if (!res.ok) return;

    if (DOM.sub2apiBaseUrl) DOM.sub2apiBaseUrl.value = c.base_url || '';
    if (DOM.sub2apiEmail && c.email) DOM.sub2apiEmail.value = c.email;
    if (DOM.autoSyncCheck) DOM.autoSyncCheck.checked = c.auto_sync !== 'false';
    if (DOM.uploadMode) DOM.uploadMode.value = c.upload_mode || 'snapshot';
    if (DOM.sub2apiMinCandidates) DOM.sub2apiMinCandidates.value = c.sub2api_min_candidates || 200;
    if (DOM.sub2apiAutoMaintain) DOM.sub2apiAutoMaintain.checked = !!c.sub2api_auto_maintain;
    if (DOM.sub2apiInterval) DOM.sub2apiInterval.value = c.sub2api_maintain_interval_minutes || 30;

    if (DOM.multithreadCheck) DOM.multithreadCheck.checked = !!c.multithread;
    if (DOM.threadCountInput) DOM.threadCountInput.value = c.thread_count || 3;

    if (DOM.proxyInput && c.proxy) DOM.proxyInput.value = c.proxy;
    if (DOM.autoRegisterCheck) DOM.autoRegisterCheck.checked = !!c.auto_register;
    if (DOM.expectedTokenCountInput) DOM.expectedTokenCountInput.value = c.desired_token_count || 0;
    if (DOM.localAutoMaintainCheck) DOM.localAutoMaintainCheck.checked = !!c.local_auto_maintain;
    if (DOM.localMaintainIntervalInput) DOM.localMaintainIntervalInput.value = c.local_maintain_interval_minutes || 30;
    if (DOM.apiAuthEnabled) DOM.apiAuthEnabled.checked = !!c.api_auth_enabled;
    if (DOM.apiAuthStatus && c.x_api_key_preview) {
      DOM.apiAuthStatus.textContent = `已保存 key: ${c.x_api_key_preview}`;
    }
  } catch {}
}

async function saveSyncConfig() {
  const base_url = (DOM.sub2apiBaseUrl?.value || '').trim();
  const email = (DOM.sub2apiEmail?.value || '').trim();
  const password = (DOM.sub2apiPassword?.value || '').trim();
  if (!base_url) return showToast('请填写 Sub2Api 地址', 'error');
  if (!email) return showToast('请填写管理员邮箱', 'error');

  const payload = {
    base_url, email, password, account_name: 'AutoReg', auto_sync: DOM.autoSyncCheck?.checked ? 'true' : 'false',
    upload_mode: DOM.uploadMode?.value || 'snapshot', sub2api_min_candidates: num(DOM.sub2apiMinCandidates?.value, 200),
    sub2api_auto_maintain: !!DOM.sub2apiAutoMaintain?.checked, sub2api_maintain_interval_minutes: num(DOM.sub2apiInterval?.value, 30),
    multithread: !!DOM.multithreadCheck?.checked, thread_count: num(DOM.threadCountInput?.value, 3),
    auto_register: !!DOM.autoRegisterCheck?.checked, desired_token_count: num(DOM.expectedTokenCountInput?.value),
    local_auto_maintain: !!DOM.localAutoMaintainCheck?.checked, local_maintain_interval_minutes: num(DOM.localMaintainIntervalInput?.value, 30),
  };

  if (DOM.saveSyncConfigBtn) { DOM.saveSyncConfigBtn.disabled = true; DOM.saveSyncConfigBtn.textContent = '保存中...'; }
  if (DOM.syncStatus) DOM.syncStatus.textContent = '正在验证并保存...';

  try {
    const res = await apiFetch('/api/sync-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.syncStatus) DOM.syncStatus.textContent = d.detail || '保存失败';
      return showToast(d.detail || '保存失败', 'error');
    }
    if (DOM.syncStatus) DOM.syncStatus.textContent = '配置已保存';
    showToast('Sub2Api 配置已保存', 'success');
    pollSub2ApiPoolStatus();
  } catch (e) {
    if (DOM.syncStatus) DOM.syncStatus.textContent = `保存失败: ${e.message}`;
    showToast(`保存失败: ${e.message}`, 'error');
  }

  if (DOM.saveSyncConfigBtn) { DOM.saveSyncConfigBtn.disabled = false; DOM.saveSyncConfigBtn.textContent = '保存 Sub2Api 配置'; }
}

async function saveUploadMode() {
  const upload_mode = DOM.uploadMode?.value || 'snapshot';
  if (!DOM.uploadModeSaveBtn) return;
  const old = DOM.uploadModeSaveBtn.textContent;
  DOM.uploadModeSaveBtn.disabled = true;
  DOM.uploadModeSaveBtn.textContent = '保存中...';
  if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = '正在保存策略...';

  try {
    const res = await apiFetch('/api/upload-mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ upload_mode }) });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = d.detail || '保存失败';
      return showToast(d.detail || '保存失败', 'error');
    }
    const label = upload_mode === 'decoupled' ? '双平台同传（单账号双上传）' : '串行补平台（先 CPA 后 Sub2Api）';
    if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = `已保存：${label}`;
    showToast(`上传策略已保存：${label}`, 'success');
  } catch (e) {
    if (DOM.uploadModeStatus) DOM.uploadModeStatus.textContent = `保存失败: ${e.message}`;
    showToast(`保存失败: ${e.message}`, 'error');
  }

  DOM.uploadModeSaveBtn.disabled = false;
  DOM.uploadModeSaveBtn.textContent = old || '保存上传模式';
}

async function batchSync() {
  if (!DOM.poolPwSyncBtn) return;
  const old = DOM.poolPwSyncBtn.textContent;
  DOM.poolPwSyncBtn.disabled = true;
  DOM.poolPwSyncBtn.textContent = '同步中...';
  try {
    const res = await apiFetch('/api/sync-batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filenames: [] }) });
    const d = await res.json();
    if (!res.ok) return showToast(d.detail || '同步失败', 'error');
    showToast(`同步完成：共 ${d.total}，成功 ${d.ok}，失败 ${d.fail}`, d.fail > 0 ? 'warn' : 'success');
    loadTokens();
    pollSub2ApiPoolStatus();
  } catch (e) {
    showToast(`同步失败: ${e.message}`, 'error');
  }
  DOM.poolPwSyncBtn.disabled = false;
  DOM.poolPwSyncBtn.textContent = old || '批量同步 Sub2Api';
}
const PROVIDER_DEFAULTS = {
  zenproxy_api: 'https://zenproxy.top/api/fetch',
  dreamy_socks5_pool: 'socks5://127.0.0.1:1080',
  docker_warp_socks: 'socks5://127.0.0.1:9091',
};

function normalizeProvider(v) {
  v = String(v || 'zenproxy_api').trim().toLowerCase();
  if (['dreamy_socks5_pool','dreamy_socks5','socks5_proxy'].includes(v)) return 'dreamy_socks5_pool';
  if (['docker_warp_socks','warp_socks','warp'].includes(v)) return 'docker_warp_socks';
  return 'zenproxy_api';
}

function isZenProvider(v) {
  return normalizeProvider(v) === 'zenproxy_api';
}

function applyProviderUI(provider, setDefault = false) {
  provider = normalizeProvider(provider);
  const zen = isZenProvider(provider);
  if (DOM.proxyPoolProvider) DOM.proxyPoolProvider.value = provider;
  if (DOM.proxyPoolApiUrlLabel) DOM.proxyPoolApiUrlLabel.textContent = zen ? '抓取 API 地址' : '代理 URL';
  if (DOM.proxyPoolZenAuthRow) DOM.proxyPoolZenAuthRow.style.display = zen ? 'grid' : 'none';
  if (DOM.proxyPoolZenFilterRow) DOM.proxyPoolZenFilterRow.style.display = zen ? 'grid' : 'none';
  if (setDefault && DOM.proxyPoolApiUrl) DOM.proxyPoolApiUrl.value = PROVIDER_DEFAULTS[provider] || PROVIDER_DEFAULTS.zenproxy_api;
}

function onProxyPoolProviderChange() {
  applyProviderUI(DOM.proxyPoolProvider?.value || 'zenproxy_api', true);
}

function proxyPoolPayload() {
  const provider = normalizeProvider(DOM.proxyPoolProvider?.value || 'zenproxy_api');
  return {
    proxy_pool_enabled: !!DOM.proxyPoolEnabled?.checked,
    proxy_pool_provider: provider,
    proxy_pool_api_url: (DOM.proxyPoolApiUrl?.value || '').trim() || (PROVIDER_DEFAULTS[provider] || ''),
    proxy_pool_auth_mode: String(DOM.proxyPoolAuthMode?.value || 'query').trim().toLowerCase(),
    proxy_pool_api_key: (DOM.proxyPoolApiKey?.value || '').trim(),
    proxy_pool_count: num(DOM.proxyPoolCount?.value, 1),
    proxy_pool_country: String(DOM.proxyPoolCountry?.value || 'US').trim().toUpperCase() || 'US',
  };
}

async function loadProxyPoolConfig() {
  try {
    const res = await apiFetch('/api/proxy-pool/config');
    const d = await res.json();
    if (!res.ok) return;

    const provider = normalizeProvider(d.proxy_pool_provider || 'zenproxy_api');
    if (DOM.proxyPoolEnabled) DOM.proxyPoolEnabled.checked = !!d.proxy_pool_enabled;
    applyProviderUI(provider, false);

    if (DOM.proxyPoolApiUrl) DOM.proxyPoolApiUrl.value = d.proxy_pool_api_url || PROVIDER_DEFAULTS[provider] || '';
    if (DOM.proxyPoolAuthMode) DOM.proxyPoolAuthMode.value = String(d.proxy_pool_auth_mode || 'query').toLowerCase();
    if (DOM.proxyPoolApiKey) {
      DOM.proxyPoolApiKey.value = '';
      if (d.proxy_pool_api_key_preview) DOM.proxyPoolApiKey.placeholder = d.proxy_pool_api_key_preview;
    }
    if (DOM.proxyPoolCount) DOM.proxyPoolCount.value = d.proxy_pool_count || 1;
    if (DOM.proxyPoolCountry) DOM.proxyPoolCountry.value = d.proxy_pool_country || 'US';
  } catch {}
}

async function saveProxyPoolConfig() {
  if (!DOM.proxyPoolSaveBtn) return;
  const old = DOM.proxyPoolSaveBtn.textContent;
  DOM.proxyPoolSaveBtn.disabled = true;
  DOM.proxyPoolSaveBtn.textContent = '保存中...';

  try {
    const res = await apiFetch('/api/proxy-pool/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(proxyPoolPayload()),
    });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = d.detail || '保存失败';
      return showToast(d.detail || '保存失败', 'error');
    }
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = '代理池配置已保存';
    showToast('代理池配置已保存', 'success');
  } catch (e) {
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = `保存失败: ${e.message}`;
    showToast(`保存失败: ${e.message}`, 'error');
  }

  DOM.proxyPoolSaveBtn.disabled = false;
  DOM.proxyPoolSaveBtn.textContent = old || '保存代理池配置';
}

async function testProxyPoolFetch() {
  if (!DOM.proxyPoolTestBtn) return;
  const old = DOM.proxyPoolTestBtn.textContent;
  DOM.proxyPoolTestBtn.disabled = true;
  DOM.proxyPoolTestBtn.textContent = '测试中...';
  if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = '正在获取...';

  const raw = proxyPoolPayload();
  const payload = {
    enabled: raw.proxy_pool_enabled,
    provider: raw.proxy_pool_provider,
    api_url: raw.proxy_pool_api_url,
    auth_mode: raw.proxy_pool_auth_mode,
    api_key: raw.proxy_pool_api_key,
    count: raw.proxy_pool_count,
    country: raw.proxy_pool_country,
  };

  try {
    const res = await apiFetch('/api/proxy-pool/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await res.json();

    if (!res.ok || !d.ok) {
      if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = d.error || d.detail || '测试失败';
      return showToast(d.error || d.detail || '测试失败', 'error');
    }

    const msg = `测试成功：${d.proxy || '--'}${d.loc ? `，loc=${d.loc}` : ''}${d.supported === false ? '，不支持注册区(CN/HK)' : ''}${d.trace_error ? `，trace: ${d.trace_error}` : ''}`;
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = msg;
    showToast('代理池取代理成功', 'success');
  } catch (e) {
    if (DOM.proxyPoolStatus) DOM.proxyPoolStatus.textContent = `测试失败: ${e.message}`;
    showToast(`测试失败: ${e.message}`, 'error');
  }

  DOM.proxyPoolTestBtn.disabled = false;
  DOM.proxyPoolTestBtn.textContent = old || '测试代理池获取';
}

async function loadPoolConfig() {
  try {
    const res = await apiFetch('/api/pool/config');
    const d = await res.json();
    if (!res.ok) return;

    if (DOM.cpaBaseUrl) DOM.cpaBaseUrl.value = d.cpa_base_url || '';
    if (DOM.cpaToken) {
      DOM.cpaToken.value = '';
      if (d.cpa_token_preview) DOM.cpaToken.placeholder = d.cpa_token_preview;
    }
    if (DOM.cpaMinCandidates) DOM.cpaMinCandidates.value = d.min_candidates || 800;
    if (DOM.cpaUsedPercent) DOM.cpaUsedPercent.value = d.used_percent_threshold || 95;
    if (DOM.cpaAutoMaintain) DOM.cpaAutoMaintain.checked = !!d.auto_maintain;
    if (DOM.cpaInterval) DOM.cpaInterval.value = d.maintain_interval_minutes || 30;
  } catch {}
}

async function savePoolConfig() {
  const payload = {
    cpa_base_url: (DOM.cpaBaseUrl?.value || '').trim(),
    cpa_token: (DOM.cpaToken?.value || '').trim(),
    min_candidates: num(DOM.cpaMinCandidates?.value, 800),
    used_percent_threshold: num(DOM.cpaUsedPercent?.value, 95),
    auto_maintain: !!DOM.cpaAutoMaintain?.checked,
    maintain_interval_minutes: num(DOM.cpaInterval?.value, 30),
  };

  if (DOM.cpaSaveBtn) DOM.cpaSaveBtn.disabled = true;
  try {
    const res = await apiFetch('/api/pool/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.cpaStatus) DOM.cpaStatus.textContent = d.detail || '保存失败';
      return showToast(d.detail || '保存失败', 'error');
    }
    if (DOM.cpaStatus) DOM.cpaStatus.textContent = '配置已保存';
    showToast('CPA 配置已保存', 'success');
    pollPoolStatus();
  } catch (e) {
    if (DOM.cpaStatus) DOM.cpaStatus.textContent = `保存失败: ${e.message}`;
    showToast(`保存失败: ${e.message}`, 'error');
  }
  if (DOM.cpaSaveBtn) DOM.cpaSaveBtn.disabled = false;
}

async function testCpaConnection() {
  if (!DOM.cpaTestBtn) return;
  DOM.cpaTestBtn.disabled = true;
  if (DOM.cpaStatus) DOM.cpaStatus.textContent = '测试中...';

  try {
    const res = await apiFetch('/api/pool/check', { method: 'POST' });
    const d = await res.json();
    if (d.ok) {
      if (DOM.cpaStatus) DOM.cpaStatus.textContent = d.message || '连接成功';
      showToast('CPA 连接成功', 'success');
    } else {
      const m = d.message || d.detail || '连接失败';
      if (DOM.cpaStatus) DOM.cpaStatus.textContent = m;
      showToast(m, 'error');
    }
  } catch (e) {
    if (DOM.cpaStatus) DOM.cpaStatus.textContent = `测试失败: ${e.message}`;
    showToast(`测试失败: ${e.message}`, 'error');
  }

  DOM.cpaTestBtn.disabled = false;
}

function headerLevel(fillPct, errorCount) {
  if (errorCount > 0) return 'danger';
  if (fillPct > 110) return 'over';
  if (fillPct >= 100) return 'ok';
  if (fillPct >= 80) return 'warn';
  return 'danger';
}

function headerDelta(fillPct) {
  if (!Number.isFinite(fillPct)) return '--';
  const d = Math.round(fillPct - 100);
  if (d === 0) return '0%';
  return `${d > 0 ? '+' : ''}${d}%`;
}

function updateHeaderSub2Api(data) {
  if (!data) {
    if (DOM.headerSub2apiLabel) DOM.headerSub2apiLabel.textContent = '-- / --';
    if (DOM.headerSub2apiDelta) DOM.headerSub2apiDelta.textContent = '--';
    if (DOM.headerSub2apiBar) {
      DOM.headerSub2apiBar.style.width = '0%';
      DOM.headerSub2apiBar.className = 'pool-chip-fill';
    }
    if (DOM.headerSub2apiChip) DOM.headerSub2apiChip.className = 'pool-chip status-idle';
    return;
  }
  const lv = headerLevel(data.fillPct, data.error);
  if (DOM.headerSub2apiLabel) DOM.headerSub2apiLabel.textContent = `${data.normal} / ${data.threshold}`;
  if (DOM.headerSub2apiDelta) DOM.headerSub2apiDelta.textContent = headerDelta(data.fillPct);
  if (DOM.headerSub2apiBar) {
    DOM.headerSub2apiBar.style.width = `${Math.min(100, data.fillPct)}%`;
    DOM.headerSub2apiBar.className = `pool-chip-fill ${lv}`;
  }
  if (DOM.headerSub2apiChip) DOM.headerSub2apiChip.className = `pool-chip status-${lv}`;
}

function updateHeaderCpa(data) {
  if (!data) {
    if (DOM.headerCpaLabel) DOM.headerCpaLabel.textContent = '-- / --';
    if (DOM.headerCpaDelta) DOM.headerCpaDelta.textContent = '--';
    if (DOM.headerCpaBar) {
      DOM.headerCpaBar.style.width = '0%';
      DOM.headerCpaBar.className = 'pool-chip-fill';
    }
    if (DOM.headerCpaChip) DOM.headerCpaChip.className = 'pool-chip status-idle';
    return;
  }
  const lv = headerLevel(data.fillPct, data.errorCount);
  if (DOM.headerCpaLabel) DOM.headerCpaLabel.textContent = `${data.candidates} / ${data.threshold}`;
  if (DOM.headerCpaDelta) DOM.headerCpaDelta.textContent = headerDelta(data.fillPct);
  if (DOM.headerCpaBar) {
    DOM.headerCpaBar.style.width = `${Math.min(100, data.fillPct)}%`;
    DOM.headerCpaBar.className = `pool-chip-fill ${lv}`;
  }
  if (DOM.headerCpaChip) DOM.headerCpaChip.className = `pool-chip status-${lv}`;
}

async function pollPoolStatus() {
  try {
    const res = await apiFetch('/api/pool/status');
    const d = await res.json();

    if (!d.configured) {
      ['poolTotal','poolCandidates','poolError','poolThreshold','poolPercent'].forEach((k) => { if (DOM[k]) DOM[k].textContent = '--'; });
      return updateHeaderCpa(null);
    }

    const candidates = num(d.candidates);
    const errorCount = num(d.error_count);
    const threshold = num(d.threshold);
    const fillPct = threshold > 0 ? Math.round((candidates / threshold) * 100) : 100;

    if (DOM.poolTotal) DOM.poolTotal.textContent = String(num(d.total));
    if (DOM.poolCandidates) DOM.poolCandidates.textContent = String(candidates);
    if (DOM.poolError) DOM.poolError.textContent = String(errorCount);
    if (DOM.poolThreshold) DOM.poolThreshold.textContent = String(threshold);
    if (DOM.poolPercent) DOM.poolPercent.textContent = `${fillPct}%`;

    updateHeaderCpa({ candidates, threshold, fillPct, errorCount });
  } catch {}
}

async function triggerMaintenance() {
  if (!DOM.poolMaintainBtn) return;
  const old = DOM.poolMaintainBtn.textContent;
  DOM.poolMaintainBtn.disabled = true;
  DOM.poolMaintainBtn.textContent = '维护中...';
  if (DOM.poolMaintainStatus) DOM.poolMaintainStatus.textContent = '正在探测并清理无效账号...';

  try {
    const res = await apiFetch('/api/pool/maintain', { method: 'POST' });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.poolMaintainStatus) DOM.poolMaintainStatus.textContent = d.detail || '维护失败';
      return showToast(d.detail || '维护失败', 'error');
    }
    const msg = `维护完成: 无效 ${num(d.invalid_count)}, 已删除 ${num(d.deleted_ok)}, 失败 ${num(d.deleted_fail)}`;
    if (DOM.poolMaintainStatus) DOM.poolMaintainStatus.textContent = msg;
    showToast(msg, 'success');
    pollPoolStatus();
    loadTokens();
  } catch (e) {
    if (DOM.poolMaintainStatus) DOM.poolMaintainStatus.textContent = `维护失败: ${e.message}`;
    showToast(`维护失败: ${e.message}`, 'error');
  }

  DOM.poolMaintainBtn.disabled = false;
  DOM.poolMaintainBtn.textContent = old || '维护';
}

async function pollSub2ApiPoolStatus() {
  try {
    const res = await apiFetch('/api/sub2api/pool/status');
    const d = await res.json();

    if (d.configured && d.error) {
      if (DOM.sub2apiPoolMaintainStatus) DOM.sub2apiPoolMaintainStatus.textContent = `Sub2Api 状态获取失败: ${d.error}`;
      return updateHeaderSub2Api(null);
    }

    if (!d.configured) {
      ['sub2apiPoolTotal','sub2apiPoolNormal','sub2apiPoolError','sub2apiPoolThreshold','sub2apiPoolPercent'].forEach((k) => { if (DOM[k]) DOM[k].textContent = '--'; });
      return updateHeaderSub2Api(null);
    }

    const normal = num(d.candidates);
    const error = num(d.error_count);
    const total = num(d.total);
    const threshold = num(d.threshold);
    const fillPct = threshold > 0 ? Math.round((normal / threshold) * 100) : 100;

    if (DOM.sub2apiPoolTotal) DOM.sub2apiPoolTotal.textContent = String(total);
    if (DOM.sub2apiPoolNormal) DOM.sub2apiPoolNormal.textContent = String(normal);
    if (DOM.sub2apiPoolError) DOM.sub2apiPoolError.textContent = String(error);
    if (DOM.sub2apiPoolThreshold) DOM.sub2apiPoolThreshold.textContent = String(threshold);
    if (DOM.sub2apiPoolPercent) DOM.sub2apiPoolPercent.textContent = `${fillPct}%`;

    updateHeaderSub2Api({ normal, threshold, fillPct, error });
  } catch {}
}

async function triggerSub2ApiMaintenance() {
  if (!DOM.sub2apiPoolMaintainBtn) return;
  const old = DOM.sub2apiPoolMaintainBtn.textContent;
  DOM.sub2apiPoolMaintainBtn.disabled = true;
  DOM.sub2apiPoolMaintainBtn.textContent = '维护中...';
  if (DOM.sub2apiPoolMaintainStatus) DOM.sub2apiPoolMaintainStatus.textContent = '正在刷新异常账号并清理...';

  try {
    const res = await apiFetch('/api/sub2api/pool/maintain', { method: 'POST' });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.sub2apiPoolMaintainStatus) DOM.sub2apiPoolMaintainStatus.textContent = d.detail || '维护失败';
      return showToast(d.detail || '维护失败', 'error');
    }
    const msg = `维护完成: 异常 ${num(d.error_count)}, 刷新 ${num(d.refreshed)}, 删除 ${num(d.deleted_ok)}, 失败 ${num(d.deleted_fail)}`;
    if (DOM.sub2apiPoolMaintainStatus) DOM.sub2apiPoolMaintainStatus.textContent = msg;
    showToast(msg, 'success');
    pollSub2ApiPoolStatus();
  } catch (e) {
    if (DOM.sub2apiPoolMaintainStatus) DOM.sub2apiPoolMaintainStatus.textContent = `维护失败: ${e.message}`;
    showToast(`维护失败: ${e.message}`, 'error');
  }

  DOM.sub2apiPoolMaintainBtn.disabled = false;
  DOM.sub2apiPoolMaintainBtn.textContent = old || '维护';
}

async function testSub2ApiPoolConnection() {
  if (!DOM.sub2apiTestPoolBtn) return;
  DOM.sub2apiTestPoolBtn.disabled = true;
  if (DOM.syncStatus) DOM.syncStatus.textContent = '测试连接中...';

  try {
    const res = await apiFetch('/api/sub2api/pool/check', { method: 'POST' });
    const d = await res.json();
    if (d.ok) {
      if (DOM.syncStatus) DOM.syncStatus.textContent = d.message || '连接成功';
      showToast('Sub2Api 池连接成功', 'success');
    } else {
      const m = d.message || d.detail || '连接失败';
      if (DOM.syncStatus) DOM.syncStatus.textContent = m;
      showToast('Sub2Api 池连接失败', 'error');
    }
  } catch (e) {
    if (DOM.syncStatus) DOM.syncStatus.textContent = `连接失败: ${e.message}`;
    showToast(`连接失败: ${e.message}`, 'error');
  }

  DOM.sub2apiTestPoolBtn.disabled = false;
}
function initMailCheckboxes() {
  document.querySelectorAll('.mail-provider-check').forEach((cb) => {
    cb.addEventListener('change', () => {
      const cfg = cb.closest('.provider-item')?.querySelector('.provider-config');
      if (cfg) cfg.style.display = cb.checked ? 'grid' : 'none';
    });
  });
}

async function loadMailConfig() {
  try {
    const res = await apiFetch('/api/mail/config');
    const d = await res.json();
    if (!res.ok) return;

    const providers = d.mail_providers || [d.mail_provider || 'mailtm'];
    const providerCfgs = d.mail_provider_configs || {};

    document.querySelectorAll('.provider-item').forEach((item) => {
      const name = item.dataset.provider;
      const cb = item.querySelector('.mail-provider-check');
      const checked = providers.includes(name);
      if (cb) cb.checked = checked;

      const panel = item.querySelector('.provider-config');
      if (panel) panel.style.display = checked ? 'grid' : 'none';

      const cfg = providerCfgs[name] || {};
      item.querySelectorAll('[data-key]').forEach((el) => {
        const key = el.dataset.key;
        const previewKey = `${key}_preview`;
        if (Object.prototype.hasOwnProperty.call(cfg, key)) el.value = cfg[key] ?? '';
        else if (cfg[previewKey]) el.placeholder = cfg[previewKey];
      });
    });

    if (DOM.mailStrategySelect) DOM.mailStrategySelect.value = d.mail_strategy || 'round_robin';

    if (!d.mail_providers && d.mail_config) {
      const item = document.querySelector(`.provider-item[data-provider="${d.mail_provider || 'mailtm'}"]`);
      if (item) {
        item.querySelectorAll('[data-key]').forEach((el) => {
          const key = el.dataset.key;
          if (d.mail_config[key]) el.value = d.mail_config[key];
        });
      }
    }
  } catch {}
}

function collectMailPayload() {
  const providers = [];
  const providerCfgs = {};

  document.querySelectorAll('.provider-item').forEach((item) => {
    const name = item.dataset.provider;
    const cb = item.querySelector('.mail-provider-check');
    if (!cb?.checked || !name) return;

    providers.push(name);
    providerCfgs[name] = {};

    item.querySelectorAll('[data-key]').forEach((el) => {
      const key = el.dataset.key;
      const val = String(el.value || '').trim();
      if (val) providerCfgs[name][key] = val;
    });
  });

  const main = providers[0] || 'mailtm';
  return {
    mail_provider: main,
    mail_config: { ...(providerCfgs[main] || {}) },
    mail_providers: providers,
    mail_provider_configs: providerCfgs,
    mail_strategy: DOM.mailStrategySelect?.value || 'round_robin',
  };
}

async function saveMailConfig() {
  if (!DOM.mailSaveBtn) return;
  const payload = collectMailPayload();
  if (!payload.mail_providers.length) return showToast('请至少勾选一个邮箱提供商', 'error');

  DOM.mailSaveBtn.disabled = true;
  if (DOM.mailStatus) DOM.mailStatus.textContent = '保存中...';

  try {
    const res = await apiFetch('/api/mail/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.mailStatus) DOM.mailStatus.textContent = d.detail || '保存失败';
      return showToast(d.detail || '保存失败', 'error');
    }
    if (DOM.mailStatus) DOM.mailStatus.textContent = '配置已保存';
    showToast('邮箱配置已保存', 'success');
  } catch (e) {
    if (DOM.mailStatus) DOM.mailStatus.textContent = `保存失败: ${e.message}`;
    showToast(`保存失败: ${e.message}`, 'error');
  }

  DOM.mailSaveBtn.disabled = false;
}

async function testMailConnection() {
  if (!DOM.mailTestBtn) return;
  DOM.mailTestBtn.disabled = true;
  if (DOM.mailStatus) DOM.mailStatus.textContent = '测试中...';

  try {
    const res = await apiFetch('/api/mail/test', { method: 'POST' });
    const d = await res.json();
    if (!res.ok) {
      if (DOM.mailStatus) DOM.mailStatus.textContent = d.detail || '测试失败';
      return showToast(d.detail || '测试失败', 'error');
    }
    if (DOM.mailStatus) DOM.mailStatus.textContent = d.message || (d.ok ? '连接成功' : '连接失败');
    showToast(d.ok ? '邮箱测试通过' : '邮箱测试失败', d.ok ? 'success' : 'error');
  } catch (e) {
    if (DOM.mailStatus) DOM.mailStatus.textContent = `测试失败: ${e.message}`;
    showToast(`测试失败: ${e.message}`, 'error');
  }

  DOM.mailTestBtn.disabled = false;
}

function showToast(msg, type = 'info') {
  const box = $('toastContainer');
  if (!box) return;
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.innerHTML = `<span class="toast-icon">${TOAST_ICONS[type] || TOAST_ICONS.info}</span><span>${esc(msg)}</span>`;
  box.appendChild(node);
  setTimeout(() => {
    node.style.animation = 'toast-out .22s ease forwards';
    node.addEventListener('animationend', () => node.remove(), { once: true });
  }, 3200);
}
