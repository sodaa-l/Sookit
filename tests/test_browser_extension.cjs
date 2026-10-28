/* 无第三方依赖：验证扩展脚本的消息来源、按钮交互和 YouTube SPA 导航。 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const extension = path.join(__dirname, '..', 'browser-extension');
const URL_A = 'https://www.youtube.com/watch?v=Yb9H5vhWyHg';
const URL_B = 'https://www.youtube.com/watch?v=aaaaaaaaaaa';
const ID = 'inplhidhdgncdjkkmbfcocedkabbaghn';

function background(native = async () => ({ok: true})) {
  let receive;
  const calls = [];
  const context = vm.createContext({URL, chrome: {runtime: {
    id: ID,
    onMessage: {addListener(listener) { receive = listener; }},
    async sendNativeMessage(host, request) { calls.push({host, request}); return native(request); }
  }}});
  vm.runInContext(fs.readFileSync(path.join(extension, 'background.js'), 'utf8'), context);
  const sender = {id: ID, frameId: 0, tab: {id: 1}, url: URL_A};
  return {calls, sender, send(message, source = sender) {
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

test('未注册本机桥接时给出操作提示，忙碌响应原样返回', async () => {
  const missing = background(async () => { throw new Error('Specified native messaging host not found.'); });
  const failure = await missing.send({action: 'sniff', url: URL_A});
  assert.equal(failure.ok, false);
  assert.match(failure.error, /注册脚本/);
  const busy = background(async () => ({ok: false, error: 'Sookit 正在嗅探其他视频'}));
  assert.match((await busy.send({action: 'sniff', url: URL_A})).error, /正在嗅探/);
});

function page(sendMessage = async () => ({ok: true, message: '已在后台开始嗅探'}), href = URL_A) {
  class Element {
    constructor(tag) {
      this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.events = {};
      this.style = {}; this.offsetWidth = 224; this.offsetHeight = 160; this.hidden = false;
    }
    get isConnected() { return this === root || Boolean(this.parentElement?.isConnected); }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    focus() { document.activeElement = this; }
    getBoundingClientRect() { return rect; }
    append(...elements) {
      for (const child of elements) { child.remove(); this.children.push(child); child.parentElement = this; }
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
  const rect = {top: 100, bottom: 140, right: 800};
  const root = new Element('html');
  const head = new Element('head');
  const body = new Element('body');
  root.append(head, body);
  const documentEvents = {};
  const windowEvents = {};
  let target = new Element('actions');
  body.append(target);
  const walk = node => [node, ...node.children.flatMap(walk)];
  const document = {documentElement: root, head, body,
    createElement: tag => new Element(tag),
    createElementNS: (_ns, tag) => new Element(tag),
    getElementById: id => walk(root).find(node => node.id === id),
    querySelector: () => target,
    addEventListener: (name, listener) => { documentEvents[name] = listener; }
  };
  const location = {href};
  const window = {innerWidth: 1024, innerHeight: 768,
    addEventListener(name, listener) { windowEvents[name] = listener; }};
  window.top = window;
  const frames = [];
  const observers = [];
  const timers = new Map();
  let timerId = 0;
  const requests = [];
  const context = vm.createContext({document, window, location, URL,
    requestAnimationFrame: fn => frames.push(fn),
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; },
    clearTimeout: id => timers.delete(id),
    MutationObserver: class { constructor(fn) { observers.push(fn); } observe() {} },
    chrome: {runtime: {getURL: file => `chrome-extension://${ID}/${file}`,
      sendMessage(message) { requests.push(message); return sendMessage(message); }}}
  });
  const source = fs.readFileSync(path.join(extension, 'content.js'), 'utf8');
  vm.runInContext(source, context);
  const flush = () => { for (const fn of frames.splice(0)) fn(); };
  flush();
  return {document, requests, flush, frames, rect, window,
    documentEvent(name, event) { documentEvents[name]?.(event); },
    windowEvent(name) { windowEvents[name]?.(); },
    get menu() { return document.getElementById('sookit-action-menu'); },
    choose(action = 'sniff') {
      if (!this.menu || this.menu.hidden) this.button.events.click();
      return this.menu.children.find(item => item.dataset.action === action).events.click();
    },
    get button() { return document.getElementById('sookit-sniff-button'); },
    get feedback() { return document.getElementById('sookit-feedback'); },
    mutate() { observers.forEach(fn => fn()); },
    reinject() { vm.runInContext(source, context); flush(); },
    navigate(url) { location.href = url; documentEvents['yt-navigate-finish'](); flush(); },
    replaceActions() { target.remove(); target = new Element('actions'); body.append(target); this.mutate(); flush(); }
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

test('点击只展开三项菜单，选择后发送且等待时不重复提交', async () => {
  let finish;
  const site = page(() => new Promise(resolve => { finish = resolve; }));
  const button = site.button;
  button.events.click();
  assert.equal(site.requests.length, 0);
  assert.equal(site.menu.hidden, false);
  assert.equal(button.attributes['aria-expanded'], 'true');
  assert.deepEqual(site.menu.children.map(item => item.children[1].textContent),
    ['嗅探视频', '下载最佳画质', '下载封面']);
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
  assert.equal(site.feedback.textContent, '后台嗅探已启动');
  assert.equal(site.feedback.dataset.error, 'false');
});

test('三种菜单选项分别传递动作和当前视频，不提前嗅探', async () => {
  const site = page(async () => ({ok: true}));
  for (const action of ['sniff', 'download_best', 'download_cover']) {
    await site.choose(action);
    assert.equal(site.requests.at(-1).action, action);
    assert.equal(site.requests.at(-1).url, URL_A);
  }
  assert.equal(site.requests.length, 3);
  assert.match(site.feedback.textContent, /封面下载/);
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

test('切换视频后抛弃旧回执，返回同一视频也不会显示过期消息', async () => {
  for (const returnToOriginal of [false, true]) {
    let finish;
    const site = page(() => new Promise(resolve => { finish = resolve; }));
    const sent = site.choose();
    site.navigate(URL_B);
    if (returnToOriginal) site.navigate(URL_A);
    finish({ok: true, message: '旧视频的回执'});
    await sent;
    assert.equal(site.feedback, undefined);
    assert.equal(site.button.disabled, false);
  }
});

test('连接失败显示错误，恢复按钮并允许重试', async () => {
  const site = page(async () => { throw new Error('本机桥接未注册'); });
  await site.choose();
  assert.equal(site.feedback.dataset.error, 'true');
  assert.match(site.feedback.textContent, /未注册/);
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
