/* Sookit 扩展面板：读扩展设置、探测连接、按需请求启动桌面端。 */
const SITES_KEY = 'sookitSites';

const el = {
  status: document.getElementById('status'),
  statusText: document.getElementById('status-text'),
  youtube: document.getElementById('toggle-youtube'),
  x: document.getElementById('toggle-x'),
  master: document.getElementById('master-value'),
  primary: document.getElementById('primary'),
  hint: document.getElementById('hint'),
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
    el.hint.textContent = '设置保存失败，请重新加载扩展后再试';
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

function showConnected(status) {
  applyTheme(status.theme_color, status.dark);
  el.master.textContent = status.enabled ? '已开启' : '已关闭';
  if (status.enabled) {
    setStatus('ok', '已连接');
    el.primary.textContent = '已连接';
    el.primary.disabled = true;
    el.hint.textContent = '在页面里点 Sookit 按钮即可调用桌面端';
    return;
  }
  setStatus('paused', '已暂停');
  el.primary.textContent = '已暂停';
  el.primary.disabled = true;
  el.hint.textContent = '请到 Sookit 设置页打开「浏览器扩展调用」';
}

// 桥接通了但桌面端没开：扩展本身是生效的，所以仍显示「已连接」，再用主按钮和提示说明现状。
function showDesktopDown() {
  el.master.textContent = '未知';
  setStatus('ok', '已连接');
  el.primary.textContent = '启动 Sookit';
  el.primary.disabled = false;
  el.hint.textContent = '扩展已连接；Sookit 未运行，启动后才能调用桌面端';
}

function showOffline(kind) {
  el.master.textContent = '未知';
  setStatus('offline', '未连接');
  el.primary.textContent = '启动 Sookit';
  el.primary.disabled = kind === 'outdated' || kind === 'missing';
  if (kind === 'outdated') {
    el.hint.textContent = 'Sookit 版本过旧，请更新桌面端后重新注册桥接';
  } else if (kind === 'missing') {
    el.hint.textContent = '桥接未注册：重装 Sookit，或运行 scripts/register_browser_bridge.py';
  } else if (kind === 'failed') {
    el.hint.textContent = '启动没有成功，请手动启动 Sookit 后重试';
  } else {
    el.hint.textContent = '点击后会自动打开 Sookit；已在运行时通常几秒内连上';
  }
}

async function refresh() {
  const {response, error} = await sendPanel('status');
  if (response?.ok) {
    showConnected(response);
    return;
  }
  if (response?.code === 'offline') {
    showDesktopDown();
    return;
  }
  showOffline(offlineKind(String(response?.error || error || '')));
}

async function launch() {
  if (el.primary.disabled) return;
  el.primary.disabled = true;
  el.primary.textContent = '正在启动…';
  el.hint.textContent = '首次启动可能需要几秒；关掉面板后 Sookit 仍会继续启动';
  const {response, error} = await sendPanel('launch');
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
