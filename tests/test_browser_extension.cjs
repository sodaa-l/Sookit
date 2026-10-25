/* 无第三方依赖：验证扩展脚本的消息来源、按钮交互和 YouTube SPA 导航。 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const extension = path.join(__dirname, '..', 'browser-extension');
const URL_A = 'https://www.youtube.com/watch?v=Yb9H5vhWyHg';
const URL_B = 'https://www.youtube.com/watch?v=aaaaaaaaaaa';
const ID = 'aonfogehcnacjjmgcedjpbgcjadbegma';

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
    constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.events = {}; }
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
    getElementById: id => walk(root).find(node => node.id === id),
    querySelector: () => target,
    addEventListener: (name, listener) => { documentEvents[name] = listener; }
  };
  const location = {href};
  const window = {addEventListener(name, listener) { windowEvents[name] = listener; }};
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
  return {document, requests, flush, frames,
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

test('单次点击发送当前视频，等待期间重复点击不重复提交', async () => {
  let finish;
  const site = page(() => new Promise(resolve => { finish = resolve; }));
  const button = site.button;
  const first = button.events.click();
  await button.events.click();
  assert.equal(button.disabled, true);
  assert.equal(site.requests.length, 1);
  assert.equal(site.requests[0].url, URL_A);
  finish({ok: true, message: '后台嗅探已启动'});
  await first;
  assert.equal(button.disabled, false);
  assert.equal(site.feedback.textContent, '后台嗅探已启动');
  assert.equal(site.feedback.dataset.error, 'false');
});

test('YouTube 站内导航使用新视频链接，离开视频页即移除按钮', async () => {
  const site = page();
  site.navigate(URL_B + '&t=10');
  await site.button.events.click();
  assert.equal(site.requests[0].url, URL_B);
  site.navigate('https://www.youtube.com/results?search_query=Sookit');
  assert.equal(site.button, undefined);
  assert.equal(site.feedback.hidden, true);
  site.navigate(URL_A);
  assert.ok(site.button);
  assert.equal(page(undefined, 'https://www.youtube.com/').button, undefined);
});

test('切换视频后抛弃旧视频的异步回执', async () => {
  let finish;
  const site = page(() => new Promise(resolve => { finish = resolve; }));
  const sent = site.button.events.click();
  site.navigate(URL_B);
  finish({ok: true, message: '旧视频的回执'});
  await sent;
  assert.equal(site.feedback, undefined);
  assert.equal(site.button.disabled, false);
});

test('连接失败显示错误，恢复按钮并允许重试', async () => {
  const site = page(async () => { throw new Error('本机桥接未注册'); });
  await site.button.events.click();
  assert.equal(site.feedback.dataset.error, 'true');
  assert.match(site.feedback.textContent, /未注册/);
  assert.equal(site.button.disabled, false);
  await site.button.events.click();
  assert.equal(site.requests.length, 2);
});
