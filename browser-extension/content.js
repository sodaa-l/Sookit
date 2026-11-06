(() => {
  'use strict';
  if (window.top !== window || document.getElementById('sookit-extension-marker')) return;
  const marker = document.createElement('meta');
  marker.id = 'sookit-extension-marker';
  document.head.append(marker);
  const actions = [
    {id: 'sniff', label: '嗅探视频', icon: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
      pending: '正在嗅探视频…', message: 'Sookit 已在后台开始嗅探，完成后会发送系统通知'},
    {id: 'download_best', label: '下载最佳画质', icon: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
      pending: '正在解析最佳画质…', message: '最佳画质下载已加入 Sookit 任务队列'},
    {id: 'download_cover', label: '下载最佳封面', icon: 'M3 3h18v18H3ZM3 17l6-6 4 4 3-3 5 5M8 7h.01',
      pending: '正在解析封面…', message: '封面下载已开始'},
    {id: 'monitor', label: '直播 / Premiere 监控', icon: 'M12 8v4l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
      pending: '正在添加监控…', message: '已添加到 Sookit 监控列表，结束后自动下载'}
  ];
  const xActions = [
    actions[0],
    actions[1],
    {id: 'parse_gif', label: '解析动图', icon: 'M4 4h16v16H4zM9 8l6 4-6 4z',
      pending: '正在解析动图…', message: '已在 Sookit 动图页开始解析'}
  ];
  const xHosts = new Set(['x.com', 'www.x.com', 'twitter.com', 'www.twitter.com']);
  let button;
  let menu;
  let menuUrl = '';
  let pageUrl = currentVideoUrl();
  let pageGeneration = 0;
  let feedback;
  let feedbackText;
  let busy = false;
  let scheduled = false;
  let feedbackTimer;
  let xMenu;
  let xMenuButton;
  let xMenuUrl = '';
  let xMenuArticle;
  let xButtonSequence = 0;
  let xPageUrl = location.href;
  // 站点开关存在扩展 local storage（面板写入）；改动后立即增删本站点按钮。
  const SITES_KEY = 'sookitSites';
  const sites = {youtube: true, x: true};
  function applySites(value) {
    if (!value || typeof value !== 'object') return;
    sites.youtube = value.youtube !== false;
    sites.x = value.x !== false;
    scheduleMount();
  }
  try {
    chrome.storage?.local?.get(SITES_KEY, stored => applySites(stored?.[SITES_KEY]));
    chrome.storage?.onChanged?.addListener((changes, area) => {
      if (area === 'local' && changes?.[SITES_KEY]) applySites(changes[SITES_KEY].newValue);
    });
  } catch {}

  function currentVideoUrl() {
    const url = new URL(location.href);
    const id = url.searchParams.get('v');
    if (url.pathname === '/watch' && /^[A-Za-z0-9_-]{11}$/.test(id || '')) {
      return `https://www.youtube.com/watch?v=${id}`;
    }
    const short = url.pathname.match(/^\/shorts\/([A-Za-z0-9_-]{11})\/?$/);
    return short ? `https://www.youtube.com/watch?v=${short[1]}` : null;
  }

  function isShortsPage() {
    return /^\/shorts\/[A-Za-z0-9_-]{11}\/?$/.test(new URL(location.href).pathname);
  }

  function isXSite() {
    return xHosts.has(location.hostname.toLowerCase());
  }

  function normalizeXStatusUrl(value) {
    try {
      const url = new URL(value, location.href);
      if (url.protocol !== 'https:' || !xHosts.has(url.hostname.toLowerCase())
          || url.username || url.password || (url.port && url.port !== '443')) return null;
      if (!/^\/(?:[^/]+\/status\/|i\/web\/status\/)\d+(?:\/(?:video|photo)\/\d+)?\/?$/.test(url.pathname)) return null;
      const path = url.pathname.replace(/\/(?:video|photo)\/\d+\/?$/, '').replace(/\/+$/, '');
      return 'https://x.com' + path;
    } catch {
      return null;
    }
  }

  function getXStatusUrl(article) {
    const links = [...article.querySelectorAll('a[href*="/status/"]')]
      .filter(link => link.closest('article[data-testid="tweet"]') === article)
      .sort((left, right) => Number(Boolean(right.querySelector('time')))
        - Number(Boolean(left.querySelector('time'))));
    for (const link of links) {
      const url = normalizeXStatusUrl(link.href);
      if (url) return url;
    }
    return null;
  }

  function tweetHasVideo(article) {
    return [...article.querySelectorAll('video, [data-testid="videoPlayer"]')]
      .some(media => media.closest('article[data-testid="tweet"]') === article);
  }

  function findGrokButton(article) {
    return [...article.querySelectorAll('button[aria-label], button[data-testid]')]
      .find(candidate => candidate.closest('article[data-testid="tweet"]') === article
        && /grok/i.test((candidate.getAttribute('aria-label') || '') + ' '
          + (candidate.getAttribute('data-testid') || '')));
  }

  function cleanDisplayText(value, maxLength) {
    if (typeof value !== 'string') return '';
    return value.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, maxLength);
  }

  function readVideoMetadata() {
    const title = cleanDisplayText(
      document.querySelector('meta[name="title"]')?.content
        || document.querySelector('meta[property="og:title"]')?.content
        || document.querySelector('ytd-watch-metadata h1')?.textContent
        || document.title.replace(/\s*-\s*YouTube\s*$/i, ''),
      500,
    );
    const author = document.querySelector('[itemprop="author"] [itemprop="name"]');
    const channel = cleanDisplayText(
      author?.getAttribute('content') || author?.textContent
        || document.querySelector('ytd-reel-video-renderer[is-active] #channel-name')?.textContent
        || document.querySelector('ytd-watch-metadata #owner #channel-name')?.textContent,
      200,
    );
    const durationElement = document.querySelector('[itemprop="duration"]');
    const durationValue = durationElement?.getAttribute('content') || durationElement?.textContent || '';
    const match = /^PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?$/i.exec(durationValue.trim());
    let duration = null;
    if (match && match.slice(1).some(Boolean)) {
      duration = Math.round(Number(match[1] || 0) * 3600
        + Number(match[2] || 0) * 60 + Number(match[3] || 0));
    }
    if (!duration) {
      const videoDuration = document.querySelector('video.html5-main-video')?.duration;
      if (Number.isFinite(videoDuration) && videoDuration > 0) duration = Math.round(videoDuration);
    }
    const metadata = {};
    if (title && title.toLowerCase() !== 'youtube') metadata.title = title;
    if (channel) metadata.channel = channel;
    if (Number.isSafeInteger(duration) && duration > 0 && duration <= 604800) metadata.duration = duration;
    return metadata;
  }

  async function readVideoMetadataWhenReady(url, generation) {
    const metadata = {};
    const deadline = Date.now() + 2000;
    while (true) {
      if (currentVideoUrl() !== url || generation !== pageGeneration) return {};
      Object.assign(metadata, readVideoMetadata());
      if (metadata.title && metadata.channel && metadata.duration) return metadata;
      if (Date.now() >= deadline) return metadata;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }

  function getShortsRendererVideoIds(renderer) {
    const identities = new Set();
    const attributeVideoId = renderer.getAttribute('video-id');
    if (/^[A-Za-z0-9_-]{11}$/.test(attributeVideoId || '')) identities.add(attributeVideoId);

    const links = [...renderer.querySelectorAll("a[href*='/shorts/']")];
    const playerLinks = links.filter(link => link.matches("a.ytp-title-link[href*='/shorts/']"));
    for (const link of playerLinks.length ? playerLinks : links) {
      try {
        const href = link.getAttribute('href');
        const videoId = href && new URL(href, location.href).pathname.match(/^\/shorts\/([A-Za-z0-9_-]{11})\/?$/)?.[1];
        if (videoId) identities.add(videoId);
      } catch {
        // Ignore malformed or incomplete links while YouTube hydrates a reel.
      }
    }
    return identities;
  }

  function intersectsViewport(element) {
    const rect = element.getBoundingClientRect();
    const height = window.innerHeight || document.documentElement.clientHeight;
    const width = window.innerWidth || document.documentElement.clientWidth;
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
      && rect.top < height && rect.left < width;
  }

  function isActuallyVisible(element) {
    if (!element) return false;
    if (typeof element.checkVisibility === 'function') {
      try {
        return element.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
      } catch {
        // Older implementations may expose checkVisibility without its options.
      }
    }

    if (['hidden', 'collapse'].includes(getComputedStyle(element).visibility)) return false;
    for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      if (style.display === 'none' || Number(style.opacity) <= 0) return false;
    }
    return true;
  }

  function isFullyInViewport(element) {
    const rect = element.getBoundingClientRect();
    const height = window.innerHeight || document.documentElement.clientHeight;
    const width = window.innerWidth || document.documentElement.clientWidth;
    return rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.left >= 0
      && rect.bottom <= height && rect.right <= width;
  }

  function shortsActionBar(renderer) {
    return renderer.querySelector('reel-action-bar-view-model') || renderer.querySelector('#actions');
  }

  function hasConflictingShortButton(actionBar, videoId) {
    const existing = actionBar.querySelector('#sookit-sniff-button');
    return Boolean(existing?.dataset.shortVideoId && existing.dataset.shortVideoId !== videoId);
  }

  function findShortsActionBar(url) {
    const videoId = new URL(url).searchParams.get('v');
    if (!/^[A-Za-z0-9_-]{11}$/.test(videoId || '')) return null;

    const renderers = [...document.querySelectorAll('ytd-reel-video-renderer')]
      .filter(renderer => intersectsViewport(renderer) && isActuallyVisible(renderer))
      .map(renderer => ({renderer, actionBar: shortsActionBar(renderer), videoIds: getShortsRendererVideoIds(renderer)}))
      .filter(candidate => !candidate.actionBar || isActuallyVisible(candidate.actionBar));
    const matching = renderers.filter(candidate => candidate.videoIds.size === 1
      && candidate.videoIds.has(videoId));
    if (matching.length === 1) return matching[0].actionBar;
    if (matching.length > 1) return null;

    const identityless = renderers.filter(candidate => candidate.videoIds.size === 0
      && (!candidate.actionBar || !hasConflictingShortButton(candidate.actionBar, videoId)));
    const active = identityless.filter(candidate => candidate.renderer.getAttribute('is-active') !== null);
    if (active.length === 1) return active[0].actionBar;
    if (renderers.some(candidate => candidate.videoIds.size > 0)) return null;
    if (identityless.length === 1) return identityless[0].actionBar;

    const fullyVisible = identityless.filter(candidate => isFullyInViewport(candidate.renderer));
    return fullyVisible.length === 1 ? fullyVisible[0].actionBar : null;
  }

  function hideFeedback() {
    clearTimeout(feedbackTimer);
    if (feedback) feedback.hidden = true;
  }

  // 右上角小窗：loading 等结果，成功两秒后自动收起，失败常驻到用户关闭。
  function ensureFeedback() {
    if (feedback) return feedback;
    feedback = document.createElement('div');
    feedback.id = 'sookit-feedback';
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    const spinner = document.createElement('span');
    spinner.className = 'sookit-feedback-spinner';
    spinner.setAttribute('aria-hidden', 'true');
    feedbackText = document.createElement('span');
    feedbackText.className = 'sookit-feedback-text';
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'sookit-feedback-close';
    close.setAttribute('aria-label', '关闭提示');
    close.textContent = '\u00d7';
    close.addEventListener('click', hideFeedback);
    feedback.append(spinner, feedbackText, close);
    document.body.append(feedback);
    return feedback;
  }

  function tell(text, state = 'success') {
    const element = ensureFeedback();
    element.dataset.state = state;
    feedbackText.textContent = text;
    element.hidden = false;
    clearTimeout(feedbackTimer);
    // 成功只停留两秒；进行中和失败都常驻，由关闭按钮或下一条指令收走。
    if (state === 'success') {
      feedbackTimer = setTimeout(hideFeedback, 2000);
    }
  }

  function closeMenu(restoreFocus = false) {
    if (menu) menu.hidden = true;
    menuUrl = '';
    button?.setAttribute('aria-expanded', 'false');
    if (restoreFocus && button?.isConnected) button.focus();
  }

  function positionMenu() {
    if (!menu || menu.hidden) return;
    if (!button?.isConnected || currentVideoUrl() !== menuUrl) {
      closeMenu();
      return;
    }
    const rect = button.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) {
      closeMenu();
      return;
    }
    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
    const top = rect.bottom + height + 8 <= window.innerHeight
      ? rect.bottom + 8 : Math.max(8, rect.top - height - 8);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  async function send(action) {
    const url = currentVideoUrl();
    if (!url || busy || url !== menuUrl) {
      closeMenu();
      return;
    }
    const generation = pageGeneration;
    closeMenu(true);
    busy = true;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.title = '正在发送到 Sookit…';
    tell(action.pending || '正在发送到 Sookit…', 'loading');
    try {
      const message = {action: action.id, url};
      if (action.id === 'download_best' || action.id === 'download_cover' || action.id === 'monitor') {
        message.video_metadata = await readVideoMetadataWhenReady(url, generation);
      }
      if (currentVideoUrl() !== url || generation !== pageGeneration) return;
      const response = await chrome.runtime.sendMessage(message);
      if (currentVideoUrl() !== url || generation !== pageGeneration) return;
      if (!response?.ok) throw new Error(response?.error || 'Sookit 未确认接收请求');
      tell(response.message || action.message);
    } catch (error) {
      if (currentVideoUrl() === url && generation === pageGeneration) {
        tell(error.message || '无法连接 Sookit，请刷新页面后重试', 'error');
      }
    } finally {
      busy = false;
      // 页面切走等没有结果的路径，别把小窗留在 loading 上。
      if (feedback?.dataset.state === 'loading') hideFeedback();
      if (button) {
        button.disabled = false;
        button.removeAttribute('aria-busy');
        button.title = 'Sookit 视频工具';
      }
    }
  }

  function createMenu() {
    const element = document.createElement('div');
    element.id = 'sookit-action-menu';
    element.hidden = true;
    element.setAttribute('role', 'menu');
    element.setAttribute('aria-label', 'Sookit 视频工具');
    element.setAttribute('aria-labelledby', 'sookit-sniff-button');
    for (const action of actions) {
      const item = document.createElement('button');
      item.type = 'button';
      item.tabIndex = -1;
      item.dataset.action = action.id;
      item.setAttribute('role', 'menuitem');
      const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      icon.setAttribute('viewBox', '0 0 24 24');
      icon.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', action.icon);
      icon.append(path);
      const label = document.createElement('span');
      label.textContent = action.label;
      item.append(icon, label);
      item.addEventListener('click', () => send(action));
      element.append(item);
    }
    element.addEventListener('keydown', event => {
      const items = [...element.children];
      const index = items.indexOf(document.activeElement);
      let next;
      if (event.key === 'ArrowDown') next = (index + 1) % items.length;
      else if (event.key === 'ArrowUp') next = (index + items.length - 1) % items.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = items.length - 1;
      else if (event.key === 'Tab') closeMenu();
      if (next !== undefined) {
        event.preventDefault();
        items[next].focus();
      }
    });
    document.body.append(element);
    return element;
  }

  function toggleMenu(lastItem = false) {
    if (busy || !currentVideoUrl()) return;
    if (menu && !menu.hidden) {
      closeMenu(true);
      return;
    }
    if (!menu) menu = createMenu();
    menuUrl = currentVideoUrl();
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    positionMenu();
    if (!menu.hidden) menu.children[lastItem ? actions.length - 1 : 0].focus();
  }

  function createButton() {
    const element = document.createElement('button');
    element.id = 'sookit-sniff-button';
    element.type = 'button';
    element.title = 'Sookit 视频工具';
    element.setAttribute('aria-label', 'Sookit 视频工具');
    element.setAttribute('aria-haspopup', 'menu');
    element.setAttribute('aria-controls', 'sookit-action-menu');
    element.setAttribute('aria-expanded', 'false');
    const icon = document.createElement('img');
    icon.src = chrome.runtime.getURL('icons/sookit-48.png');
    icon.alt = '';
    icon.width = 24;
    icon.height = 24;
    const label = document.createElement('span');
    label.textContent = 'Sookit';
    element.append(icon, label);
    element.addEventListener('click', () => toggleMenu());
    element.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (!menu || menu.hidden) toggleMenu(event.key === 'ArrowUp');
      }
    });
    return element;
  }

  function closeXMenu(restoreFocus = false) {
    if (xMenu) xMenu.hidden = true;
    const trigger = xMenuButton;
    trigger?.setAttribute('aria-expanded', 'false');
    xMenuButton = null;
    xMenuUrl = '';
    xMenuArticle = null;
    if (restoreFocus && trigger?.isConnected) trigger.focus();
  }

  function positionXMenu() {
    if (!xMenu || xMenu.hidden) return;
    if (!xMenuButton?.isConnected || !xMenuArticle?.isConnected || !isXSite()
        || getXStatusUrl(xMenuArticle) !== xMenuUrl
        || xMenuButton.dataset.xStatusUrl !== xMenuUrl) {
      closeXMenu();
      return;
    }
    const rect = xMenuButton.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) {
      closeXMenu();
      return;
    }
    const width = xMenu.offsetWidth;
    const height = xMenu.offsetHeight;
    const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
    const top = rect.bottom + height + 8 <= window.innerHeight
      ? rect.bottom + 8 : Math.max(8, rect.top - height - 8);
    xMenu.style.left = left + 'px';
    xMenu.style.top = top + 'px';
  }

  function safeXPosterUrl(value) {
    if (typeof value !== 'string' || value.length > 2048) return '';
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || url.hostname.toLowerCase() !== 'pbs.twimg.com'
          || url.username || url.password || (url.port && url.port !== '443')) return '';
      return url.href;
    } catch {
      return '';
    }
  }

  function readXVideoMetadata(article) {
    const metadata = {};
    const tweetText = article.querySelector('[data-testid="tweetText"]');
    const title = cleanDisplayText(tweetText?.innerText || tweetText?.textContent || '', 500);
    if (title) metadata.title = title;

    const userName = article.querySelector('[data-testid="User-Name"]');
    const displayName = [...(userName?.querySelectorAll('span') || [])]
      .map(span => cleanDisplayText(span.innerText || span.textContent || '', 200))
      .find(value => value && !value.startsWith('@') && value !== '·');
    const authorLinks = [...(userName?.querySelectorAll('a') || [])]
      .map(link => cleanDisplayText(link.innerText || link.textContent || '', 200));
    const channel = displayName
      || authorLinks.find(value => value && !value.startsWith('@'))
      || authorLinks.find(value => value.startsWith('@'));
    if (channel) metadata.channel = channel;

    const player = article.querySelector('[data-testid="videoPlayer"]');
    const video = player?.querySelector('video') || article.querySelector('video');
    const duration = video?.duration;
    if (Number.isFinite(duration) && duration > 0) {
      const seconds = Math.round(duration);
      if (Number.isSafeInteger(seconds) && seconds > 0 && seconds <= 604800) {
        metadata.duration = seconds;
      }
    }
    const poster = video?.poster || player?.querySelector('img[src]')?.src;
    const coverUrl = safeXPosterUrl(poster);
    if (coverUrl) metadata.cover_url = coverUrl;
    return metadata;
  }

  async function sendX(action) {
    const trigger = xMenuButton;
    const article = xMenuArticle;
    const url = xMenuUrl;
    const pageHref = location.href;
    const stillCurrent = () => isXSite() && location.href === pageHref
      && article?.isConnected && getXStatusUrl(article) === url;
    if (!trigger?.isConnected || !article?.isConnected || !url || busy || !stillCurrent()) {
      closeXMenu();
      return;
    }
    closeXMenu(true);
    busy = true;
    trigger.disabled = true;
    trigger.setAttribute('aria-busy', 'true');
    trigger.title = '正在发送到 Sookit…';
    tell(action.pending || '正在发送到 Sookit…', 'loading');
    try {
      const message = {action: action.id, url};
      if (action.id === 'download_best') {
        const metadata = readXVideoMetadata(article);
        if (Object.keys(metadata).length) message.video_metadata = metadata;
      }
      const response = await chrome.runtime.sendMessage(message);
      if (!stillCurrent()) return;
      if (!response?.ok) throw new Error(response?.error || 'Sookit 未确认接收请求');
      tell(response.message || action.message);
    } catch (error) {
      if (stillCurrent()) tell(error.message || '无法连接 Sookit，请刷新页面后重试', 'error');
    } finally {
      busy = false;
      if (feedback?.dataset.state === 'loading') hideFeedback();
      if (trigger.isConnected) {
        trigger.disabled = false;
        trigger.removeAttribute('aria-busy');
        trigger.title = 'Sookit 视频工具';
      }
    }
  }

  function xMenuSolidBackground() {
    for (const node of [document.body, document.documentElement]) {
      const match = /^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/.exec(getComputedStyle(node).backgroundColor);
      if (match && (match[4] === undefined || Number(match[4]) > 0.9)) {
        return `rgb(${match[1]}, ${match[2]}, ${match[3]})`;
      }
    }
    return null;
  }

  function xMenuPalette() {
    const article = document.querySelector('article[data-testid="tweet"]');
    const textSample = article?.querySelector('[data-testid="tweetText"]') || article;
    const text = (textSample && getComputedStyle(textSample).color) || 'rgb(15, 20, 25)';
    const channels = (text.match(/\d+/g) || []).slice(0, 3).map(Number);
    const dark = channels.length === 3
      ? (channels[0] * 299 + channels[1] * 587 + channels[2] * 114) / 1000 > 128 : false;
    return {
      dark,
      text,
      background: xMenuSolidBackground() || (dark ? 'rgb(21, 32, 43)' : 'rgb(255, 255, 255)'),
    };
  }

  function createXMenu() {
    const palette = xMenuPalette();
    const element = document.createElement('div');
    element.id = 'sookit-x-action-menu';
    element.hidden = true;
    element.dataset.theme = palette.dark ? 'dark' : 'light';
    element.style.setProperty('--sookit-x-menu-text', palette.text);
    element.style.setProperty('--sookit-x-menu-background', palette.background);
    for (const action of xActions) {
      const item = document.createElement('button');
      item.type = 'button';
      item.tabIndex = -1;
      item.dataset.action = action.id;
      item.setAttribute('role', 'menuitem');
      const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      icon.setAttribute('viewBox', '0 0 24 24');
      icon.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', action.icon);
      icon.append(path);
      const label = document.createElement('span');
      label.textContent = action.label;
      item.append(icon, label);
      item.addEventListener('click', () => sendX(action));
      element.append(item);
    }
    element.setAttribute('role', 'menu');
    element.setAttribute('aria-label', 'Sookit 视频工具');
    element.addEventListener('keydown', event => {
      const items = [...element.children];
      const index = items.indexOf(document.activeElement);
      let next;
      if (event.key === 'ArrowDown') next = (index + 1) % items.length;
      else if (event.key === 'ArrowUp') next = (index + items.length - 1) % items.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = items.length - 1;
      else if ((event.key === 'Enter' || event.key === ' ') && index >= 0) {
        event.preventDefault();
        items[index].click();
        return;
      } else if (event.key === 'Tab') closeXMenu();
      if (next !== undefined) {
        event.preventDefault();
        items[next].focus();
      }
    });
    document.body.append(element);
    return element;
  }

  function toggleXMenu(trigger, lastItem = false) {
    if (busy || !trigger?.isConnected) return;
    if (xMenu && !xMenu.hidden && xMenuButton === trigger) {
      closeXMenu(true);
      return;
    }
    const article = trigger.closest('article[data-testid="tweet"]');
    const url = article && getXStatusUrl(article);
    if (!article || !tweetHasVideo(article) || !url) return;
    if (xMenu && !xMenu.hidden) closeXMenu();
    if (xMenu) xMenu.remove();
    xMenu = createXMenu();
    xMenuButton = trigger;
    xMenuArticle = article;
    xMenuUrl = url;
    trigger.dataset.xStatusUrl = url;
    xMenu.setAttribute('aria-labelledby', trigger.id);
    xMenu.hidden = false;
    trigger.setAttribute('aria-expanded', 'true');
    positionXMenu();
    if (!xMenu.hidden) xMenu.children[lastItem ? xActions.length - 1 : 0].focus();
  }

  function createXButton(grokButton, statusUrl) {
    const element = document.createElement('button');
    element.type = 'button';
    element.id = 'sookit-x-button-' + (++xButtonSequence);
    element.className = grokButton.className;
    element.classList.add('sookit-x-button');
    element.title = 'Sookit 视频工具';
    element.setAttribute('aria-label', 'Sookit 视频工具');
    element.setAttribute('aria-haspopup', 'menu');
    element.setAttribute('aria-controls', 'sookit-x-action-menu');
    element.setAttribute('aria-expanded', 'false');
    element.dataset.xStatusUrl = statusUrl;
    const rect = grokButton.getBoundingClientRect();
    const width = rect.width > 0 ? rect.width : 40;
    const height = rect.height > 0 ? rect.height : width;
    element.style.boxSizing = 'border-box';
    element.style.width = width + 'px';
    element.style.height = height + 'px';
    element.style.margin = '0 8px 0 0';
    element.style.padding = '0';
    element.style.border = '0';
    element.style.borderRadius = '9999px';
    element.style.color = 'inherit';
    element.style.display = 'inline-flex';
    element.style.alignItems = 'center';
    element.style.justifyContent = 'center';
    element.style.flex = '0 0 auto';
    element.style.cursor = 'pointer';
    const icon = document.createElement('img');
    icon.src = chrome.runtime.getURL('icons/sookit-48.png');
    icon.alt = '';
    icon.width = 20;
    icon.height = 20;
    icon.style.display = 'block';
    icon.style.width = '20px';
    icon.style.height = '20px';
    icon.style.objectFit = 'contain';
    icon.style.pointerEvents = 'none';
    element.append(icon);
    element.addEventListener('click', () => toggleXMenu(element));
    element.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        if (!xMenu || xMenu.hidden) toggleXMenu(element, event.key === 'ArrowUp');
      }
    });
    return element;
  }

  function removeXButtons() {
    closeXMenu();
    for (const element of document.querySelectorAll('button.sookit-x-button')) element.remove();
  }

  function mountXButtons() {
    if (xMenuButton && (!xMenuButton.isConnected || !xMenuArticle?.isConnected)) closeXMenu();
    for (const article of document.querySelectorAll('article[data-testid="tweet"]')) {
      const buttons = [...article.querySelectorAll('button.sookit-x-button')]
        .filter(element => element.closest('article[data-testid="tweet"]') === article);
      const statusUrl = getXStatusUrl(article);
      const grokButton = findGrokButton(article);
      if (!tweetHasVideo(article) || !statusUrl || !grokButton) {
        for (const element of buttons) {
          if (xMenuButton === element) closeXMenu();
          element.remove();
        }
        continue;
      }
      const element = buttons.shift() || createXButton(grokButton, statusUrl);
      for (const duplicate of buttons) {
        if (xMenuButton === duplicate) closeXMenu();
        duplicate.remove();
      }
      if (xMenuButton === element && xMenuUrl !== statusUrl) closeXMenu();
      element.className = grokButton.className;
      element.classList.add('sookit-x-button');
      const rect = grokButton.getBoundingClientRect();
      if (rect.width > 0) element.style.width = rect.width + 'px';
      if (rect.height > 0) element.style.height = rect.height + 'px';
      element.dataset.xStatusUrl = statusUrl;
      if (grokButton.previousElementSibling !== element) grokButton.before(element);
      element.disabled = busy;
    }
    for (const element of document.querySelectorAll('button.sookit-x-button')) {
      if (!element.closest('article[data-testid="tweet"]')) {
        if (xMenuButton === element) closeXMenu();
        element.remove();
      }
    }
  }

  function mount() {
    scheduled = false;
    const url = currentVideoUrl();
    if (url !== pageUrl) {
      pageGeneration += 1;
      pageUrl = url;
      closeMenu();
      if (feedback) feedback.hidden = true;
    }
    if (isXSite()) {
      if (location.href !== xPageUrl) {
        xPageUrl = location.href;
        closeXMenu();
        if (feedback) feedback.hidden = true;
      }
      closeMenu();
      button?.remove();
      if (button) delete button.dataset.shortVideoId;
      if (sites.x) {
        mountXButtons();
      } else {
        removeXButtons();
      }
      return;
    }
    if (xPageUrl !== location.href || xMenuButton || document.querySelector('button.sookit-x-button')) {
      xPageUrl = location.href;
      removeXButtons();
    }
    if (!sites.youtube) {
      closeMenu();
      button?.remove();
      if (button) delete button.dataset.shortVideoId;
      return;
    }
    if (!url) {
      closeMenu();
      button?.remove();
      if (button) delete button.dataset.shortVideoId;
      return;
    }
    const shorts = isShortsPage();
    const videoId = shorts ? new URL(url).searchParams.get('v') : null;
    if (!shorts && button) delete button.dataset.shortVideoId;
    const target = shorts ? findShortsActionBar(url)
      : document.querySelector('ytd-watch-metadata #actions #top-level-buttons-computed')
        || document.querySelector('ytd-watch-metadata #actions ytd-menu-renderer #top-level-buttons-computed')
        || document.querySelector('#info #menu-container #top-level-buttons-computed');
    if (!target) {
      closeMenu();
      button?.remove();
      return;
    }
    if (!button) button = createButton();
    button.setAttribute('data-short-form', String(shorts));
    if (shorts) button.dataset.shortVideoId = videoId;
    if (button.parentElement !== target || (shorts && target.firstElementChild !== button)) {
      closeMenu();
      if (shorts) target.prepend(button);
      else target.append(button);
    }
    button.disabled = busy;
  }

  function scheduleMount() {
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(mount);
    }
  }

  function relevantMutation(records) {
    if (isXSite()) {
      return records.some(record => {
        const target = record.target instanceof Element ? record.target : record.target.parentElement;
        if (target?.closest('#sookit-x-action-menu, #sookit-feedback, button.sookit-x-button')) return false;
        if (record.type === 'attributes'
            && !['data-testid', 'aria-label', 'href'].includes(record.attributeName)) return false;
        if (target?.closest('article[data-testid="tweet"]')) return true;
        return [...record.addedNodes, ...record.removedNodes].some(node => node instanceof Element
          && (node.matches('article[data-testid="tweet"]')
            || node.querySelector('article[data-testid="tweet"]')));
      });
    }
    if (!records.length || records.some(record => record.type !== 'attributes'
      || !['class', 'style'].includes(record.attributeName))) return true;
    return records.some(record => {
      const target = record.target;
      return Boolean(target.closest('ytd-reel-video-renderer')
        || target.querySelector('ytd-reel-video-renderer'));
    });
  }
  document.addEventListener('pointerdown', event => {
    if (menu && !menu.hidden && !menu.contains(event.target) && !button?.contains(event.target)) closeMenu();
    if (xMenu && !xMenu.hidden && !xMenu.contains(event.target) && !xMenuButton?.contains(event.target)) closeXMenu();
  }, true);
  document.addEventListener('focusin', event => {
    if (menu && !menu.hidden && !menu.contains(event.target) && !button?.contains(event.target)) closeMenu();
    if (xMenu && !xMenu.hidden && !xMenu.contains(event.target) && !xMenuButton?.contains(event.target)) closeXMenu();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && xMenu && !xMenu.hidden) {
      event.preventDefault();
      event.stopPropagation();
      closeXMenu(true);
      return;
    }
    if (event.key === 'Escape' && menu && !menu.hidden) {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
    }
  }, true);
  window.addEventListener('resize', () => { positionMenu(); positionXMenu(); scheduleMount(); });
  window.addEventListener('scroll', () => {
    positionMenu();
    positionXMenu();
    if (!isXSite()) scheduleMount();
  }, true);
  new MutationObserver(records => {
    if (relevantMutation(records)) scheduleMount();
  }).observe(document.documentElement, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ['is-active', 'video-id', 'href', 'class', 'style', 'data-testid', 'aria-label']
  });
  document.addEventListener('yt-navigate-start', () => {
    pageGeneration += 1;
    closeMenu();
    closeXMenu();
    if (feedback) feedback.hidden = true;
  });
  document.addEventListener('yt-navigate-finish', scheduleMount);
  window.addEventListener('popstate', scheduleMount);
  scheduleMount();
})();
