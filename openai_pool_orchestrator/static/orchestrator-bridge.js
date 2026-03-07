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
  const BRIDGE_ROUTE_HASH = '#/orchestrator';
  const BRIDGE_NAV_ID = 'opa-orchestrator-nav-item';
  const BRIDGE_NAV_WRAP_ID = 'opa-orchestrator-nav-wrap';
  const BRIDGE_HOST_ID = 'opa-orchestrator-route-host';

  let bridgeRoot = null;
  let bridgeSyncTimer = null;
  let bridgeObserver = null;
  let bridgePrevActive = false;

  const qs = (id) => document.getElementById(id);
  const qsa = (selector) => Array.from(document.querySelectorAll(selector));

  function api(path, init) {
    return fetch(path, init).then(async (res) => {
      let data = {};
      try { data = await res.json(); } catch (_) {}
      if (!res.ok) {
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
      const text = '状态: ' + (d.status || 'idle') + ' | 成功: ' + num(d.success, 0) + ' | 失败: ' + num(d.fail, 0);
      const el = qs('opaRunningStatus');
      if (el) el.textContent = text;
    } catch (e) {
      const el = qs('opaRunningStatus');
      if (el) el.textContent = '状态读取失败: ' + e.message;
    }
  }

  function isBridgeRoute() {
    const hash = String(window.location.hash || '');
    return hash === BRIDGE_ROUTE_HASH || hash.startsWith(BRIDGE_ROUTE_HASH + '/') || hash.startsWith(BRIDGE_ROUTE_HASH + '?');
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
      item.className = sample && sample.className ? sample.className : '';
      item.innerHTML = '<span>注册机</span>';

      if (wrapSample) {
        const wrap = document.createElement(wrapSample.tagName || 'div');
        wrap.id = BRIDGE_NAV_WRAP_ID;
        wrap.className = wrapSample.className || '';
        wrap.appendChild(item);
        navContainer.appendChild(wrap);
      } else {
        navContainer.appendChild(item);
      }
    }

    item.classList.toggle('opa-nav-active', isBridgeRoute());
    item.setAttribute('aria-current', isBridgeRoute() ? 'page' : 'false');
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
    ensureBridgeNavItem();
    const active = isBridgeRoute();
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
      loadPoolConfig();
      loadMailConfig();
      refreshStatus();
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
    if (toggle) toggle.addEventListener('click', () => panel.classList.toggle('open'));
    if (close) close.addEventListener('click', () => panel.classList.remove('open'));

    bindSecretEye('opaCpaToken', 'opaCpaTokenEye');
    bindSecretEye('opaMoemailApiKey', 'opaMoemailApiKeyEye');
    bindSecretEye('opaDuckmailBearerToken', 'opaDuckmailBearerTokenEye');
    bindSecretEye('opaCfSitePassword', 'opaCfSitePasswordEye');
    bindSecretEye('opaCfAdminPassword', 'opaCfAdminPasswordEye');

    markSecretDirty(qs('opaCpaToken'));
    markSecretDirty(qs('opaMoemailApiKey'));
    markSecretDirty(qs('opaDuckmailBearerToken'));
    markSecretDirty(qs('opaCfSitePassword'));
    markSecretDirty(qs('opaCfAdminPassword'));

    qsa('#opaMailProviderPicks input[data-provider]').forEach((el) => {
      el.addEventListener('change', () => syncMailPrimaryOptions(qs('opaMailPrimary').value));
    });

    qs('opaCheckProxy').addEventListener('click', async () => {
      const proxy = (qs('opaProxy').value || '').trim();
      if (!proxy) return status('请先输入代理');
      status('代理检测中...');
      try {
        const d = await api('/api/check-proxy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ proxy: proxy })
        });
        status(d.ok ? ('代理可用，地区: ' + (d.loc || '--')) : ('代理不可用: ' + (d.error || '--')));
      } catch (e) {
        status('代理检测失败: ' + e.message);
      }
    });

    qs('opaStart').addEventListener('click', async () => {
      status('启动中...');
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
        status('注册任务已启动');
        refreshStatus();
      } catch (e) {
        status('启动失败: ' + e.message);
      }
    });

    qs('opaStop').addEventListener('click', async () => {
      status('停止中...');
      try {
        await api('/api/stop', { method: 'POST' });
        status('停止请求已发送');
        refreshStatus();
      } catch (e) {
        status('停止失败: ' + e.message);
      }
    });

    qs('opaSaveRegister').addEventListener('click', async () => {
      status('保存自动注册配置...');
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
        status('自动注册配置已保存');
      } catch (e) {
        status('保存失败: ' + e.message);
      }
    });

    qs('opaSaveMaintain').addEventListener('click', async () => {
      status('保存保活配置...');
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
        status('保活配置已保存');
      } catch (e) {
        status('保存失败: ' + e.message);
      }
    });

    qs('opaRunMaintain').addEventListener('click', async () => {
      status('执行本地测活中...');
      try {
        const d = await api('/api/local/maintain', { method: 'POST' });
        status('测活完成: 检查 ' + num(d.checked, 0) + '，删除 ' + num(d.deleted_ok, 0) + '，剩余 ' + num(d.remaining, 0));
      } catch (e) {
        status('执行失败: ' + e.message);
      }
    });

    qs('opaSaveCpa').addEventListener('click', async () => {
      status('保存 CPA 配置...');
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
        status('CPA 配置已保存');
        loadPoolConfig();
      } catch (e) {
        status('保存失败: ' + e.message);
      }
    });

    qs('opaTestCpa').addEventListener('click', async () => {
      status('测试 CPA 连接...');
      try {
        const d = await api('/api/pool/check', { method: 'POST' });
        status(d.message || 'CPA 连接成功');
      } catch (e) {
        status('CPA 测试失败: ' + e.message);
      }
    });

    qs('opaSaveMail').addEventListener('click', async () => {
      const selectedProviders = getSelectedMailProviders();
      if (!selectedProviders.length) {
        status('至少选择一个邮箱提供商');
        return;
      }
      let primary = String(qs('opaMailPrimary').value || '').trim().toLowerCase();
      if (!selectedProviders.includes(primary)) {
        primary = selectedProviders[0];
      }

      status('保存邮箱配置...');
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
        status('邮箱配置已保存');
        await loadMailConfig();
      } catch (e) {
        status('邮箱配置保存失败: ' + e.message);
      }
    });

    qs('opaTestMail').addEventListener('click', async () => {
      status('测试邮箱 API 中...');
      try {
        const d = await api('/api/mail/test', { method: 'POST' });
        const results = Array.isArray(d.results) ? d.results : [];
        if (!results.length) {
          status((d.ok ? '成功' : '失败') + ': ' + (d.message || '无返回结果'));
          return;
        }
        const details = results.map((r) => {
          const name = String(r.provider || 'unknown');
          const ok = r.ok ? 'OK' : 'FAIL';
          return name + ':' + ok;
        }).join(' | ');
        status(details + (d.message ? (' | ' + d.message) : ''));
      } catch (e) {
        status('邮箱 API 测试失败: ' + e.message);
      }
    });

    qs('opaRefresh').addEventListener('click', async () => {
      refreshStatus();
      await loadProxyConfig();
      await loadPoolConfig();
      await loadMailConfig();
      status('已刷新');
    });
  }

  function init() {
    build();
    bind();
    loadProxyConfig();
    loadPoolConfig();
    loadMailConfig();
    refreshStatus();
    setInterval(refreshStatus, 3000);
    applyBridgeRouteVisibility();
    startBridgeObserver();
    scheduleBridgeSync();
    window.setTimeout(scheduleBridgeSync, 300);
    window.setTimeout(scheduleBridgeSync, 1200);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
