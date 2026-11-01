/* Sookit 扩展面板：读扩展设置、探测连接、按需请求启动桌面端。 */
const SITES_KEY = 'sookitSites';

const el = {
  status: document.getElementById('status'),
  statusText: document.getElementById('status-text'),
  youtube: document.getElementById('toggle-youtube'),
  x: document.getElementById('toggle-x'),
  master: document.getElementById('master-value'),
  primary: document.getElementById('primary'),
};

function setStatus(state, text) {
  el.status.dataset.state = state;
  el.statusText.textContent = text;
}

// 强调色与明暗都来自桌面端回执，面板跟着设置页走；离线时退回系统偏好。
function applyTheme(color, dark) {
  if (typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color)) {
    document.documentElement.style.setProperty('--accent', color);
  }
  if (typeof dark === 'boolean') {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  }
}

async function loadSites() {
  try {
    const stored = await chrome.storage.local.get(SITES_KEY);
    const sites = stored?.[SITES_KEY] || {};
    el.youtube.checked = sites.youtube !== false;
    el.x.checked = sites.x !== false;
  } catch {
    el.youtube.checked = true;
    el.x.checked = true;
  }
}

async function saveSite(name, checked) {
  try {
    const stored = await chrome.storage.local.get(SITES_KEY);
    const sites = {...(stored?.[SITES_KEY] || {})};
    sites[name] = checked;
    await chrome.storage.local.set({[SITES_KEY]: sites});
  } catch {
    const input = el[name];
    if (input) input.checked = !checked;
    setStatus('offline', '设置未保存');
  }
}

function sendPanel(op) {
  return new Promise(resolve => {
    try {
      chrome.runtime.sendMessage({type: 'sookit.panel', op}, response => {
        resolve({response: response ?? null, error: chrome.runtime.lastError?.message || ''});
      });
    } catch (error) {
      resolve({response: null, error: String(error?.message || error)});
    }
  });
}

function offlineKind(detail) {
  if (/不支持的浏览器请求|不支持的面板操作/.test(detail)) return 'outdated';
  if (/启动超时/.test(detail)) return 'failed';
  if (/未连接到 Sookit|not found|not registered|forbidden|native/i.test(detail)) return 'missing';
  return 'offline';
}

// 总闸由 host 直接读配置文件得到，桌面端没开也照样显示。
function showMaster(enabled) {
  if (enabled === false) {
    el.master.textContent = '已关闭';
    setStatus('paused', '已暂停');
    return;
  }
  el.master.textContent = enabled === true ? '已开启' : '未知';
  setStatus('ok', '已连接');
}

function showConnected(status) {
  applyTheme(status.theme_color, status.dark);
  showMaster(status.enabled);
  el.primary.textContent = '启动 Sookit';
  el.primary.disabled = false;
}

// 桥接通了但桌面端没开：扩展本身是生效的，所以仍显示「已连接」，主按钮负责启动。
function showDesktopDown(status) {
  applyTheme(status.theme_color);
  showMaster(status.enabled);
  el.primary.textContent = '启动 Sookit';
  el.primary.disabled = false;
}

function showOffline(kind) {
  el.master.textContent = '未知';
  setStatus('offline', '未连接');
  el.primary.textContent = '启动 Sookit';
  el.primary.disabled = kind === 'outdated' || kind === 'missing';
}

async function refresh() {
  const {response, error} = await sendPanel('status');
  if (response?.ok) {
    showConnected(response);
    return;
  }
  if (response?.code === 'offline') {
    showDesktopDown(response);
    return;
  }
  showOffline(offlineKind(String(response?.error || error || '')));
}

async function launch() {
  if (el.primary.disabled) return;
  el.primary.disabled = true;
  el.primary.textContent = '正在启动…';
  const {response, error} = await sendPanel('launch');
  el.primary.textContent = '启动 Sookit';
  if (response?.ok) {
    if (response.status?.ok) showConnected(response.status);
    else refresh();
    return;
  }
  showOffline(offlineKind(String(response?.error || error || '')));
}

el.primary.addEventListener('click', () => { launch(); });
el.youtube.addEventListener('change', () => { saveSite('youtube', el.youtube.checked); });
el.x.addEventListener('change', () => { saveSite('x', el.x.checked); });
loadSites();
refresh();
