/* 页面脚本只传递链接；本机调用始终经过扩展后台。 */
const HOST = 'com.sookit.browser';
const ACTIONS = new Set(['sniff', 'download_best', 'download_cover']);
const ALLOWED_HOSTS = new Set(['www.youtube.com', 'youtube.com', 'm.youtube.com', 'youtu.be']);

function videoUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'https:' || !ALLOWED_HOSTS.has(url.hostname)
      || url.username || url.password || (url.port && url.port !== '443')) {
    throw new Error('仅支持 HTTPS YouTube 视频链接');
  }
  let id = '';
  if (url.hostname === 'youtu.be') id = url.pathname.slice(1);
  else if (url.pathname === '/watch' && url.searchParams.getAll('v').length === 1) id = url.searchParams.get('v');
  else if (/^\/(shorts|live)\//.test(url.pathname)) id = url.pathname.split('/')[2];
  if (!/^[A-Za-z0-9_-]{11}$/.test(id)) throw new Error('没有找到有效的 YouTube 视频 ID');
  return `https://www.youtube.com/watch?v=${id}`;
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (!ACTIONS.has(message?.action)) return false;
  (async () => {
    if (sender.id !== chrome.runtime.id || !sender.tab || sender.frameId !== 0) {
      throw new Error('请求来源无效');
    }
    const url = videoUrl(message.url);
    if (videoUrl(sender.url) !== url) throw new Error('页面已切换，请重试');
    try {
      return await chrome.runtime.sendNativeMessage(HOST, {action: message.action, url});
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
