/* 无第三方依赖：验证扩展脚本的消息来源、按钮交互和 YouTube SPA 导航。 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const extension = path.join(__dirname, '..', 'browser-extension');
const URL_A = 'https://www.youtube.com/watch?v=Yb9H5vhWyHg';
const URL_B = 'https://www.youtube.com/watch?v=aaaaaaaaaaa';
const SHORT_URL = 'https://www.youtube.com/shorts/Yb9H5vhWyHg';
const SHORT_URL_B = 'https://www.youtube.com/shorts/aaaaaaaaaaa';
const ID = 'inplhidhdgncdjkkmbfcocedkabbaghn';

function background(native = async () => ({ok: true}), getTab = async () => ({url: URL_A}), sites = null) {
  let receive;
  const calls = [];
  const tabQueries = [];
  const stored = sites ? {sookitSites: {...sites}} : {};
  const context = vm.createContext({URL, chrome: {
    tabs: {async get(id) { tabQueries.push(id); return getTab(id); }},
    storage: {local: {
      async get(key) { return key && key in stored ? {[key]: stored[key]} : {}; },
      async set(values) { Object.assign(stored, values); }
    }},
    runtime: {
      id: ID,
      getURL: file => `chrome-extension://${ID}/${file}`,
      onMessage: {addListener(listener) { receive = listener; }},
      async sendNativeMessage(host, request) { calls.push({host, request}); return native(request); }
    }
  }});
  vm.runInContext(fs.readFileSync(path.join(extension, 'background.js'), 'utf8'), context);
  const sender = {id: ID, frameId: 0, tab: {id: 1}, url: URL_A};
  const popupSender = {id: ID, url: `chrome-extension://${ID}/popup.html`, origin: `chrome-extension://${ID}`};
  return {calls, tabQueries, stored, sender, popupSender, send(message, source = sender) {
    return new Promise(resolve => {
      const pending = receive(message, source, resolve);
      if (!pending) resolve(undefined);
    });
  }};
}

test('后台只传递标准化的当前视频链接', async () => {
  const bridge = background();
  const result = await bridge.send({action: 'sniff', url: URL_A + '&list=PLtest&t=12', command: 'ignored'});
  assert.equal(result.ok, true);
  assert.equal(bridge.calls.length, 1);
  assert.equal(bridge.calls[0].host, 'com.sookit.browser');
  assert.equal(JSON.stringify(bridge.calls[0].request), JSON.stringify({action: 'sniff', url: URL_A}));
});

test('后台拒绝跨视频、跨扩展、子框架和非 YouTube 请求', async () => {
  const bridge = background();
  for (const [url, source] of [
    [URL_B, bridge.sender],
    [URL_A, {...bridge.sender, id: 'wrong'}],
    [URL_A, {...bridge.sender, frameId: 1}],
    [URL_A, {...bridge.sender, tab: undefined}],
    ['https://evil.test/watch?v=Yb9H5vhWyHg', bridge.sender],
    [URL_A, {...bridge.sender, url: 'https://evil.test/'}],
    [URL_A + '&v=aaaaaaaaaaa', bridge.sender]
  ]) {
    assert.equal((await bridge.send({action: 'sniff', url}, source)).ok, false);
  }
  assert.equal(bridge.calls.length, 0);
  assert.equal(await bridge.send({action: 'exec', url: URL_A}), undefined);
});

test('后台支持短链、Shorts 和直播的单视频标准化', async () => {
  const bridge = background();
  for (const url of ['https://youtu.be/Yb9H5vhWyHg',
    'https://www.youtube.com/shorts/Yb9H5vhWyHg', 'https://www.youtube.com/live/Yb9H5vhWyHg']) {
    assert.equal((await bridge.send({action: 'sniff', url}, {...bridge.sender, url})).ok, true);
    assert.equal(bridge.calls.at(-1).request.url, URL_A);
  }
});

test('站内导航后按当前标签页校验，不把内容脚本的旧地址当成当前视频', async () => {
  const current = 'https://www.youtube.com/watch?v=XFl4d3Ya9t4';
  for (const original of ['https://www.youtube.com/',
    'https://www.youtube.com/results?search_query=cover', URL_A]) {
    const bridge = background(undefined, async () => ({url: current + '&t=10'}));
    const sender = {...bridge.sender, url: original};
    for (const action of ['sniff', 'download_best', 'download_cover']) {
      const result = await bridge.send({action, url: current}, sender);
      assert.equal(result.ok, true, result.error);
      assert.equal(bridge.calls.at(-1).request.url, current);
    }
    assert.deepEqual(bridge.tabQueries, [1, 1, 1]);
  }
});

test('当前标签页已切换、正在导航、已关闭或无法读取时不发送本机请求', async () => {
  for (const getTab of [
    async () => ({url: URL_B}),
    async () => ({url: 'https://www.youtube.com/'}),
    async () => ({url: 'https://evil.test/'}),
    async () => ({url: URL_A, pendingUrl: URL_B}),
    async () => ({url: URL_A, pendingUrl: 'https://evil.test/'}),
    async () => ({}),
    async () => { throw new Error('No tab with id: 1.'); }
  ]) {
    const bridge = background(undefined, getTab);
    const result = await bridge.send({action: 'sniff', url: URL_A});
    assert.equal(result.ok, false);
    assert.match(result.error, /页面|视频/);
    assert.equal(bridge.calls.length, 0);
  }
});

test('未注册本机桥接时给出操作提示，忙碌响应原样返回', async () => {
  const missing = background(async () => { throw new Error('Specified native messaging host not found.'); });
  const failure = await missing.send({action: 'sniff', url: URL_A});
  assert.equal(failure.ok, false);
  assert.match(failure.error, /注册脚本/);
  const busy = background(async () => ({ok: false, error: 'Sookit 正在嗅探其他视频'}));
  assert.match((await busy.send({action: 'sniff', url: URL_A})).error, /正在嗅探/);
});

function page(sendMessage = async () => ({ok: true, message: '已在后台开始嗅探'}), href = URL_A, options = {}) {
  class Element {
    constructor(tag) {
      this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.events = {};
      this.style = {}; this.offsetWidth = 224; this.offsetHeight = 160; this.hidden = false;
    }
    get isConnected() { return this === root || Boolean(this.parentElement?.isConnected); }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    closest(selector) {
      for (let element = this; element; element = element.parentElement) {
        if (element.matches(selector)) return element;
      }
      return null;
    }
    get firstElementChild() { return this.children[0] || null; }
    getAttribute(key) { return this.attributes[key] ?? null; }
    hasAttribute(key) { return this.attributes[key] !== undefined; }
    matches(selector) {
      if (selector.startsWith('#')) return this.id === selector.slice(1);
      if (selector === "a[href*='/shorts/']") {
        return this.tagName === 'a' && String(this.getAttribute('href') || '').includes('/shorts/');
      }
      if (selector === "a.ytp-title-link[href*='/shorts/']") {
        return this.tagName === 'a' && String(this.className || '').split(/\s+/).includes('ytp-title-link')
          && String(this.getAttribute('href') || '').includes('/shorts/');
      }
      return this.tagName === selector;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
      const results = [];
      const visit = node => {
        for (const child of node.children) {
          if (child.matches(selector)) results.push(child);
          visit(child);
        }
      };
      visit(this);
      return results;
    }
    focus() { document.activeElement = this; }
    getBoundingClientRect() { return this.rect || rect; }
    append(...elements) {
      for (const child of elements) { child.remove(); this.children.push(child); child.parentElement = this; }
    }
    prepend(...elements) {
      for (const child of [...elements].reverse()) {
        child.remove(); this.children.unshift(child); child.parentElement = this;
      }
    }
    remove() {
      if (this.parentElement) {
        const siblings = this.parentElement.children;
        siblings.splice(siblings.indexOf(this), 1);
        this.parentElement = null;
      }
    }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(event, listener) { this.events[event] = listener; }
  }
  const rect = {top: 40, bottom: 700, left: 300, right: 620, width: 320, height: 660};
  const root = new Element('html');
  const head = new Element('head');
  const body = new Element('body');
  root.append(head, body);
  const documentEvents = {};
  const windowEvents = {};
  let target = new Element('actions');
  body.append(target);
  const reels = [];
  const attachActionBar = reel => {
    const actions = new Element(options.structure === 'legacy' ? 'div' : 'reel-action-bar-view-model');
    if (options.structure === 'legacy') actions.id = 'actions';
    const like = new Element('button'); like.id = 'like-button';
    actions.append(like); reel.renderer.append(actions);
    reel.actions = actions;
    reel.like = like;
  };
  const renderReels = url => {
    for (const reel of reels) reel.renderer.remove();
    reels.length = 0;
    if (!new URL(url).pathname.startsWith('/shorts/')) return;
    for (const [index, id] of ['Yb9H5vhWyHg', 'aaaaaaaaaaa'].entries()) {
      const renderer = new Element('ytd-reel-video-renderer');
      renderer.rect = options.rects?.[index] || rect;
      if (options.activeIndex === index || (options.activeIndex === undefined
        && options.structure === 'legacy' && index === 0)) renderer.setAttribute('is-active', '');
      if (index === options.hiddenRendererIndex) renderer.style.opacity = '0';
      if (index === options.semanticHiddenIndex) {
        renderer.setAttribute('aria-hidden', 'true'); renderer.setAttribute('inert', '');
      }
      const reel = {renderer, actions: null, like: null, id};
      if (!options.deferIdentity) {
        if (options.identityByLink) {
          const titleLink = new Element('a');
          titleLink.className = 'ytp-title-link';
          titleLink.setAttribute('href', '/shorts/' + id);
          renderer.append(titleLink);
        } else if (!options.identityless && options.identitylessIndex !== index) renderer.setAttribute('video-id', id);
      }
      if (!options.deferActionBars) attachActionBar(reel);
      if (index === options.hiddenActionBarIndex) reel.actions.style.visibility = 'hidden';
      body.append(renderer);
      reels.push(reel);
    }
  };
  renderReels(href);
  const walk = node => [node, ...node.children.flatMap(walk)];
  const document = {documentElement: root, head, body,
    createElement: tag => new Element(tag),
    createElementNS: (_ns, tag) => new Element(tag),
    getElementById: id => walk(root).find(node => node.id === id),
    title: 'Sookit 测试视频 - YouTube',
    querySelector: selector => {
      if (selector === 'ytd-reel-video-renderer[is-active]') {
        return reels.find(reel => reel.renderer.hasAttribute('is-active'))?.renderer || null;
      }
      // 内容脚本读取标题/频道/时长用于浏览器下载任务；真实页面有这些节点，假页面也要给全。
      if (selector === 'meta[name="title"]') return {content: 'Sookit 测试视频'};
      if (selector === '[itemprop="author"] [itemprop="name"]') {
        return {getAttribute: () => 'Sookit 频道', textContent: 'Sookit 频道'};
      }
      if (selector === '[itemprop="duration"]') return {getAttribute: () => 'PT1M5S', textContent: 'PT1M5S'};
      return target;
    },
    querySelectorAll: selector => root.querySelectorAll(selector),
    addEventListener: (name, listener) => { documentEvents[name] = listener; }
  };
  const location = {href};
  // 真实页面里 location.hostname 是派生属性；这里也要派生，否则站点判定无从下手。
  Object.defineProperty(location, 'hostname', {
    get() { try { return new URL(String(this.href)).hostname; } catch { return ''; } }
  });
  const window = {innerWidth: 1024, innerHeight: 768,
    addEventListener(name, listener) { windowEvents[name] = listener; }};
  window.top = window;
  const frames = [];
  const observers = [];
  const observerConfigs = [];
  const storageListeners = [];
  const timers = new Map();
  let timerId = 0;
  const requests = [];
  const context = vm.createContext({document, window, location, URL,
    requestAnimationFrame: fn => frames.push(fn),
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
    getComputedStyle: element => {
      let visibility = 'visible';
      for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
        if (ancestor.style.visibility) { visibility = ancestor.style.visibility; break; }
      }
      return {display: element.style.display || '', visibility, opacity: element.style.opacity ?? '1'};
    },
    MutationObserver: class {
      constructor(fn) { observers.push(fn); }
      observe(_target, config) { observerConfigs.push(config); }
    },
    chrome: {runtime: {getURL: file => `chrome-extension://${ID}/${file}`,
      sendMessage(message) { requests.push(message); return sendMessage(message); }},
      storage: options.sites ? {
        local: {get(_key, callback) { callback({sookitSites: {...options.sites}}); }},
        onChanged: {addListener(listener) { storageListeners.push(listener); }}
      } : undefined}
  });
  const source = fs.readFileSync(path.join(extension, 'content.js'), 'utf8');
  vm.runInContext(source, context);
  const flush = () => { for (const fn of frames.splice(0)) fn(); };
  flush();
  const tick = () => {
    const entries = [...timers.entries()];
    timers.clear();
    for (const [, callback] of entries) callback();
  };
  return {document, requests, flush, frames, rect, window, reels, observerConfigs, timers, tick,
    activateReel(index, url) {
      if (options.structure === 'legacy') {
        for (const reel of reels) reel.renderer.removeAttribute('is-active');
        reels[index].renderer.setAttribute('is-active', '');
      }
      location.href = url;
      this.windowEvent('scroll'); this.mutate(); this.flush();
    },
    hydrateVideoId(index, id = reels[index].id) {
      reels[index].renderer.setAttribute('video-id', id);
      this.mutate(); this.flush();
    },
    hydrateCanonicalLink(index, id = reels[index].id) {
      const link = new Element('a'); link.className = 'ytp-title-link';
      link.setAttribute('href', '/shorts/' + id); reels[index].renderer.append(link);
      this.mutate(); this.flush();
    },
    setActiveReel(index) {
      for (const reel of reels) reel.renderer.removeAttribute('is-active');
      reels[index].renderer.setAttribute('is-active', '');
      this.mutate(); this.flush();
    },
    addActionBar(index) { attachActionBar(reels[index]); this.mutate(); this.flush(); },
    setReelRect(index, bounds) { reels[index].renderer.rect = bounds; this.windowEvent('scroll'); this.flush(); },
    documentEvent(name, event) { documentEvents[name]?.(event); },
    windowEvent(name) { windowEvents[name]?.(); },
    get menu() { return document.getElementById('sookit-action-menu'); },
    // 内容脚本用 setTimeout 轮询视频元数据；这里边等边推进假定时器，避免请求永远发不出去。
    async choose(action = 'sniff') {
      if (!this.menu || this.menu.hidden) this.button.events.click();
      const pending = this.menu.children.find(item => item.dataset.action === action).events.click();
      let settled = false;
      const done = () => { settled = true; };
      Promise.resolve(pending).then(done, done);
      for (let step = 0; step < 40 && !settled && timers.size; step += 1) {
        const [id, callback] = timers.entries().next().value;
        timers.delete(id);
        callback();
        await new Promise(resolve => setImmediate(resolve));
      }
      return pending;
    },
    get button() { return document.getElementById('sookit-sniff-button'); },
    get feedback() { return document.getElementById('sookit-feedback'); },
    get feedbackMessage() { return this.feedback?.children[1]?.textContent; },
    mutate(target, attributeName) {
      const records = target ? [{type: 'attributes', target, attributeName}]
        : [{type: 'childList', target: body}];
      observers.forEach(fn => fn(records));
    },
    reinject() { vm.runInContext(source, context); flush(); },
    navigate(url) { location.href = url; renderReels(url); documentEvents['yt-navigate-finish'](); flush(); },
    popstate(url) { location.href = url; renderReels(url); windowEvents.popstate(); flush(); },
    replaceActions() { target.remove(); target = new Element('actions'); body.append(target); this.mutate(); flush(); },
    // 模拟面板写入站点开关后触发的 storage.onChanged。
    setSites(sites) {
      Object.assign(options.sites, sites);
      for (const listener of storageListeners) {
        listener({sookitSites: {newValue: {...options.sites}}}, 'local');
      }
      flush();
    }
  };
}

test('视频操作栏只生成一个图标加 Sookit 文字的按钮', () => {
  const site = page();
  const button = site.button;
  assert.ok(button);
  assert.equal(button.children[0].tagName, 'img');
  assert.match(button.children[0].src, /icons\/sookit-48.png$/);
  assert.equal(button.children[1].textContent, 'Sookit');
  site.reinject();
  site.mutate(); site.mutate();
  assert.equal(site.frames.length, 1);
  site.flush();
  assert.equal(site.button, button);
  site.replaceActions();
  assert.equal(site.button, button);
  assert.equal(button.parentElement.tagName, 'actions');
});

test('现代 Shorts 操作栏按视频身份切换，传递当前视频并清理旧状态', async () => {
  let finishFirst;
  const site = page(() => site.requests.length === 1
    ? new Promise(resolve => { finishFirst = resolve; })
    : Promise.resolve({ok: true}), SHORT_URL);
  const button = site.button;
  const [first, second] = site.reels;
  assert.ok(button);
  assert.equal(button.parentElement, first.actions);
  assert.equal(first.actions.firstElementChild, button);
  assert.equal(first.actions.children[1], first.like);
  assert.equal(second.actions.children.some(child => child.id === 'sookit-sniff-button'), false);
  assert.equal(first.renderer.hasAttribute('is-active'), false);
  assert.equal(second.renderer.hasAttribute('is-active'), false);
  assert.equal(first.actions.tagName, 'reel-action-bar-view-model');

  const oldRequest = site.choose('sniff');
  assert.equal(site.requests[0].url, URL_A);
  site.activateReel(1, SHORT_URL_B);
  assert.equal(site.button, button);
  assert.equal(button.parentElement, second.actions);
  assert.equal(second.actions.firstElementChild, button);
  assert.equal(first.actions.children.includes(button), false);
  assert.equal([...site.reels.flatMap(reel => reel.actions.children)]
    .filter(child => child.id === 'sookit-sniff-button').length, 1);

  finishFirst({ok: true, message: '旧视频的回执'});
  await oldRequest;
  // 旧视频的回执被丢弃：等待中的小窗也跟着收走，不能留在页面上。
  assert.equal(site.feedback.hidden, true);
  await site.choose('sniff');
  assert.deepEqual(site.requests.map(request => request.url), [URL_A, URL_B]);
  site.navigate(URL_A);
  assert.equal(site.button.parentElement, site.document.body.children[0]);
  assert.equal(site.button.attributes['data-short-form'], 'false');
  site.popstate(SHORT_URL);
  assert.equal(site.button.parentElement, site.reels[0].actions);
  assert.equal(site.button.attributes['data-short-form'], 'true');
  site.popstate(URL_A);
  assert.equal(site.button.parentElement, site.document.body.children[0]);
  site.navigate('https://www.youtube.com/');
  assert.equal(site.button, undefined);
});

test('现代 Shorts 用规范播放器链接识别多个预加载 reel', () => {
  const site = page(undefined, SHORT_URL_B, {identityByLink: true});
  assert.ok(site.button);
  assert.equal(site.button.parentElement, site.reels[1].actions);
  assert.equal(site.button.parentElement.firstElementChild, site.button);
  assert.ok(site.reels.every(reel => !reel.renderer.hasAttribute('is-active')));
});

test('视频身份晚水合时等待唯一匹配，不误挂到预加载项目', () => {
  const site = page(undefined, SHORT_URL, {deferIdentity: true});
  assert.equal(site.button, undefined);
  site.hydrateVideoId(0);
  assert.ok(site.button);
  assert.equal(site.button.parentElement, site.reels[0].actions);
});

test('现代 Shorts 操作栏晚加载时补挂按钮', () => {
  const site = page(undefined, SHORT_URL, {deferActionBars: true});
  assert.equal(site.button, undefined);
  site.addActionBar(0);
  assert.ok(site.button);
  assert.equal(site.button.parentElement, site.reels[0].actions);
});

test('身份不明的 reel 不覆盖已识别的预加载视频', () => {
  const site = page(undefined, SHORT_URL, {identitylessIndex: 0});
  assert.equal(site.button, undefined);
  site.setActiveReel(0);
  assert.ok(site.button);
  assert.equal(site.button.parentElement, site.reels[0].actions);
});

test('身份缺失且操作栏延迟时只在唯一 active 项出现后挂载', () => {
  const site = page(undefined, SHORT_URL, {deferIdentity: true, deferActionBars: true});
  assert.equal(site.button, undefined);
  site.addActionBar(1);
  assert.equal(site.button, undefined);
  site.setActiveReel(1);
  assert.ok(site.button);
  assert.equal(site.button.parentElement, site.reels[1].actions);
});

test('旧 renderer 的唯一 is-active 与 #actions 仍兼容', () => {
  const site = page(undefined, SHORT_URL, {structure: 'legacy'});
  assert.ok(site.button);
  assert.equal(site.button.parentElement, site.reels[0].actions);
  assert.equal(site.button.parentElement.firstElementChild, site.button);
});

test('CSS 隐藏的 Shorts renderer 和现代操作栏跳过匹配，恢复后挂到爱心前', () => {
  const hiddenRenderer = page(undefined, SHORT_URL, {hiddenRendererIndex: 0});
  assert.equal(hiddenRenderer.button, undefined);
  const renderer = hiddenRenderer.reels[0].renderer;
  renderer.style.opacity = '1';
  hiddenRenderer.mutate(renderer, 'style');
  assert.equal(hiddenRenderer.frames.length, 1);
  hiddenRenderer.flush();
  assert.equal(hiddenRenderer.button.parentElement, hiddenRenderer.reels[0].actions);
  assert.equal(hiddenRenderer.reels[0].actions.firstElementChild, hiddenRenderer.button);
  assert.equal(hiddenRenderer.reels[0].actions.children[1], hiddenRenderer.reels[0].like);

  const hiddenActionBar = page(undefined, SHORT_URL, {hiddenActionBarIndex: 0});
  assert.equal(hiddenActionBar.button, undefined);
  const actionBar = hiddenActionBar.reels[0].actions;
  actionBar.style.visibility = 'visible';
  hiddenActionBar.mutate(actionBar, 'style');
  hiddenActionBar.flush();
  assert.equal(hiddenActionBar.button.parentElement, actionBar);
  assert.equal(actionBar.firstElementChild, hiddenActionBar.button);
  assert.equal(actionBar.children[1], hiddenActionBar.reels[0].like);
});

test('检查 ancestor 可见性但不把 aria-hidden 或 inert 当作视觉隐藏', () => {
  const site = page(undefined, SHORT_URL, {semanticHiddenIndex: 0});
  assert.ok(site.button);
  assert.equal(site.button.parentElement, site.reels[0].actions);
  assert.ok(site.observerConfigs[0].attributeFilter.includes('class'));
  assert.ok(site.observerConfigs[0].attributeFilter.includes('style'));

  const unrelated = site.document.createElement('div');
  site.document.body.append(unrelated);
  site.mutate(unrelated, 'class');
  assert.equal(site.frames.length, 0);

  site.document.body.style.visibility = 'hidden';
  site.mutate(site.document.body, 'style');
  site.flush();
  assert.equal(site.button, undefined);
  site.document.body.style.visibility = 'visible';
  site.mutate(site.document.body, 'class');
  site.flush();
  assert.equal(site.button.parentElement, site.reels[0].actions);
});

test('滚出视口时移除旧项按钮，切到可见视频后重新定位', () => {
  const site = page(undefined, SHORT_URL);
  const button = site.button;
  site.setReelRect(0, {top: 800, bottom: 1400, left: 300, right: 620, width: 320, height: 600});
  assert.equal(site.button, undefined);
  site.activateReel(1, SHORT_URL_B);
  assert.equal(site.button, button);
  assert.equal(button.parentElement, site.reels[1].actions);
});

test('点击只展开三项菜单，选择后发送且等待时不重复提交', async () => {
  let finish;
  const site = page(() => new Promise(resolve => { finish = resolve; }));
  const button = site.button;
  button.events.click();
  assert.equal(site.requests.length, 0);
  assert.equal(site.menu.hidden, false);
  assert.equal(button.attributes['aria-expanded'], 'true');
  assert.deepEqual(site.menu.children.map(item => item.children[1].textContent),
    ['嗅探视频', '下载最佳画质', '下载最佳封面']);
  assert.equal(site.document.activeElement, site.menu.children[0]);
  const first = site.choose();
  await site.choose('download_best');
  assert.equal(site.menu.hidden, true);
  assert.equal(button.disabled, true);
  assert.equal(site.requests.length, 1);
  assert.equal(site.requests[0].url, URL_A);
  finish({ok: true, message: '后台嗅探已启动'});
  await first;
  assert.equal(button.disabled, false);
  assert.equal(site.feedbackMessage, '后台嗅探已启动');
  assert.equal(site.feedback.dataset.state, 'success');
});

test('三种菜单选项分别传递动作和当前视频，不提前嗅探', async () => {
  const site = page(async () => ({ok: true}));
  for (const action of ['sniff', 'download_best', 'download_cover']) {
    await site.choose(action);
    assert.equal(site.requests.at(-1).action, action);
    assert.equal(site.requests.at(-1).url, URL_A);
  }
  assert.equal(site.requests.length, 3);
  assert.match(site.feedbackMessage, /封面下载/);
});

test('YouTube 站内导航关闭菜单，使用新链接并在离开视频页时移除按钮', async () => {
  const site = page();
  site.button.events.click();
  site.navigate(URL_B + '&t=10');
  assert.equal(site.menu.hidden, true);
  await site.choose('download_cover');
  assert.equal(site.requests[0].url, URL_B);
  site.navigate('https://www.youtube.com/results?search_query=Sookit');
  assert.equal(site.button, undefined);
  assert.equal(site.feedback.hidden, true);
  assert.equal(site.menu.hidden, true);
  site.navigate(URL_A);
  assert.ok(site.button);
  assert.equal(page(undefined, 'https://www.youtube.com/').button, undefined);
});

test('从首页进入视频再切换视频，页面菜单与后台按当前链接联动', async () => {
  let current = 'https://www.youtube.com/';
  const bridge = background(undefined, async () => ({url: current}));
  const sender = {...bridge.sender, url: current};
  const site = page(message => bridge.send(message, sender), current);
  assert.equal(site.button, undefined);
  for (const url of ['https://www.youtube.com/watch?v=XFl4d3Ya9t4', URL_A]) {
    current = url;
    site.navigate(url);
    for (const action of ['sniff', 'download_best', 'download_cover']) {
      await site.choose(action);
      assert.equal(site.feedback.dataset.state, 'success');
      assert.equal(bridge.calls.at(-1).request.url, url);
      assert.equal(bridge.calls.at(-1).request.action, action);
    }
  }
  assert.equal(bridge.calls.length, 6);
});

test('切换视频后抛弃旧回执，返回同一视频也不会显示过期消息', async () => {
  for (const returnToOriginal of [false, true]) {
    let finish;
    const site = page(() => new Promise(resolve => { finish = resolve; }));
    const sent = site.choose();
    site.navigate(URL_B);
    if (returnToOriginal) site.navigate(URL_A);
    finish({ok: true, message: '旧视频的回执'});
    await sent;
    assert.equal(site.feedback.hidden, true);
    assert.equal(site.button.disabled, false);
  }
});

test('连接失败显示错误，恢复按钮并允许重试', async () => {
  const site = page(async () => { throw new Error('本机桥接未注册'); });
  await site.choose();
  assert.equal(site.feedback.dataset.state, 'error');
  assert.match(site.feedbackMessage, /未注册/);
  assert.equal(site.button.disabled, false);
  await site.choose('download_best');
  assert.equal(site.requests.length, 2);
});

test('再次点击、点外部、Esc、焦点离开和操作栏重建都关闭菜单', () => {
  const site = page();
  const open = () => site.button.events.click();
  open(); open();
  assert.equal(site.menu.hidden, true);
  open();
  site.documentEvent('pointerdown', {target: site.menu.children[1]});
  assert.equal(site.menu.hidden, false);
  site.documentEvent('pointerdown', {target: site.document.body});
  assert.equal(site.menu.hidden, true);
  open();
  let prevented = false;
  site.documentEvent('keydown', {key: 'Escape', preventDefault() { prevented = true; }, stopPropagation() {}});
  assert.equal(prevented, true);
  assert.equal(site.document.activeElement, site.button);
  assert.equal(site.menu.hidden, true);
  open();
  site.documentEvent('focusin', {target: site.document.body});
  assert.equal(site.menu.hidden, true);
  open(); site.replaceActions();
  assert.equal(site.menu.hidden, true);
  assert.equal(site.requests.length, 0);
});

test('菜单支持上下键、首尾键和 Tab，定位不会超出视口', () => {
  const site = page();
  const key = key => ({key, preventDefault() {}, stopPropagation() {}});
  site.button.events.keydown(key('ArrowUp'));
  assert.equal(site.document.activeElement, site.menu.children[2]);
  site.menu.events.keydown(key('ArrowDown'));
  assert.equal(site.document.activeElement, site.menu.children[0]);
  site.menu.events.keydown(key('End'));
  assert.equal(site.document.activeElement, site.menu.children[2]);
  site.menu.events.keydown(key('Home'));
  assert.equal(site.document.activeElement, site.menu.children[0]);
  site.rect.right = 40;
  site.rect.top = 700;
  site.rect.bottom = 740;
  site.windowEvent('resize');
  assert.equal(site.menu.style.left, '8px');
  assert.equal(site.menu.style.top, '532px');
  site.menu.events.keydown(key('Tab'));
  assert.equal(site.menu.hidden, true);
  assert.equal(site.requests.length, 0);
});

test('后台的两种下载复用来源验证且只转发动作和链接', async () => {
  const bridge = background();
  for (const action of ['download_best', 'download_cover']) {
    assert.equal((await bridge.send({action, url: URL_A, output_dir: 'ignored'})).ok, true);
    assert.equal(JSON.stringify(bridge.calls.at(-1).request), JSON.stringify({action, url: URL_A}));
    assert.equal((await bridge.send({action, url: URL_B})).ok, false);
    assert.equal((await bridge.send({action, url: URL_A}, {...bridge.sender, frameId: 1})).ok, false);
  }
  assert.equal(bridge.calls.length, 2);
});

test('清单声明工具栏面板、存储权限与面板资源', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, 'manifest.json'), 'utf8'));
  assert.equal(manifest.action?.default_popup, 'popup.html');
  assert.ok(manifest.permissions.includes('storage'));
  assert.ok(manifest.permissions.includes('nativeMessaging'));
  for (const file of ['popup.html', 'popup.js', 'popup.css']) {
    assert.ok(fs.statSync(path.join(extension, file)).isFile(), file);
  }
});

test('面板消息只认扩展自己的 popup 来源，页面脚本冒充会被拒绝', async () => {
  const bridge = background(async request => ({ok: true, enabled: true, theme_color: '#3A9B8A', dark: false}));
  const status = await bridge.send({type: 'sookit.panel', op: 'status'}, bridge.popupSender);
  assert.equal(status.ok, true);
  assert.equal(bridge.calls.at(-1).request.action, 'status');
  const spoofed = await bridge.send({type: 'sookit.panel', op: 'launch'}, bridge.sender);
  assert.equal(spoofed.ok, false);
  assert.match(spoofed.error, /来源无效/);
  const contentScript = {
    id: ID, frameId: 0, url: URL_A, origin: 'https://www.youtube.com',
    tab: {id: 1}
  };
  const fromPage = await bridge.send({type: 'sookit.panel', op: 'launch'}, contentScript);
  assert.equal(fromPage.ok, false);
  assert.match(fromPage.error, /来源无效/);
  assert.equal(bridge.calls.length, 1);
});

test('面板拒绝未知控制动作，launch 按用户点击原样转发', async () => {
  const bridge = background(async () => ({ok: true, started: true}));
  const unknown = await bridge.send({type: 'sookit.panel', op: 'sniff'}, bridge.popupSender);
  assert.equal(unknown.ok, false);
  assert.match(unknown.error, /不支持的面板操作/);
  assert.equal(bridge.calls.length, 0);
  const launched = await bridge.send({type: 'sookit.panel', op: 'launch'}, bridge.popupSender);
  assert.equal(launched.ok, true);
  assert.equal(bridge.calls.at(-1).request.action, 'launch');
});

test('站点开关关闭后同站点请求在后台被拒，不调用本机桥接', async () => {
  const closed = background(async () => ({ok: true}), async () => ({url: URL_A}),
    {youtube: false, x: true});
  const rejected = await closed.send({action: 'sniff', url: URL_A});
  assert.equal(rejected.ok, false);
  assert.match(rejected.error, /扩展设置中关闭/);
  assert.equal(closed.calls.length, 0);
  const open = background(async () => ({ok: true}), async () => ({url: URL_A}),
    {youtube: true, x: false});
  assert.equal((await open.send({action: 'sniff', url: URL_A})).ok, true);
});

test('站点开关关闭时不注入按钮，改回开启后立即补挂', () => {
  const site = page(undefined, URL_A, {sites: {youtube: false, x: true}});
  assert.equal(site.button, undefined);
  site.setSites({youtube: true});
  assert.ok(site.button);
  site.setSites({youtube: false});
  assert.equal(site.button, undefined);
});

function popupView(response, sites = null, options = {}) {
  const elements = new Map();
  const messages = [];
  const pending = [];
  const accent = {};
  const documentElement = {dataset: {}, style: {setProperty(name, value) { accent[name] = value; }}};
  const stored = sites ? {sookitSites: {...sites}} : {};
  if (options.theme) stored.sookitTheme = {...options.theme};
  for (const id of ['status', 'status-text', 'toggle-youtube', 'toggle-x', 'master-value', 'primary']) {
    elements.set(id, {id, dataset: {}, checked: true, disabled: false, textContent: '',
      listeners: {}, addEventListener(name, listener) { this.listeners[name] = listener; }});
  }
  const body = {dataset: {}};
  const context = vm.createContext({
    document: {documentElement, body, getElementById: id => elements.get(id) || null},
    chrome: {
      storage: {local: {
        async get(key) { return key in stored ? {[key]: stored[key]} : {}; },
        async set(values) { Object.assign(stored, values); }
      }},
      runtime: {lastError: null, sendMessage(message, reply) {
        messages.push(message);
        if (options.holdStatus && message.op === 'status') return;
        if (options.holdLaunch && message.op === 'launch') {
          pending.push(reply);
          return;
        }
        reply(response);
      }}
    }
  });
  vm.runInContext(fs.readFileSync(path.join(extension, 'popup.js'), 'utf8'), context);
  return {elements, messages, stored, documentElement, accent, body,
    replyLaunch(reply) { pending.shift()(reply); }};
}

const settle = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };

test('面板已连接时同步主题，主按钮恒为启动 Sookit', async () => {
  const view = popupView({ok: true, enabled: true, theme_color: '#0098ff', dark: true});
  await settle();
  assert.equal(view.messages.length, 1);
  assert.equal(view.messages[0].type, 'sookit.panel');
  assert.equal(view.messages[0].op, 'status');
  assert.equal(view.elements.get('status').dataset.state, 'ok');
  assert.equal(view.elements.get('status-text').textContent, '已连接');
  assert.equal(view.elements.get('master-value').textContent, '已开启');
  assert.equal(view.elements.get('primary').textContent, '启动 Sookit');
  assert.equal(view.elements.get('primary').disabled, false);
  assert.equal(view.documentElement.dataset.theme, 'dark');
  assert.equal(view.accent['--accent'], '#0098ff');
});

test('总闸关闭时显示已暂停，主按钮文案不变', async () => {
  const view = popupView({ok: true, enabled: false, theme_color: '#3A9B8A', dark: false});
  await settle();
  assert.equal(view.elements.get('status').dataset.state, 'paused');
  assert.equal(view.elements.get('status-text').textContent, '已暂停');
  assert.equal(view.elements.get('master-value').textContent, '已关闭');
  assert.equal(view.elements.get('primary').textContent, '启动 Sookit');
});

test('桌面端没开时总闸仍由 host 读配置给出，主按钮照常可点', async () => {
  const desktopDown = popupView({
    ok: false, code: 'offline', error: 'Sookit 未运行', enabled: true, theme_color: '#0098ff',
  });
  await settle();
  assert.equal(desktopDown.elements.get('status').dataset.state, 'ok');
  assert.equal(desktopDown.elements.get('status-text').textContent, '已连接');
  assert.equal(desktopDown.elements.get('master-value').textContent, '已开启');
  assert.equal(desktopDown.elements.get('primary').textContent, '启动 Sookit');
  assert.equal(desktopDown.elements.get('primary').disabled, false);
  assert.equal(desktopDown.accent['--accent'], '#0098ff');

  const paused = popupView({ok: false, code: 'offline', error: 'Sookit 未运行', enabled: false});
  await settle();
  assert.equal(paused.elements.get('status').dataset.state, 'paused');
  assert.equal(paused.elements.get('master-value').textContent, '已关闭');

  const unknown = popupView({ok: false, code: 'offline', error: 'Sookit 未运行', enabled: null});
  await settle();
  assert.equal(unknown.elements.get('status').dataset.state, 'ok');
  assert.equal(unknown.elements.get('master-value').textContent, '未知');
});

test('桥接未注册时才算未连接，且主按钮不可点', async () => {
  const missing = popupView({ok: false, error: '未连接到 Sookit，请安装新版 Sookit 或运行浏览器桥接注册脚本'});
  await settle();
  assert.equal(missing.elements.get('status').dataset.state, 'offline');
  assert.equal(missing.elements.get('status-text').textContent, '未连接');
  assert.equal(missing.elements.get('master-value').textContent, '未知');
  assert.equal(missing.elements.get('primary').textContent, '启动 Sookit');
  assert.equal(missing.elements.get('primary').disabled, true);
});

test('拉起 Sookit 时标题栏转圈，主按钮文字保持不变', async () => {
  const view = popupView(
    {ok: false, code: 'offline', error: 'Sookit 未运行', enabled: true}, null, {holdLaunch: true});
  await settle();
  assert.equal(view.elements.get('status-text').textContent, '已连接');
  view.elements.get('primary').listeners.click();
  await settle();
  assert.equal(view.elements.get('status').dataset.state, 'busy');
  assert.equal(view.elements.get('status-text').textContent, '正在启动…');
  assert.equal(view.elements.get('primary').textContent, '启动 Sookit');
  assert.equal(view.elements.get('primary').disabled, true);
  view.replyLaunch({ok: false, error: 'Sookit 连接失败：boom'});
  await settle();
  assert.equal(view.elements.get('status').dataset.state, 'offline');
  assert.equal(view.elements.get('status-text').textContent, '启动失败');
  assert.equal(view.elements.get('primary').disabled, false);
});

test('面板打开先用上次记住的主题，不等状态回执', async () => {
  const view = popupView({ok: true, enabled: true, theme_color: '#0098ff', dark: true},
    null, {holdStatus: true, theme: {color: '#0098ff', dark: true}});
  await settle();
  assert.equal(view.accent['--accent'], '#0098ff');
  assert.equal(view.documentElement.dataset.theme, 'dark');
  assert.equal(view.body.dataset.ready, 'true');
  assert.equal(view.elements.get('status-text').textContent, '连接中…');
});

test('状态回执到达后记住主题，下次打开直接用', async () => {
  const view = popupView({ok: true, enabled: true, theme_color: '#0098ff', dark: true});
  await settle();
  assert.equal(view.stored.sookitTheme.color, '#0098ff');
  assert.equal(view.stored.sookitTheme.dark, true);
});

test('主题读到之前不画内容，避免先默认色再突然变色', () => {
  const css = fs.readFileSync(path.join(extension, 'popup.css'), 'utf8');
  assert.match(css, /body \{[^}]*opacity: 0;/);
  assert.match(css, /body\[data-ready='true'\] \{\s*opacity: 1;\s*\}/);
});

test('标题栏带转圈占位符，供进行中的操作显示', () => {
  const html = fs.readFileSync(path.join(extension, 'popup.html'), 'utf8');
  assert.match(html, /class="spinner"/);
});

test('指令发出立即在页面右上角弹出带转圈和关闭按钮的小窗', () => {
  let finish;
  const site = page(() => new Promise(resolve => { finish = resolve; }));
  site.button.events.click();
  const item = site.menu.children.find(child => child.dataset.action === 'sniff');
  const pending = item.events.click();
  const feedback = site.feedback;
  assert.ok(feedback);
  assert.equal(feedback.hidden, false);
  assert.equal(feedback.dataset.state, 'loading');
  assert.equal(feedback.children[0].className, 'sookit-feedback-spinner');
  assert.equal(feedback.children[1].textContent, '正在嗅探视频…');
  assert.equal(feedback.children[2].className, 'sookit-feedback-close');
  finish({ok: true, message: '已在后台开始嗅探'});
  return pending;
});

test('成功提示两秒后自动收起', async () => {
  const site = page();
  await site.choose('sniff');
  const feedback = site.feedback;
  assert.equal(feedback.dataset.state, 'success');
  assert.equal(feedback.children[1].textContent, '已在后台开始嗅探');
  assert.equal(feedback.hidden, false);
  site.tick();
  assert.equal(feedback.hidden, true);
});

test('失败提示常驻，点关闭按钮才收起', async () => {
  const site = page(async () => ({ok: false, error: '该站点的 Sookit 按钮已在扩展设置中关闭'}));
  await site.choose('sniff');
  const feedback = site.feedback;
  assert.equal(feedback.dataset.state, 'error');
  assert.equal(feedback.children[1].textContent, '该站点的 Sookit 按钮已在扩展设置中关闭');
  site.tick();
  assert.equal(feedback.hidden, false);
  feedback.children[2].events.click();
  assert.equal(feedback.hidden, true);
});

test('小窗样式对齐 Sookit：右上角、转圈、关闭按钮', () => {
  const css = fs.readFileSync(path.join(extension, 'content.css'), 'utf8');
  const block = css.slice(css.indexOf('#sookit-feedback {'));
  assert.match(block, /position: fixed;/);
  assert.match(block, /top: 16px;/);
  assert.match(block, /right: 16px;/);
  assert.match(block, /border-radius: 10px;/);
  assert.match(block, /background: #ffffff/);
  assert.match(block, /min-width: min\(300px/);
  assert.match(block, /font: 15px\/1.5/);
  assert.match(css, /\.sookit-feedback-spinner \{[^}]*animation: sookit-feedback-spin/);
  assert.match(css, /#sookit-feedback\[data-state='loading'\] \.sookit-feedback-spinner \{ display: block; \}/);
  assert.match(css, /\.sookit-feedback-close \{/);
  const script = fs.readFileSync(path.join(extension, 'content.js'), 'utf8');
  assert.match(script, /tell\(action\.pending \|\| '正在发送到 Sookit…', 'loading'\);/);
});

test('面板不再有底部小字提示', () => {
  const html = fs.readFileSync(path.join(extension, 'popup.html'), 'utf8');
  assert.doesNotMatch(html, /id="hint"/);
});

test('面板站点开关读写扩展本地存储', async () => {
  const view = popupView({ok: true, enabled: true, theme_color: '#3A9B8A', dark: false},
    {youtube: true, x: false});
  await settle();
  assert.equal(view.elements.get('toggle-youtube').checked, true);
  assert.equal(view.elements.get('toggle-x').checked, false);
  view.elements.get('toggle-x').checked = true;
  view.elements.get('toggle-x').listeners.change();
  await settle();
  assert.equal(view.stored.sookitSites.x, true);
  assert.equal(view.stored.sookitSites.youtube, true);
});
