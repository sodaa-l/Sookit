/* 页面脚本只传递视频链接和受限展示信息；本机调用始终经过扩展后台。 */
const HOST = 'com.sookit.browser';
const ACTIONS = new Set(['sniff', 'download_best', 'download_cover', 'parse_gif']);
const YOUTUBE_HOSTS = new Set(['www.youtube.com', 'youtube.com', 'm.youtube.com', 'youtu.be']);
const X_HOSTS = new Set(['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com']);

function youtubeUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || !YOUTUBE_HOSTS.has(url.hostname)
      || url.username || url.password || (url.port && url.port !== '443')) {
    throw new Error('仅支持 HTTPS YouTube 视频链接');
  }
  return url;
}

function videoUrl(value) {
  const url = youtubeUrl(value);
  let id = '';
  if (url.hostname === 'youtu.be') id = url.pathname.slice(1);
  else if (url.pathname === '/watch' && url.searchParams.getAll('v').length === 1) id = url.searchParams.get('v');
  else if (/^\/(shorts|live)\//.test(url.pathname)) id = url.pathname.split('/')[2];
  if (!/^[A-Za-z0-9_-]{11}$/.test(id)) throw new Error('没有找到有效的 YouTube 视频 ID');
  return `https://www.youtube.com/watch?v=${id}`;
}

function xStatusUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || !X_HOSTS.has(url.hostname)
      || url.username || url.password || (url.port && url.port !== '443')) {
    throw new Error('仅支持 HTTPS X 状态链接');
  }
  if (!/^\/(?:[^/]+\/status\/|i\/web\/status\/)\d+(?:\/(?:video|photo)\/\d+)?\/?$/.test(url.pathname)) {
    throw new Error('没有找到有效的 X 状态链接');
  }
  const path = url.pathname.replace(/\/(?:video|photo)\/\d+\/?$/, '').replace(/\/+$/, '');
  return `https://x.com${path}`;
}

function requestUrl(action, value) {
  if (action === 'parse_gif') return xStatusUrl(value);
  if (action === 'download_cover') return videoUrl(value);
  try {
    return videoUrl(value);
  } catch (youtubeError) {
    try {
      return xStatusUrl(value);
    } catch {
      throw youtubeError;
    }
  }
}

function pagePlatform(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) {
    throw new Error('浏览器页面来源无效');
  }
  if (YOUTUBE_HOSTS.has(url.hostname)) return 'youtube';
  if (X_HOSTS.has(url.hostname)) return 'x';
  throw new Error('浏览器页面来源无效');
}

function maybeXStatusUrl(value) {
  try {
    return xStatusUrl(value);
  } catch {
    return null;
  }
}

function cleanDisplayText(value, maxLength) {
  if (typeof value !== 'string') return '';
  return value.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, maxLength);
}

function videoMetadata(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const result = {};
  const title = cleanDisplayText(value.title, 500);
  const channel = cleanDisplayText(value.channel, 200);
  if (title) result.title = title;
  if (channel) result.channel = channel;
  if (Number.isSafeInteger(value.duration) && value.duration > 0 && value.duration <= 604800) {
    result.duration = value.duration;
  }
  return Object.keys(result).length ? result : null;
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (!ACTIONS.has(message?.action)) return false;
  (async () => {
    if (sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0) {
      throw new Error('请求来源无效');
    }
    const url = requestUrl(message.action, message.url);
    const platform = url.startsWith('https://x.com/') ? 'x' : 'youtube';
    if (pagePlatform(sender.url) !== platform) throw new Error('请求来源与视频链接不匹配');

    // SPA 的 sender.url 可能是旧路由；仍以当前标签页及其 pendingUrl 判定站点。
    let tab;
    try {
      tab = await chrome.tabs.get(sender.tab.id);
    } catch {
      throw new Error('无法确认当前视频页面，请刷新页面后重试');
    }
    let tabPlatform;
    try {
      tabPlatform = pagePlatform(tab.url);
    } catch {
      throw new Error('无法确认当前视频页面，请刷新页面后重试');
    }
    if (tabPlatform !== platform) throw new Error('页面已切换，请重试');
    if (tab.pendingUrl) {
      let pendingPlatform;
      try {
        pendingPlatform = pagePlatform(tab.pendingUrl);
      } catch {
        throw new Error('页面正在切换');
      }
      if (pendingPlatform !== platform) throw new Error('页面正在切换');
    }
    if (platform === 'youtube') {
      let currentUrl;
      try {
        currentUrl = videoUrl(tab.url);
        if (tab.pendingUrl && videoUrl(tab.pendingUrl) !== currentUrl) {
          throw new Error('页面正在切换');
        }
      } catch {
        throw new Error('无法确认当前视频页面，请刷新页面后重试');
      }
      if (currentUrl !== url) throw new Error('页面已切换，请重试');
    } else {
      const currentStatusUrl = maybeXStatusUrl(tab.url);
      if (currentStatusUrl && currentStatusUrl !== url) throw new Error('页面已切换，请重试');
    }
    try {
      const nativeMessage = {action: message.action, url};
      if (message.action === 'download_best' || message.action === 'download_cover') {
        const metadata = videoMetadata(message.video_metadata);
        if (metadata) nativeMessage.video_metadata = metadata;
      }
      return await chrome.runtime.sendNativeMessage(HOST, nativeMessage);
    } catch (error) {
      const detail = String(error?.message || error);
      if (/not found|not registered|forbidden|Access to the specified native/i.test(detail)) {
        throw new Error('未连接到 Sookit，请安装新版 Sookit 或运行浏览器桥接注册脚本');
      }
      throw new Error(`Sookit 连接失败：${detail}`);
    }
  })().then(reply, error => reply({ok: false, error: String(error.message || error)}));
  return true;
});
