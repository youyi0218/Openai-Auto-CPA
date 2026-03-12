(function () {
  if (window.__OPA_BRIDGE_INIT__) return;
  window.__OPA_BRIDGE_INIT__ = true;

  const SECRET_MASK = '********';
  const MAIL_PROVIDERS = [
    { id: 'mailtm', label: 'Mail.tm' },
    { id: 'moemail', label: 'MoeMail' },
    { id: 'duckmail', label: 'DuckMail' },
    { id: 'cloudflare_temp_email', label: 'Cloudflare Temp Email' }
  ];
  const PROXY_POOL_PROVIDERS = [
    { id: 'zenproxy_api', label: 'ZenProxy API', defaultUrl: 'https://zenproxy.top/api/fetch' },
    { id: 'dreamy_socks5_pool', label: 'Dreamy SOCKS5', defaultUrl: 'socks5://127.0.0.1:1080' },
    { id: 'docker_warp_socks', label: 'Docker WARP SOCKS', defaultUrl: 'socks5://127.0.0.1:9091' }
  ];
  const BRIDGE_ROUTE_HASH = '#/?opa_view=orchestrator';
  const BRIDGE_ROUTE_COMPAT_HASH = '#/orchestrator';
  const BRIDGE_ROUTE_PATH = '/';
  const BRIDGE_ROUTE_QUERY_KEY = 'opa_view';
  const BRIDGE_ROUTE_QUERY_VALUE = 'orchestrator';
  const BRIDGE_NAV_ID = 'opa-orchestrator-nav-item';
  const BRIDGE_NAV_WRAP_ID = 'opa-orchestrator-nav-wrap';
  const BRIDGE_HOST_ID = 'opa-orchestrator-route-host';
  const BRIDGE_PREV_CURRENT_ATTR = 'data-opa-prev-aria-current';
  const BRIDGE_API_KEY_STORAGE = 'opa_bridge_api_key';
  const BRIDGE_API_KEY_QUERY = 'api_key';
  const RUN_LOG_STREAM_ID = 'opaRunStream';
  const RUN_LOG_CLEAR_ID = 'opaRunLogClear';
  const SECTION_LOG_IDS = {
    realtime: 'opaLogRealtime',
    proxy_pool: 'opaLogProxyPool',
    register: 'opaLogRegister',
    maintain: 'opaLogMaintain',
    cpa: 'opaLogCpa',
    mail: 'opaLogMail',
  };

  let bridgeRoot = null;
  let bridgeSyncTimer = null;
  let bridgeObserver = null;
  let bridgePrevActive = false;
  let bridgeRuntimeApiKey = '';
  let bridgeApiPrompted = false;
  let runLogStream = null;
  let runLogErrorLogged = false;
  let runLogReconnectTimer = null;
  const bridgeSecretCache = new Map();

  const qs = (id) => document.getElementById(id);
  const qsa = (selector) => Array.from(document.querySelectorAll(selector));

  function normalizeApiKey(value) {
    const raw = String(value == null ? '' : value).trim();
    return raw.replace(/^"+|"+$/g, '').trim();
  }

  function readApiKeyFromUrl() {
    try {
      const sp = new URLSearchParams(window.location.search || '');
      const fromSearch = normalizeApiKey(sp.get(BRIDGE_API_KEY_QUERY) || '');
      if (fromSearch) return fromSearch;

      const hash = String(window.location.hash || '');
      const idx = hash.indexOf('?');
      if (idx >= 0) {
        const hashSearch = new URLSearchParams(hash.slice(idx + 1));
        const fromHash = normalizeApiKey(hashSearch.get(BRIDGE_API_KEY_QUERY) || '');
        if (fromHash) return fromHash;
      }
    } catch (_) {}
    return '';
  }

  function readApiKeyFromStorage() {
    const candidates = [BRIDGE_API_KEY_STORAGE, 'x_api_key', 'api_key'];
    for (const key of candidates) {
      try {
        const value = localStorage.getItem(key);
        if (!value) continue;
        const normalized = normalizeApiKey(value);
        if (normalized && !normalized.startsWith('enc::v1::')) return normalized;
      } catch (_) {}
    }
    return '';
  }

  function getApiKey() {
    if (bridgeRuntimeApiKey) return bridgeRuntimeApiKey;
    const fromUrl = readApiKeyFromUrl();
    if (fromUrl) {
      bridgeRuntimeApiKey = fromUrl;
      try { localStorage.setItem(BRIDGE_API_KEY_STORAGE, fromUrl); } catch (_) {}
      return bridgeRuntimeApiKey;
    }
    const fromStorage = readApiKeyFromStorage();
    if (fromStorage) {
      bridgeRuntimeApiKey = fromStorage;
      return bridgeRuntimeApiKey;
    }
    return '';
  }

  function setApiKey(value) {
    const key = normalizeApiKey(value);
    bridgeRuntimeApiKey = key;
    try {
      if (key) localStorage.setItem(BRIDGE_API_KEY_STORAGE, key);
      else localStorage.removeItem(BRIDGE_API_KEY_STORAGE);
    } catch (_) {}
  }

  function buildAuthedRequest(path, init, apiKey) {
    const headers = new Headers((init && init.headers) || {});
    let requestPath = path;
    if (apiKey) {
      headers.set('x-api-key', apiKey);
      if (!headers.has('authorization')) {
        headers.set('authorization', 'Bearer ' + apiKey);
      }
      try {
        const url = new URL(path, window.location.origin);
        url.searchParams.set(BRIDGE_API_KEY_QUERY, apiKey);
        requestPath = url.pathname + url.search;
      } catch (_) {
        requestPath = path + (String(path).includes('?') ? '&' : '?') + BRIDGE_API_KEY_QUERY + '=' + encodeURIComponent(apiKey);
      }
    }
    return {
      path: requestPath,
      init: Object.assign({}, init || {}, { headers }),
    };
  }

  function promptApiKeyIfNeeded() {
    if (bridgeApiPrompted) return '';
    bridgeApiPrompted = true;
    const input = window.prompt('检测到已开启 API 鉴权，请输入 API Key（x-api-key）');
    return normalizeApiKey(input || '');
  }

  function api(path, init, allowRetryPrompt) {
    const canRetry = allowRetryPrompt !== false;
    const apiKey = getApiKey();
    const req = buildAuthedRequest(path, init, apiKey);
    return fetch(req.path, req.init).then(async (res) => {
      let data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
        if (res.status === 401) {
          if (!apiKey && canRetry) {
            const prompted = promptApiKeyIfNeeded();
            if (prompted) {
              setApiKey(prompted);
              return api(path, init, false);
            }
          }
          const hint = apiKey
            ? '401 未授权：当前 API Key 无效，请在系统配置更新后刷新页面。'
            : '401 未授权：请在 URL 添加 ?api_key=你的key，或先在系统配置里关闭鉴权。';
          throw new Error(hint);
        }
        const msg = data.detail || data.message || ('HTTP ' + res.status);
        throw new Error(msg);
      }
      return data;
    });
  }

  function num(v, d) {
    const n = Number(v);
    return Number.isFinite(n) ? n : d;
  }

  function status(text) {
    const el = qs('opaStatus');
    if (el) el.textContent = text;
  }

  function logSection(section, text, level) {
    const id = SECTION_LOG_IDS[section];
    const el = id ? qs(id) : null;
    const line = '[' + new Date().toLocaleTimeString() + '] ' + String(text || '');
    if (el) {
      const row = document.createElement('div');
      row.className = 'opa-bridge-log-line' + (level === 'error' ? ' error' : '');
      row.textContent = line;
      el.prepend(row);
      while (el.childElementCount > 8) {
        el.removeChild(el.lastElementChild);
      }
    }
    status(String(text || ''));
  }

  function appendRunLogLine(payload) {
    const box = qs(RUN_LOG_STREAM_ID);
    if (!box) return;

    const levelRaw = String(payload?.level || 'info').trim().toLowerCase();
    const level = ['info', 'success', 'error', 'warn', 'warning', 'connected'].includes(levelRaw) ? levelRaw : 'info';
    const step = String(payload?.step || '').trim();
    const msg = String(payload?.message || '').trim();
    if (!msg) return;
    const ts = String(payload?.ts || new Date().toLocaleTimeString()).trim();

    const row = document.createElement('div');
    row.className = 'opa-bridge-runlog-line level-' + (level === 'warning' ? 'warn' : level);
    row.textContent = '[' + ts + '] ' + (step ? '[' + step + '] ' : '') + msg;
    box.appendChild(row);

    while (box.childElementCount > 600) {
      box.removeChild(box.firstElementChild);
    }
    box.scrollTop = box.scrollHeight;
  }

  function openRunLogStream() {
    if (runLogStream || runLogReconnectTimer) return;

    let streamUrl = '/api/logs';
    const apiKey = getApiKey();
    if (apiKey) {
      streamUrl += '?api_key=' + encodeURIComponent(apiKey);
    }

    try {
      runLogStream = new EventSource(streamUrl);
    } catch (e) {
      appendRunLogLine({ level: 'error', step: 'logs', message: '打开日志流失败：' + (e?.message || e) });
      return;
    }

    runLogStream.onopen = function () {
      runLogErrorLogged = false;
      appendRunLogLine({ level: 'connected', step: 'logs', message: '实时日志流已连接' });
    };

    runLogStream.onmessage = function (evt) {
      if (!evt || !evt.data) return;
      try {
        const payload = JSON.parse(evt.data);
        appendRunLogLine(payload);
      } catch (_) {
        appendRunLogLine({ level: 'warn', step: 'logs', message: String(evt.data || '') });
      }
    };

    runLogStream.onerror = function () {
      if (runLogErrorLogged) return;
      runLogErrorLogged = true;
      appendRunLogLine({ level: 'warn', step: 'logs', message: '日志流已断开，正在重连...' });
      if (runLogStream && runLogStream.readyState === 2) {
        closeRunLogStream();
        if (!runLogReconnectTimer) {
          runLogReconnectTimer = window.setTimeout(() => {
            runLogReconnectTimer = null;
            openRunLogStream();
          }, 1500);
        }
      }
    };
  }

  function closeRunLogStream() {
    if (runLogReconnectTimer) {
      window.clearTimeout(runLogReconnectTimer);
      runLogReconnectTimer = null;
    }
    if (!runLogStream) return;
    try {
      runLogStream.close();
    } catch (_) {}
    runLogStream = null;
  }

  function getSecretState(el) {
    const raw = String(el?.value || '');
    const trimmed = raw.trim();
    const dirty = el?.dataset?.dirty === '1';
    const masked = el?.dataset?.masked === '1' && raw === SECRET_MASK && !dirty;
    const clear = dirty && trimmed === '';
    return { raw, trimmed, dirty, masked, clear };
  }

  function setSecretMasked(el, hasStored) {
    if (!el) return;
    el.value = hasStored ? SECRET_MASK : '';
    el.dataset.masked = hasStored ? '1' : '0';
    el.dataset.dirty = '0';
    if (!hasStored) {
      bridgeSecretCache.delete(el.id);
    }
    el.type = 'password';
    const eye = document.querySelector('[data-eye-for="' + el.id + '"]');
    if (eye) eye.textContent = '显示';
  }

  function markSecretDirty(el) {
    if (!el || el.dataset.bound === '1') return;
    const onDirty = function () {
      el.dataset.dirty = '1';
      if (el.value !== SECRET_MASK) {
        el.dataset.masked = '0';
        const val = String(el.value || '').trim();
        if (val) bridgeSecretCache.set(el.id, val);
        else bridgeSecretCache.delete(el.id);
      }
    };
    el.addEventListener('input', onDirty);
    el.addEventListener('change', onDirty);
    el.dataset.bound = '1';
    if (!el.dataset.dirty) el.dataset.dirty = '0';
    if (!el.dataset.masked) el.dataset.masked = '0';
  }

  function bindSecretEye(inputId, btnId) {
    const ipt = qs(inputId);
    const btn = qs(btnId);
    if (!ipt || !btn) return;
    btn.addEventListener('click', function () {
      if (ipt.dataset.masked === '1' && String(ipt.value || '') === SECRET_MASK) {
        const cached = String(bridgeSecretCache.get(inputId) || '').trim();
        if (cached) {
          ipt.value = cached;
          ipt.dataset.masked = '0';
          ipt.dataset.dirty = '0';
        } else {
          const sec = inputId === 'opaCpaToken'
            ? 'cpa'
            : (inputId === 'opaProxyPoolApiKey' ? 'proxy_pool' : 'mail');
          logSection(sec, '掩码值无法直接显示，请重新输入真实值。', 'error');
          return;
        }
      }
      const show = ipt.type === 'password';
      ipt.type = show ? 'text' : 'password';
      btn.textContent = show ? '隐藏' : '显示';
    });
  }

  function build() {
    const root = document.createElement('div');
    root.className = 'opa-bridge-root';
    root.innerHTML = `
      <button id="opaToggle" class="opa-bridge-toggle" type="button">注册机控制</button>
      <div id="opaPanel" class="opa-bridge-panel">
        <div class="opa-bridge-head">
          <h3>OpenAI 注册机控制</h3>
          <button id="opaClose" class="opa-bridge-close" type="button">关闭</button>
        </div>
        <div class="opa-bridge-body">
          <section class="opa-bridge-card">
            <h4>实时控制</h4>
            <div class="opa-bridge-grid">
              <div class="opa-bridge-field">
                <label>注册代理</label>
                <input id="opaProxy" type="text" placeholder="http://127.0.0.1:7897" />
              </div>
              <div class="opa-bridge-field">
                <label>线程数</label>
                <input id="opaThreads" type="number" min="1" max="10" value="3" />
              </div>
            </div>
            <label class="opa-bridge-check"><input id="opaMultithread" type="checkbox" />多线程注册</label>
            <div class="opa-bridge-actions">
              <button id="opaCheckProxy" class="opa-bridge-btn" type="button">检测代理</button>
              <button id="opaStart" class="opa-bridge-btn primary" type="button">开始注册</button>
              <button id="opaStop" class="opa-bridge-btn warn" type="button">停止</button>
              <button id="opaRefresh" class="opa-bridge-btn" type="button">刷新状态</button>
            </div>
            <div id="opaRunningStatus" class="opa-bridge-status">状态: --</div>
          </section>

          <section class="opa-bridge-card">
            <h4>代理池配置</h4>
            <div class="opa-bridge-grid">
              <div class="opa-bridge-field">
                <label>Provider</label>
                <select id="opaProxyPoolProvider">
                  <option value="zenproxy_api">ZenProxy API</option>
                  <option value="dreamy_socks5_pool">Dreamy SOCKS5</option>
                  <option value="docker_warp_socks">Docker WARP SOCKS</option>
                </select>
              </div>
              <div class="opa-bridge-field">
                <label>Auth Mode</label>
                <select id="opaProxyPoolAuthMode">
                  <option value="query">query</option>
                  <option value="header">header</option>
                </select>
              </div>
              <div class="opa-bridge-field">
                <label>API URL / Fixed Proxy</label>
                <input id="opaProxyPoolApiUrl" type="text" placeholder="https://zenproxy.top/api/fetch" />
              </div>
              <div class="opa-bridge-field">
                <label>API Key</label>
                <div class="opa-secret-wrap">
                  <input id="opaProxyPoolApiKey" type="password" autocomplete="off" />
                  <button class="opa-secret-eye" id="opaProxyPoolApiKeyEye" data-eye-for="opaProxyPoolApiKey" type="button">显示</button>
                </div>
              </div>
              <div class="opa-bridge-field">
                <label>Count</label>
                <input id="opaProxyPoolCount" type="number" min="1" max="20" value="1" />
              </div>
              <div class="opa-bridge-field">
                <label>Country</label>
                <input id="opaProxyPoolCountry" type="text" maxlength="2" placeholder="US" />
              </div>
            </div>
            <label class="opa-bridge-check"><input id="opaProxyPoolEnabled" type="checkbox" />启用代理池</label>
            <div class="opa-bridge-actions">
              <button id="opaSaveProxyPool" class="opa-bridge-btn primary" type="button">保存代理池</button>
              <button id="opaTestProxyPool" class="opa-bridge-btn" type="button">测试代理池</button>
            </div>
          </section>

          <section class="opa-bridge-card">
            <h4>自动注册配置</h4>
            <div class="opa-bridge-grid">
              <div class="opa-bridge-field">
                <label>期望账号数量</label>
                <input id="opaDesired" type="number" min="0" value="0" />
              </div>
            </div>
            <label class="opa-bridge-check"><input id="opaAutoRegister" type="checkbox" />数量不足自动补号</label>
            <div class="opa-bridge-actions">
              <button id="opaSaveRegister" class="opa-bridge-btn primary" type="button">保存自动注册</button>
            </div>
          </section>

          <section class="opa-bridge-card">
            <h4>自动测活保活</h4>
            <div class="opa-bridge-grid">
              <div class="opa-bridge-field">
                <label>测活间隔(分钟)</label>
                <input id="opaMaintainInterval" type="number" min="5" value="30" />
              </div>
              <div class="opa-bridge-field">
                <label>探测超时(秒)</label>
                <input id="opaProbeTimeout" type="number" min="5" max="60" value="12" />
              </div>
            </div>
            <label class="opa-bridge-check"><input id="opaLocalMaintain" type="checkbox" />开启本地定时测活</label>
            <div class="opa-bridge-actions">
              <button id="opaSaveMaintain" class="opa-bridge-btn primary" type="button">保存保活配置</button>
              <button id="opaRunMaintain" class="opa-bridge-btn" type="button">立即执行一次</button>
            </div>
          </section>

          <section class="opa-bridge-card">
            <h4>CPA 配置（注册机侧）</h4>
            <div class="opa-bridge-grid">
              <div class="opa-bridge-field">
                <label>CPA Base URL</label>
                <input id="opaCpaBaseUrl" type="text" placeholder="https://cpa.example.com" />
              </div>
              <div class="opa-bridge-field">
                <label>CPA Token</label>
                <div class="opa-secret-wrap">
                  <input id="opaCpaToken" type="password" autocomplete="off" />
                  <button class="opa-secret-eye" id="opaCpaTokenEye" data-eye-for="opaCpaToken" type="button">显示</button>
                </div>
              </div>
              <div class="opa-bridge-field">
                <label>目标阈值</label>
                <input id="opaCpaMin" type="number" min="1" value="800" />
              </div>
              <div class="opa-bridge-field">
                <label>使用率阈值(%)</label>
                <input id="opaCpaUsed" type="number" min="1" max="100" value="95" />
              </div>
              <div class="opa-bridge-field">
                <label>维护间隔(分钟)</label>
                <input id="opaCpaInterval" type="number" min="5" value="30" />
              </div>
            </div>
            <label class="opa-bridge-check"><input id="opaCpaAuto" type="checkbox" />自动维护 CPA 池</label>
            <div class="opa-bridge-actions">
              <button id="opaSaveCpa" class="opa-bridge-btn primary" type="button">保存 CPA 配置</button>
              <button id="opaTestCpa" class="opa-bridge-btn" type="button">测试 CPA 连接</button>
            </div>
          </section>

          <section class="opa-bridge-card">
            <h4>邮箱 API 配置</h4>
            <div class="opa-bridge-grid">
              <div class="opa-bridge-field">
                <label>主邮箱提供商</label>
                <select id="opaMailPrimary"></select>
              </div>
              <div class="opa-bridge-field">
                <label>切换策略</label>
                <select id="opaMailStrategy">
                  <option value="round_robin">round_robin</option>
                  <option value="random">random</option>
                  <option value="failover">failover</option>
                </select>
              </div>
            </div>
            <div class="opa-provider-picks" id="opaMailProviderPicks"></div>

            <div class="opa-provider-grid">
              <div class="opa-provider-card">
                <h5>Mail.tm</h5>
                <div class="opa-bridge-field">
                  <label>API Base</label>
                  <input id="opaMailtmApiBase" type="text" placeholder="https://api.mail.tm" />
                </div>
              </div>

              <div class="opa-provider-card">
                <h5>MoeMail</h5>
                <div class="opa-bridge-field">
                  <label>API Base</label>
                  <input id="opaMoemailApiBase" type="text" placeholder="https://your-moemail-api.example.com" />
                </div>
                <div class="opa-bridge-field">
                  <label>API Key</label>
                  <div class="opa-secret-wrap">
                    <input id="opaMoemailApiKey" type="password" autocomplete="off" />
                    <button class="opa-secret-eye" id="opaMoemailApiKeyEye" data-eye-for="opaMoemailApiKey" type="button">显示</button>
                  </div>
                </div>
              </div>

              <div class="opa-provider-card">
                <h5>DuckMail</h5>
                <div class="opa-bridge-field">
                  <label>API Base</label>
                  <input id="opaDuckmailApiBase" type="text" placeholder="https://api.duckmail.sbs" />
                </div>
                <div class="opa-bridge-field">
                  <label>Bearer Token</label>
                  <div class="opa-secret-wrap">
                    <input id="opaDuckmailBearerToken" type="password" autocomplete="off" />
                    <button class="opa-secret-eye" id="opaDuckmailBearerTokenEye" data-eye-for="opaDuckmailBearerToken" type="button">显示</button>
                  </div>
                </div>
              </div>

              <div class="opa-provider-card">
                <h5>Cloudflare Temp Email</h5>
                <div class="opa-bridge-field">
                  <label>API Base</label>
                  <input id="opaCfApiBase" type="text" placeholder="https://temp-email-api.awsl.uk" />
                </div>
                <div class="opa-bridge-field">
                  <label>Domain</label>
                  <input id="opaCfDomain" type="text" placeholder="example.com" />
                </div>
                <div class="opa-bridge-field">
                  <label>站点密码 (x-custom-auth)</label>
                  <div class="opa-secret-wrap">
                    <input id="opaCfSitePassword" type="password" autocomplete="off" />
                    <button class="opa-secret-eye" id="opaCfSitePasswordEye" data-eye-for="opaCfSitePassword" type="button">显示</button>
                  </div>
                </div>
                <div class="opa-bridge-field">
                  <label>管理员密码 (x-admin-auth)</label>
                  <div class="opa-secret-wrap">
                    <input id="opaCfAdminPassword" type="password" autocomplete="off" />
                    <button class="opa-secret-eye" id="opaCfAdminPasswordEye" data-eye-for="opaCfAdminPassword" type="button">显示</button>
                  </div>
                </div>
                <label class="opa-bridge-check"><input id="opaCfEnablePrefix" type="checkbox" />启用随机前缀</label>
              </div>
            </div>

            <div class="opa-bridge-actions">
              <button id="opaSaveMail" class="opa-bridge-btn primary" type="button">保存邮箱配置</button>
              <button id="opaTestMail" class="opa-bridge-btn" type="button">测试全部邮箱 API</button>
            </div>
          </section>

          <div id="opaStatus" class="opa-bridge-status">就绪</div>
        </div>
      </div>
    `;
    document.body.appendChild(root);
    bridgeRoot = root;
    root.style.display = 'none';

    const picks = qs('opaMailProviderPicks');
    picks.innerHTML = MAIL_PROVIDERS.map((p) => (
      '<label class="opa-bridge-check opa-provider-pick">' +
        '<input type="checkbox" id="opaMailProvider_' + p.id + '" data-provider="' + p.id + '" />' +
        p.label +
      '</label>'
    )).join('');

    const realtimeActions = qs('opaCheckProxy')?.parentElement;
    if (realtimeActions && !qs('opaSaveRealtime')) {
      const btn = document.createElement('button');
      btn.id = 'opaSaveRealtime';
      btn.type = 'button';
      btn.className = 'opa-bridge-btn';
      btn.textContent = '保存实时配置';
      realtimeActions.insertBefore(btn, qs('opaCheckProxy'));
    }

    const mailActions = qs('opaSaveMail')?.parentElement;
    if (mailActions && !qs('opaTestMailSelected')) {
      const btn = document.createElement('button');
      btn.id = 'opaTestMailSelected';
      btn.type = 'button';
      btn.className = 'opa-bridge-btn';
      btn.textContent = '测试当前选中邮箱';
      mailActions.appendChild(btn);
    }

    const strategySelect = qs('opaMailStrategy');
    if (strategySelect) {
      const labels = { round_robin: '轮询', random: '随机', failover: '故障转移' };
      Array.from(strategySelect.options).forEach((opt) => {
        const key = String(opt.value || '').trim();
        if (labels[key]) opt.textContent = labels[key];
      });
    }

    const logTargets = [
      ['opaRunningStatus', 'opaLogRealtime'],
      ['opaSaveProxyPool', 'opaLogProxyPool'],
      ['opaSaveRegister', 'opaLogRegister'],
      ['opaSaveMaintain', 'opaLogMaintain'],
      ['opaSaveCpa', 'opaLogCpa'],
      ['opaSaveMail', 'opaLogMail'],
    ];
    logTargets.forEach(([anchorId, logId]) => {
      if (qs(logId)) return;
      const anchor = qs(anchorId);
      const card = anchor?.closest('.opa-bridge-card');
      if (!card) return;
      const logBox = document.createElement('div');
      logBox.id = logId;
      logBox.className = 'opa-bridge-log';
      card.appendChild(logBox);
    });

    const realtimeCard = qs('opaRunningStatus')?.closest('.opa-bridge-card');
    if (realtimeCard && !qs(RUN_LOG_STREAM_ID)) {
      const wrap = document.createElement('div');
      wrap.className = 'opa-runlog-wrap';
      wrap.innerHTML =
        '<div class="opa-runlog-head">' +
          '<span>运行日志</span>' +
          '<button id="' + RUN_LOG_CLEAR_ID + '" class="opa-bridge-btn" type="button">清空</button>' +
        '</div>' +
        '<div id="' + RUN_LOG_STREAM_ID + '" class="opa-bridge-runlog"></div>';
      realtimeCard.appendChild(wrap);
    }
  }

  function getSelectedMailProviders() {
    return qsa('#opaMailProviderPicks input[data-provider]:checked').map((el) => String(el.dataset.provider || '').trim()).filter(Boolean);
  }

  function syncMailPrimaryOptions(preferred) {
    const select = qs('opaMailPrimary');
    if (!select) return;
    const selectedProviders = getSelectedMailProviders();
    const keep = selectedProviders.includes(preferred) ? preferred : (selectedProviders[0] || 'mailtm');
    select.innerHTML = selectedProviders.map((pid) => {
      const p = MAIL_PROVIDERS.find((x) => x.id === pid);
      const label = p ? p.label : pid;
      return '<option value="' + pid + '">' + label + '</option>';
    }).join('');
    if (!selectedProviders.length) {
      select.innerHTML = '<option value="mailtm">Mail.tm</option>';
      select.value = 'mailtm';
      return;
    }
    select.value = keep;
  }

  function fillTextField(id, value) {
    const el = qs(id);
    if (!el) return;
    el.value = value == null ? '' : String(value);
  }

  function fillSecretField(id, cfg, keys) {
    const el = qs(id);
    if (!el) return;
    const keyList = Array.isArray(keys) ? keys : [keys];
    let preview = '';
    for (const key of keyList) {
      const p = String(cfg[key + '_preview'] || '').trim();
      if (p) {
        preview = p;
        break;
      }
    }
    if (preview) {
      setSecretMasked(el, true);
      return;
    }

    let plain = '';
    for (const key of keyList) {
      const v = String(cfg[key] || '').trim();
      if (v) {
        plain = v;
        break;
      }
    }

    if (plain) {
      el.value = plain;
      el.dataset.masked = '0';
      el.dataset.dirty = '0';
      bridgeSecretCache.set(id, plain);
    } else {
      setSecretMasked(el, false);
    }
  }

  function readText(id) {
    const el = qs(id);
    return el ? String(el.value || '').trim() : '';
  }

  function writeSecret(target, key, id) {
    const el = qs(id);
    if (!el) return;
    const state = getSecretState(el);
    if (state.masked) return;
    target[key] = state.trimmed;
  }

  function buildMailConfigs(selectedProviders) {
    const configs = {};

    for (const provider of selectedProviders) {
      const cfg = {};
      if (provider === 'mailtm') {
        cfg.api_base = readText('opaMailtmApiBase');
      } else if (provider === 'moemail') {
        cfg.api_base = readText('opaMoemailApiBase');
        writeSecret(cfg, 'api_key', 'opaMoemailApiKey');
      } else if (provider === 'duckmail') {
        cfg.api_base = readText('opaDuckmailApiBase');
        writeSecret(cfg, 'bearer_token', 'opaDuckmailBearerToken');
      } else if (provider === 'cloudflare_temp_email') {
        cfg.api_base = readText('opaCfApiBase');
        cfg.domain = readText('opaCfDomain').toLowerCase();
        cfg.enable_prefix = qs('opaCfEnablePrefix')?.checked ? 'true' : 'false';
        writeSecret(cfg, 'site_password', 'opaCfSitePassword');
        writeSecret(cfg, 'admin_password', 'opaCfAdminPassword');
      }
      configs[provider] = cfg;
    }

    return configs;
  }

  function proxyPoolDefaultUrl(provider) {
    const hit = PROXY_POOL_PROVIDERS.find((item) => item.id === provider);
    return hit ? hit.defaultUrl : 'https://zenproxy.top/api/fetch';
  }

  function syncProxyPoolDefaults(forceUrl) {
    const provider = readText('opaProxyPoolProvider') || 'zenproxy_api';
    const apiUrlEl = qs('opaProxyPoolApiUrl');
    const authModeEl = qs('opaProxyPoolAuthMode');
    if (!apiUrlEl || !authModeEl) return;

    const defaultUrl = proxyPoolDefaultUrl(provider);
    const currentUrl = String(apiUrlEl.value || '').trim();
    if (forceUrl || !currentUrl) {
      apiUrlEl.value = defaultUrl;
    }

    const fixedProxyProvider = provider === 'dreamy_socks5_pool' || provider === 'docker_warp_socks';
    authModeEl.disabled = fixedProxyProvider;
    if (fixedProxyProvider) {
      authModeEl.value = 'query';
    }
  }

  async function loadProxyPoolConfig() {
    try {
      const d = await api('/api/proxy-pool/config');
      qs('opaProxyPoolEnabled').checked = !!d.proxy_pool_enabled;
      fillTextField('opaProxyPoolApiUrl', d.proxy_pool_api_url || '');
      fillTextField('opaProxyPoolCountry', d.proxy_pool_country || 'US');
      fillTextField('opaProxyPoolCount', d.proxy_pool_count || 1);
      qs('opaProxyPoolProvider').value = d.proxy_pool_provider || 'zenproxy_api';
      qs('opaProxyPoolAuthMode').value = d.proxy_pool_auth_mode || 'query';
      fillSecretField('opaProxyPoolApiKey', d, ['proxy_pool_api_key', 'api_key']);
      syncProxyPoolDefaults(false);
    } catch (e) {
      logSection('proxy_pool', '加载代理池配置失败: ' + e.message, 'error');
    }
  }

  async function loadMailConfig() {
    try {
      const data = await api('/api/mail/config');
      const providerConfigs = Object.assign({}, data.mail_provider_configs || {});
      const primary = String(data.mail_provider || 'mailtm').trim().toLowerCase() || 'mailtm';
      const legacyMailConfig = (data.mail_config && typeof data.mail_config === 'object') ? data.mail_config : {};
      if (!providerConfigs[primary]) {
        providerConfigs[primary] = {};
      }
      providerConfigs[primary] = Object.assign({}, legacyMailConfig, providerConfigs[primary]);

      const selected = Array.isArray(data.mail_providers) && data.mail_providers.length
        ? data.mail_providers.map((v) => String(v || '').trim().toLowerCase()).filter(Boolean)
        : [primary];

      for (const p of MAIL_PROVIDERS) {
        const ck = qs('opaMailProvider_' + p.id);
        if (ck) ck.checked = selected.includes(p.id);
      }
      syncMailPrimaryOptions(primary);

      const strategy = String(data.mail_strategy || 'round_robin').trim().toLowerCase();
      qs('opaMailStrategy').value = ['round_robin', 'random', 'failover'].includes(strategy) ? strategy : 'round_robin';

      const mt = providerConfigs.mailtm || {};
      fillTextField('opaMailtmApiBase', mt.api_base || '');

      const moe = providerConfigs.moemail || {};
      fillTextField('opaMoemailApiBase', moe.api_base || '');
      fillSecretField('opaMoemailApiKey', moe, ['api_key']);

      const duck = providerConfigs.duckmail || {};
      fillTextField('opaDuckmailApiBase', duck.api_base || '');
      fillSecretField('opaDuckmailBearerToken', duck, ['bearer_token']);

      const cf = providerConfigs.cloudflare_temp_email || {};
      fillTextField('opaCfApiBase', cf.api_base || '');
      fillTextField('opaCfDomain', cf.domain || '');
      fillSecretField('opaCfSitePassword', cf, ['site_password', 'x_custom_auth']);
      fillSecretField('opaCfAdminPassword', cf, ['admin_password', 'x_admin_auth']);
      qs('opaCfEnablePrefix').checked = String(cf.enable_prefix ?? 'true').toLowerCase() !== 'false';
    } catch (e) {
      status('加载邮箱配置失败: ' + e.message);
    }
  }

  async function loadProxyConfig() {
    try {
      const d = await api('/api/proxy');
      qs('opaProxy').value = d.proxy || '';
      qs('opaAutoRegister').checked = !!d.auto_register;
      qs('opaDesired').value = d.desired_token_count || 0;
      qs('opaMultithread').checked = !!d.multithread;
      qs('opaThreads').value = d.thread_count || 3;
      qs('opaLocalMaintain').checked = !!d.local_auto_maintain;
      qs('opaMaintainInterval').value = d.local_maintain_interval_minutes || 30;
      qs('opaProbeTimeout').value = d.local_probe_timeout_seconds || 12;
    } catch (e) {
      status('加载代理配置失败: ' + e.message);
    }
  }

  async function loadPoolConfig() {
    try {
      const d = await api('/api/pool/config');
      qs('opaCpaBaseUrl').value = d.cpa_base_url || '';
      setSecretMasked(qs('opaCpaToken'), !!d.cpa_token_preview);
      qs('opaCpaMin').value = d.min_candidates || 800;
      qs('opaCpaUsed').value = d.used_percent_threshold || 95;
      qs('opaCpaAuto').checked = !!d.auto_maintain;
      qs('opaCpaInterval').value = d.maintain_interval_minutes || 30;
    } catch (e) {
      status('加载 CPA 配置失败: ' + e.message);
    }
  }

  async function refreshStatus() {
    try {
      const d = await api('/api/status');
      const pf = d.platform_fail || {};
      const text = '状态: ' + (d.status || 'idle')
        + ' | 成功: ' + num(d.success, 0)
        + ' | 管道失败: ' + num(d.fail, 0)
        + ' | CPA失败: ' + num(pf.cpa, 0)
        + ' | Sub2Api失败: ' + num(pf.sub2api, 0);
      const el = qs('opaRunningStatus');
      if (el) el.textContent = text;
    } catch (e) {
      const el = qs('opaRunningStatus');
      if (el) el.textContent = '状态读取失败: ' + e.message;
    }
  }


  function parseHashRoute() {
    const hash = String(window.location.hash || '');
    const raw = hash.startsWith('#') ? hash.slice(1) : hash;
    const qIndex = raw.indexOf('?');
    const path = (qIndex >= 0 ? raw.slice(0, qIndex) : raw) || '/';
    const search = qIndex >= 0 ? raw.slice(qIndex + 1) : '';
    let params = new URLSearchParams();
    try {
      params = new URLSearchParams(search);
    } catch (_) {}
    return { path, params };
  }

  function isBridgeRoute() {
    const hash = String(window.location.hash || '');
    if (
      hash === BRIDGE_ROUTE_HASH ||
      hash.startsWith(BRIDGE_ROUTE_HASH + '/') ||
      hash.startsWith(BRIDGE_ROUTE_HASH + '?')
    ) {
      return true;
    }
    const route = parseHashRoute();
    return route.path === BRIDGE_ROUTE_PATH && route.params.get(BRIDGE_ROUTE_QUERY_KEY) === BRIDGE_ROUTE_QUERY_VALUE;
  }

  function normalizeBridgeHash() {
    const hash = String(window.location.hash || '');
    if (!hash || hash === BRIDGE_ROUTE_HASH) return false;
    if (hash === BRIDGE_ROUTE_COMPAT_HASH) {
      window.location.hash = BRIDGE_ROUTE_HASH;
      return true;
    }
    const route = parseHashRoute();
    if (route.path === BRIDGE_ROUTE_PATH && route.params.get(BRIDGE_ROUTE_QUERY_KEY) === BRIDGE_ROUTE_QUERY_VALUE) {
      window.location.hash = BRIDGE_ROUTE_HASH;
      return true;
    }
    return false;
  }

  function stripActiveLikeClasses(className) {
    return String(className || '')
      .split(/\s+/)
      .map((x) => x.trim())
      .filter(Boolean)
      .filter((name) => !/(^|[-_])(active|selected|current)([-_]|$)/i.test(name))
      .join(' ');
  }

  function activateBridgeRoute() {
    const target = BRIDGE_ROUTE_HASH;
    if (String(window.location.hash || '') !== target) {
      window.location.hash = target;
    }
    scheduleBridgeSync();
    [120, 320, 700].forEach((delay) => {
      window.setTimeout(() => {
        if (!isBridgeRoute()) {
          window.location.hash = target;
        }
        scheduleBridgeSync();
      }, delay);
    });
  }

  function findSidebarNavContainer() {
    const sidebar = document.querySelector('aside[class*="sidebar"]') || document.querySelector('aside') || document.querySelector('[class*="sidebar"]');
    if (!sidebar) return null;
    return sidebar.querySelector('[class*="nav-section"]') || sidebar.querySelector('nav') || sidebar;
  }

  function ensureBridgeNavItem() {
    const navContainer = findSidebarNavContainer();
    if (!navContainer) return;

    let item = qs(BRIDGE_NAV_ID);
    if (!item || !document.contains(item)) {
      const sample = navContainer.querySelector('a[href^="#/"], a, button');
      const wrapSample = sample && sample.parentElement && sample.parentElement !== navContainer ? sample.parentElement : null;

      item = document.createElement('a');
      item.id = BRIDGE_NAV_ID;
      item.href = BRIDGE_ROUTE_HASH;
      item.className = stripActiveLikeClasses(sample && sample.className ? sample.className : '');
      item.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        activateBridgeRoute();
      });
      item.innerHTML =
        '<span class="opa-nav-entry">' +
          '<svg class="opa-nav-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2M8 8h8v8H8z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>' +
          '</svg>' +
          '<span>注册机</span>' +
        '</span>';

      if (wrapSample) {
        const wrap = document.createElement(wrapSample.tagName || 'div');
        wrap.id = BRIDGE_NAV_WRAP_ID;
        wrap.className = stripActiveLikeClasses(wrapSample.className || '');
        wrap.appendChild(item);
        navContainer.appendChild(wrap);
      } else {
        navContainer.appendChild(item);
      }
    }

    const active = isBridgeRoute();
    item.classList.toggle('opa-nav-active', active);
    if (active) {
      item.setAttribute('aria-current', 'page');
    } else {
      item.removeAttribute('aria-current');
    }
  }

  function syncNativeNavSelection(active) {
    const navContainer = findSidebarNavContainer();
    if (!navContainer) return;

    const candidates = Array.from(navContainer.querySelectorAll('a,button,[role="button"]'));
    candidates.forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      if (node.id === BRIDGE_NAV_ID || node.closest('#' + BRIDGE_NAV_WRAP_ID)) return;

      if (active) {
        if (node.getAttribute('aria-current') === 'page') {
          node.setAttribute(BRIDGE_PREV_CURRENT_ATTR, 'page');
          node.removeAttribute('aria-current');
        }
        return;
      }

      if (node.getAttribute(BRIDGE_PREV_CURRENT_ATTR) === 'page') {
        node.setAttribute('aria-current', 'page');
      }
      node.removeAttribute(BRIDGE_PREV_CURRENT_ATTR);
    });
  }

  function findMainContainer() {
    const root = qs('root');
    if (!root) return null;
    const selectors = [
      'main[class*="main-content"]',
      'main[class*="mainContent"]',
      'main',
      '[class*="main-content"]',
      '[class*="mainContent"]',
      '[class*="content"]'
    ];
    for (const selector of selectors) {
      const list = Array.from(root.querySelectorAll(selector));
      for (const el of list) {
        if (!(el instanceof HTMLElement)) continue;
        if (el.closest('aside')) continue;
        return el;
      }
    }
    return root;
  }

  function ensureBridgeHost() {
    const main = findMainContainer();
    if (!main) return null;
    main.classList.add('opa-main-container');
    let host = qs(BRIDGE_HOST_ID);
    if (!host || host.parentElement !== main) {
      if (host && host.parentElement) host.parentElement.removeChild(host);
      host = document.createElement('div');
      host.id = BRIDGE_HOST_ID;
      host.className = 'opa-route-host';
      main.appendChild(host);
    }
    return host;
  }

  function applyBridgeRouteVisibility() {
    if (!bridgeRoot) return;
    if (normalizeBridgeHash()) return;
    ensureBridgeNavItem();
    const active = isBridgeRoute();
    syncNativeNavSelection(active);
    const host = ensureBridgeHost();
    if (host && bridgeRoot.parentElement !== host) {
      host.appendChild(bridgeRoot);
    }
    if (host && host.parentElement) {
      host.parentElement.classList.toggle('opa-route-active', active);
    }

    bridgeRoot.style.display = active ? '' : 'none';
    const panel = qs('opaPanel');
    if (panel) panel.classList.add('open');

    if (active && !bridgePrevActive) {
      loadProxyConfig();
      loadProxyPoolConfig();
      loadPoolConfig();
      loadMailConfig();
      refreshStatus();
      openRunLogStream();
    }
    bridgePrevActive = active;
  }

  function scheduleBridgeSync() {
    if (bridgeSyncTimer) return;
    bridgeSyncTimer = window.setTimeout(() => {
      bridgeSyncTimer = null;
      applyBridgeRouteVisibility();
    }, 80);
  }

  function startBridgeObserver() {
    if (bridgeObserver) return;
    bridgeObserver = new MutationObserver(() => {
      scheduleBridgeSync();
    });
    bridgeObserver.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('hashchange', scheduleBridgeSync);
    window.addEventListener('popstate', scheduleBridgeSync);
  }

  function bind() {
    const panel = qs('opaPanel');
    const toggle = qs('opaToggle');
    const close = qs('opaClose');
    if (toggle && panel) toggle.addEventListener('click', () => panel.classList.toggle('open'));
    if (close && panel) close.addEventListener('click', () => panel.classList.remove('open'));

    bindSecretEye('opaCpaToken', 'opaCpaTokenEye');
    bindSecretEye('opaProxyPoolApiKey', 'opaProxyPoolApiKeyEye');
    bindSecretEye('opaMoemailApiKey', 'opaMoemailApiKeyEye');
    bindSecretEye('opaDuckmailBearerToken', 'opaDuckmailBearerTokenEye');
    bindSecretEye('opaCfSitePassword', 'opaCfSitePasswordEye');
    bindSecretEye('opaCfAdminPassword', 'opaCfAdminPasswordEye');

    markSecretDirty(qs('opaCpaToken'));
    markSecretDirty(qs('opaProxyPoolApiKey'));
    markSecretDirty(qs('opaMoemailApiKey'));
    markSecretDirty(qs('opaDuckmailBearerToken'));
    markSecretDirty(qs('opaCfSitePassword'));
    markSecretDirty(qs('opaCfAdminPassword'));

    qsa('#opaMailProviderPicks input[data-provider]').forEach((el) => {
      el.addEventListener('change', () => syncMailPrimaryOptions(qs('opaMailPrimary').value));
    });
    const proxyPoolProvider = qs('opaProxyPoolProvider');
    if (proxyPoolProvider) {
      proxyPoolProvider.addEventListener('change', () => syncProxyPoolDefaults(false));
    }

    const onClick = (id, fn) => {
      const el = qs(id);
      if (el) el.addEventListener('click', fn);
    };

    const buildProxyPoolPayload = () => {
      const keyEl = qs('opaProxyPoolApiKey');
      const keyState = keyEl ? getSecretState(keyEl) : { masked: false, clear: false, trimmed: '' };
      return {
        proxy_pool_enabled: !!qs('opaProxyPoolEnabled')?.checked,
        proxy_pool_provider: readText('opaProxyPoolProvider') || 'zenproxy_api',
        proxy_pool_api_url: readText('opaProxyPoolApiUrl'),
        proxy_pool_auth_mode: readText('opaProxyPoolAuthMode') || 'query',
        proxy_pool_api_key: keyState.masked ? '' : keyState.trimmed,
        clear_proxy_pool_api_key: !!keyState.clear,
        proxy_pool_count: Math.max(1, Math.min(20, num(qs('opaProxyPoolCount')?.value, 1))),
        proxy_pool_country: (readText('opaProxyPoolCountry') || 'US').toUpperCase()
      };
    };

    onClick(RUN_LOG_CLEAR_ID, () => {
      const box = qs(RUN_LOG_STREAM_ID);
      if (box) box.innerHTML = '';
      appendRunLogLine({ level: 'info', step: 'logs', message: '运行日志已清空' });
    });

    onClick('opaSaveRealtime', async () => {
      logSection('realtime', '正在保存实时配置...');
      try {
        await api('/api/proxy/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            proxy: (qs('opaProxy').value || '').trim(),
            multithread: !!qs('opaMultithread').checked,
            thread_count: Math.max(1, Math.min(10, num(qs('opaThreads').value, 3)))
          })
        });
        logSection('realtime', '实时配置已保存');
      } catch (e) {
        logSection('realtime', '保存失败: ' + e.message, 'error');
      }
    });

    onClick('opaCheckProxy', async () => {
      const proxy = (qs('opaProxy').value || '').trim();
      if (!proxy) {
        logSection('realtime', '请先输入代理地址', 'error');
        return;
      }
      logSection('realtime', '正在检测代理...');
      try {
        const d = await api('/api/check-proxy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ proxy: proxy })
        });
        if (d.ok) {
          logSection('realtime', '代理可用，地区: ' + (d.loc || '--'));
        } else {
          logSection('realtime', '代理检测失败: ' + (d.error || '--'), 'error');
        }
      } catch (e) {
        logSection('realtime', '代理检测异常: ' + e.message, 'error');
      }
    });

    onClick('opaSaveProxyPool', async () => {
      logSection('proxy_pool', '正在保存代理池配置...');
      try {
        const payload = buildProxyPoolPayload();
        await api('/api/proxy-pool/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        await loadProxyPoolConfig();
        logSection('proxy_pool', '代理池配置已保存');
      } catch (e) {
        logSection('proxy_pool', '保存失败: ' + e.message, 'error');
      }
    });

    onClick('opaTestProxyPool', async () => {
      logSection('proxy_pool', '正在测试代理池...');
      try {
        const payload = buildProxyPoolPayload();
        const d = await api('/api/proxy-pool/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            enabled: payload.proxy_pool_enabled,
            provider: payload.proxy_pool_provider,
            api_url: payload.proxy_pool_api_url,
            auth_mode: payload.proxy_pool_auth_mode,
            api_key: payload.proxy_pool_api_key,
            count: payload.proxy_pool_count,
            country: payload.proxy_pool_country
          })
        });
        if (d.ok) {
          const detail = [];
          if (d.provider) detail.push('provider=' + d.provider);
          if (d.proxy) detail.push('proxy=' + d.proxy);
          if (d.loc) detail.push('loc=' + d.loc);
          logSection('proxy_pool', '代理池测试通过' + (detail.length ? ' | ' + detail.join(' | ') : ''));
        } else {
          logSection('proxy_pool', '代理池测试失败: ' + (d.error || d.message || '--'), 'error');
        }
      } catch (e) {
        logSection('proxy_pool', '代理池测试异常: ' + e.message, 'error');
      }
    });

    onClick('opaStart', async () => {
      logSection('realtime', '正在启动注册任务...');
      try {
        await api('/api/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            proxy: (qs('opaProxy').value || '').trim(),
            multithread: !!qs('opaMultithread').checked,
            thread_count: Math.max(1, Math.min(10, num(qs('opaThreads').value, 3)))
          })
        });
        logSection('realtime', '注册任务已启动');
        refreshStatus();
      } catch (e) {
        logSection('realtime', '启动失败: ' + e.message, 'error');
      }
    });

    onClick('opaStop', async () => {
      logSection('realtime', '正在停止注册任务...');
      try {
        await api('/api/stop', { method: 'POST' });
        logSection('realtime', '已发送停止请求');
        refreshStatus();
      } catch (e) {
        logSection('realtime', '停止失败: ' + e.message, 'error');
      }
    });

    onClick('opaSaveRegister', async () => {
      logSection('register', '正在保存自动注册配置...');
      try {
        await api('/api/proxy/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            proxy: (qs('opaProxy').value || '').trim(),
            auto_register: !!qs('opaAutoRegister').checked,
            desired_token_count: Math.max(0, num(qs('opaDesired').value, 0)),
            multithread: !!qs('opaMultithread').checked,
            thread_count: Math.max(1, Math.min(10, num(qs('opaThreads').value, 3)))
          })
        });
        logSection('register', '自动注册配置已保存');
      } catch (e) {
        logSection('register', '保存失败: ' + e.message, 'error');
      }
    });

    onClick('opaSaveMaintain', async () => {
      logSection('maintain', '正在保存保活配置...');
      try {
        await api('/api/proxy/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            local_auto_maintain: !!qs('opaLocalMaintain').checked,
            local_maintain_interval_minutes: Math.max(5, num(qs('opaMaintainInterval').value, 30)),
            local_probe_timeout_seconds: Math.max(5, Math.min(60, num(qs('opaProbeTimeout').value, 12)))
          })
        });
        logSection('maintain', '保活配置已保存');
      } catch (e) {
        logSection('maintain', '保存失败: ' + e.message, 'error');
      }
    });

    onClick('opaRunMaintain', async () => {
      logSection('maintain', '正在执行一次保活...');
      try {
        const d = await api('/api/local/maintain', { method: 'POST' });
        logSection(
          'maintain',
          '完成: 已检查 ' + num(d.checked, 0) + '，已删除 ' + num(d.deleted_ok, 0) + '，剩余 ' + num(d.remaining, 0)
        );
      } catch (e) {
        logSection('maintain', '执行失败: ' + e.message, 'error');
      }
    });

    onClick('opaSaveCpa', async () => {
      logSection('cpa', '正在保存 CPA 配置...');
      try {
        const tokenState = getSecretState(qs('opaCpaToken'));
        await api('/api/pool/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            cpa_base_url: (qs('opaCpaBaseUrl').value || '').trim(),
            cpa_token: tokenState.masked ? '' : tokenState.trimmed,
            clear_cpa_token: tokenState.clear,
            min_candidates: Math.max(1, num(qs('opaCpaMin').value, 800)),
            used_percent_threshold: Math.max(1, Math.min(100, num(qs('opaCpaUsed').value, 95))),
            auto_maintain: !!qs('opaCpaAuto').checked,
            maintain_interval_minutes: Math.max(5, num(qs('opaCpaInterval').value, 30))
          })
        });
        logSection('cpa', 'CPA 配置已保存');
      } catch (e) {
        logSection('cpa', '保存失败: ' + e.message, 'error');
      }
    });

    onClick('opaTestCpa', async () => {
      logSection('cpa', '正在测试 CPA...');
      try {
        const d = await api('/api/pool/check', { method: 'POST' });
        logSection('cpa', d.message || 'CPA 测试通过');
      } catch (e) {
        logSection('cpa', 'CPA 测试失败: ' + e.message, 'error');
      }
    });

    onClick('opaSaveMail', async () => {
      const selectedProviders = getSelectedMailProviders();
      if (!selectedProviders.length) {
        logSection('mail', '请至少选择一个邮箱提供商', 'error');
        return;
      }
      let primary = String(qs('opaMailPrimary').value || '').trim().toLowerCase();
      if (!selectedProviders.includes(primary)) {
        primary = selectedProviders[0];
      }

      logSection('mail', '正在保存邮箱配置...');
      try {
        const mailProviderConfigs = buildMailConfigs(selectedProviders);
        const primaryLegacyConfig = Object.assign({}, mailProviderConfigs[primary] || {});

        await api('/api/mail/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mail_provider: primary,
            mail_providers: selectedProviders,
            mail_strategy: String(qs('opaMailStrategy').value || 'round_robin').trim(),
            mail_provider_configs: mailProviderConfigs,
            mail_config: primaryLegacyConfig
          })
        });
        logSection('mail', '邮箱配置已保存');
      } catch (e) {
        logSection('mail', '邮箱保存失败: ' + e.message, 'error');
      }
    });

    onClick('opaTestMail', async () => {
      logSection('mail', '正在测试所有启用的邮箱提供商...');
      try {
        const d = await api('/api/mail/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const results = Array.isArray(d.results) ? d.results : [];
        if (!results.length) {
          logSection('mail', (d.ok ? '成功' : '失败') + ': ' + (d.message || '无结果'), d.ok ? 'info' : 'error');
          return;
        }
        const details = results.map((r) => {
          const name = String(r.provider || '未知');
          const ok = r.ok ? '成功' : '失败';
          const msg = String(r.message || '').trim();
          return name + ':' + ok + (msg ? (' (' + msg + ')') : '');
        }).join(' | ');
        logSection('mail', details + (d.message ? (' | ' + d.message) : ''), d.ok ? 'info' : 'error');
      } catch (e) {
        logSection('mail', '邮箱测试失败: ' + e.message, 'error');
      }
    });

    onClick('opaTestMailSelected', async () => {
      const selectedProviders = getSelectedMailProviders();
      if (!selectedProviders.length) {
        logSection('mail', '请选择要测试的邮箱提供商', 'error');
        return;
      }
      logSection('mail', '正在测试所选提供商: ' + selectedProviders.join(', '));
      try {
        const d = await api('/api/mail/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ providers: selectedProviders })
        });
        const results = Array.isArray(d.results) ? d.results : [];
        const details = results.map((r) => {
          const name = String(r.provider || '未知');
          const ok = r.ok ? '成功' : '失败';
          const msg = String(r.message || '').trim();
          return name + ':' + ok + (msg ? (' (' + msg + ')') : '');
        }).join(' | ');
        logSection('mail', details || (d.message || '无结果'), d.ok ? 'info' : 'error');
      } catch (e) {
        logSection('mail', '所选邮箱测试失败: ' + e.message, 'error');
      }
    });

    onClick('opaRefresh', async () => {
      refreshStatus();
      await loadProxyConfig();
      await loadProxyPoolConfig();
      await loadPoolConfig();
      await loadMailConfig();
      logSection('realtime', '数据已刷新');
    });
  }

  function init() {
    build();
    bind();
    openRunLogStream();
    setInterval(() => {
      if (isBridgeRoute()) refreshStatus();
    }, 3000);
    applyBridgeRouteVisibility();
    startBridgeObserver();
    scheduleBridgeSync();
    window.setTimeout(scheduleBridgeSync, 300);
    window.setTimeout(scheduleBridgeSync, 1200);
    window.addEventListener('beforeunload', closeRunLogStream);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();


