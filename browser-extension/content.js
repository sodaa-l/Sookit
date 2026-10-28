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
      return;
    }
    // 新旧 YouTube 操作栏均支持；不抓播放器菜单或评论区的同名节点。
    const target = document.querySelector('ytd-watch-metadata #actions #top-level-buttons-computed')
      || document.querySelector('ytd-watch-metadata #actions ytd-menu-renderer #top-level-buttons-computed')
      || document.querySelector('#info #menu-container #top-level-buttons-computed');
    if (!target) {
      closeMenu();
      return;
    }
    if (!button) button = createButton();
    if (button.parentElement !== target) {
      closeMenu();
      target.append(button);
    }
    button.disabled = busy;
  }

  function scheduleMount() {
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(mount);
    }
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
  window.addEventListener('resize', positionMenu);
  window.addEventListener('scroll', positionMenu, true);
  new MutationObserver(scheduleMount).observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener('yt-navigate-start', () => {
    pageGeneration += 1;
    closeMenu();
    if (feedback) feedback.hidden = true;
  });
  document.addEventListener('yt-navigate-finish', scheduleMount);
  window.addEventListener('popstate', scheduleMount);
  scheduleMount();
})();
