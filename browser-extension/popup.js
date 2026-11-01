/* Sookit 扩展面板：读扩展设置、探测连接、按需请求启动桌面端。 */
const SITES_KEY = 'sookitSites';
const THEME_KEY = 'sookitTheme';

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

// 上次回执里的主题先落盘，下次打开面板直接用，不用等 host 往返。
async function rememberTheme(color, dark) {
  const valid = typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color);
  if (!valid && typeof dark !== 'boolean') return;
  try {
    const stored = await chrome.storage.local.get(THEME_KEY);
    const theme = {...(stored?.[THEME_KEY] || {})};
    if (valid) theme.color = color;
    if (typeof dark === 'boolean') theme.dark = dark;
    await chrome.storage.local.set({[THEME_KEY]: theme});
  } catch {
    // 存不下就继续用回执里的值，下次打开再更新。
  }
}

async function loadTheme() {
  try {
    const stored = await chrome.storage.local.get(THEME_KEY);
    const theme = stored?.[THEME_KEY];
    if (theme) applyTheme(theme.color, theme.dark);
  } catch {
    // 没有缓存就用默认薄荷绿 + 系统明暗。
  } finally {
    document.body.dataset.ready = 'true';
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
    setStatus('error', '设置未保存');
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
  rememberTheme(status.theme_color, status.dark);
  showMaster(status.enabled);
  el.primary.textContent = '启动 Sookit';
  el.primary.disabled = false;
}

// 桥接通了但桌面端没开：扩展本身是生效的，所以仍显示「已连接」，主按钮负责启动。
function showDesktopDown(status) {
  applyTheme(status.theme_color);
  rememberTheme(status.theme_color);
  showMaster(status.enabled);
  el.primary.textContent = '启动 Sookit';
  el.primary.disabled = false;
}

function showOffline(kind, text = '未连接') {
  el.master.textContent = '未知';
  setStatus('offline', text);
  el.primary.textContent = '启动 Sookit';
  el.primary.disabled = kind === 'outdated' || kind === 'missing';
}

async function refresh() {
  setStatus('busy', '连接中…');
  const {response, error} = await sendPanel('status');
  if (response?.ok) {
    showConnected(response);
    return;
  }
  if (response?.code === 'offline') {
    showDesktopDown(response);
    return;
  }
  const kind = offlineKind(String(response?.error || error || ''));
  showOffline(kind, kind === 'outdated' ? '版本过旧' : '未连接');
}

async function launch() {
  if (el.primary.disabled) return;
  el.primary.disabled = true;
  // 进度只在标题栏里显示，主按钮文字保持「启动 Sookit」。
  setStatus('busy', '正在启动…');
  const {response, error} = await sendPanel('launch');
  el.primary.disabled = false;
  if (response?.ok) {
    if (response.status?.ok) showConnected(response.status);
    else refresh();
    return;
  }
  showOffline(offlineKind(String(response?.error || error || '')), '启动失败');
}

el.primary.addEventListener('click', () => { launch(); });
el.youtube.addEventListener('change', () => { saveSite('youtube', el.youtube.checked); });
el.x.addEventListener('change', () => { saveSite('x', el.x.checked); });
loadSites();
loadTheme();
refresh();
