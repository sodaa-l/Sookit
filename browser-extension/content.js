(() => {
  'use strict';
  if (window.top !== window || document.getElementById('sookit-extension-marker')) return;
  const marker = document.createElement('meta');
  marker.id = 'sookit-extension-marker';
  document.head.append(marker);
  const actions = [
    {id: 'sniff', label: '嗅探视频', icon: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
      message: 'Sookit 已在后台开始嗅探，完成后会发送系统通知'},
    {id: 'download_best', label: '下载最佳画质', icon: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
      message: '最佳画质下载已加入 Sookit 任务队列'},
    {id: 'download_cover', label: '下载封面', icon: 'M3 3h18v18H3ZM3 17l6-6 4 4 3-3 5 5M8 7h.01',
      message: '最佳封面下载已加入 Sookit 任务队列'}
  ];
  let button;
  let menu;
  let menuUrl = '';
  let pageUrl = currentVideoUrl();
  let pageGeneration = 0;
  let feedback;
  let busy = false;
  let scheduled = false;
  let feedbackTimer;

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
    try {
      const response = await chrome.runtime.sendMessage({action: action.id, url});
      if (currentVideoUrl() !== url || generation !== pageGeneration) return;
      if (!response?.ok) throw new Error(response?.error || 'Sookit 未确认接收请求');
      tell(response.message || action.message);
    } catch (error) {
      if (currentVideoUrl() === url && generation === pageGeneration) {
        tell(error.message || '无法连接 Sookit，请刷新页面后重试', true);
      }
    } finally {
      busy = false;
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

  function mount() {
    scheduled = false;
    const url = currentVideoUrl();
    if (url !== pageUrl) {
      pageGeneration += 1;
      pageUrl = url;
      closeMenu();
      if (feedback) feedback.hidden = true;
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
    if (!records.length || records.some(record => record.type !== 'attributes'
      || !['class', 'style'].includes(record.attributeName))) return true;
    return records.some(record => {
      const target = record.target;
      return Boolean(target.closest('ytd-reel-video-renderer')
        || target.querySelector('ytd-reel-video-renderer'));
    });
  }
  document.addEventListener('pointerdown', event => {
    if (menu && !menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) closeMenu();
  }, true);
  document.addEventListener('focusin', event => {
    if (menu && !menu.hidden && !menu.contains(event.target) && !button.contains(event.target)) closeMenu();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu && !menu.hidden) {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
    }
  }, true);
  window.addEventListener('resize', () => { positionMenu(); scheduleMount(); });
  window.addEventListener('scroll', () => { positionMenu(); scheduleMount(); }, true);
  new MutationObserver(records => {
    if (relevantMutation(records)) scheduleMount();
  }).observe(document.documentElement, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ['is-active', 'video-id', 'href', 'class', 'style']
  });
  document.addEventListener('yt-navigate-start', () => {
    pageGeneration += 1;
    closeMenu();
    if (feedback) feedback.hidden = true;
  });
  document.addEventListener('yt-navigate-finish', scheduleMount);
  window.addEventListener('popstate', scheduleMount);
  scheduleMount();
})();
