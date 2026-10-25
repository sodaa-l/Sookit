(() => {
  'use strict';
  if (window.top !== window || document.getElementById('sookit-extension-marker')) return;
  const marker = document.createElement('meta');
  marker.id = 'sookit-extension-marker';
  document.head.append(marker);
  let button;
  let feedback;
  let busy = false;
  let sendingUrl = '';
  let scheduled = false;
  let feedbackTimer;

  function currentVideoUrl() {
    const url = new URL(location.href);
    const id = url.searchParams.get('v');
    return url.pathname === '/watch' && /^[A-Za-z0-9_-]{11}$/.test(id || '')
      ? `https://www.youtube.com/watch?v=${id}` : null;
  }

  function tell(text, error = false) {
    if (!feedback) {
      feedback = document.createElement('div');
      feedback.id = 'sookit-feedback';
      feedback.setAttribute('role', 'status');
      feedback.setAttribute('aria-live', 'polite');
      document.body.append(feedback);
    }
    feedback.textContent = text;
    feedback.dataset.error = String(error);
    feedback.hidden = false;
    clearTimeout(feedbackTimer);
    feedbackTimer = setTimeout(() => { feedback.hidden = true; }, error ? 7000 : 4000);
  }

  async function send() {
    const url = currentVideoUrl();
    if (!url || busy) return;
    busy = true;
    sendingUrl = url;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.title = '正在发送到 Sookit…';
    try {
      const response = await chrome.runtime.sendMessage({action: 'sniff', url});
      if (currentVideoUrl() !== url) return;
      if (!response?.ok) throw new Error(response?.error || 'Sookit 未确认接收链接');
      tell(response.message || 'Sookit 已在后台开始嗅探，完成后会发送系统通知');
    } catch (error) {
      if (currentVideoUrl() === url) tell(error.message || '无法连接 Sookit，请刷新页面后重试', true);
    } finally {
      busy = false;
      sendingUrl = '';
      if (button) {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.title = '用 Sookit 在后台嗅探此视频';
      }
    }
  }

  function createButton() {
    const element = document.createElement('button');
    element.id = 'sookit-sniff-button';
    element.type = 'button';
    element.title = '用 Sookit 在后台嗅探此视频';
    element.setAttribute('aria-label', '用 Sookit 在后台嗅探此视频');
    const icon = document.createElement('img');
    icon.src = chrome.runtime.getURL('icons/sookit-48.png');
    icon.alt = '';
    icon.width = 24;
    icon.height = 24;
    const label = document.createElement('span');
    label.textContent = 'Sookit';
    element.append(icon, label);
    element.addEventListener('click', send);
    return element;
  }

  function mount() {
    scheduled = false;
    if (!currentVideoUrl()) {
      button?.remove();
      if (feedback) feedback.hidden = true;
      return;
    }
    // 新旧 YouTube 操作栏均支持；不抓播放器菜单或评论区的同名节点。
    const target = document.querySelector('ytd-watch-metadata #actions #top-level-buttons-computed')
      || document.querySelector('ytd-watch-metadata #actions ytd-menu-renderer #top-level-buttons-computed')
      || document.querySelector('#info #menu-container #top-level-buttons-computed');
    if (!target) return;
    if (!button) button = createButton();
    if (button.parentElement !== target) target.append(button);
    button.disabled = busy;
    if (feedback && sendingUrl && sendingUrl !== currentVideoUrl()) feedback.hidden = true;
  }

  function scheduleMount() {
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(mount);
    }
  }
  new MutationObserver(scheduleMount).observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener('yt-navigate-finish', scheduleMount);
  window.addEventListener('popstate', scheduleMount);
  scheduleMount();
})();
