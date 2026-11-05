// 用 CDP 量页面宽度，找出横向溢出的元素。零依赖，靠 Node 内置 WebSocket。
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const PAGE = 'file:///F:/SPACE/2026-11-03-14-05-38/mhgu-app/app/www/index.html';
const PORT = 9344;
const profile = path.join(os.tmpdir(), 'edge-measure-' + Date.now());

const proc = spawn(EDGE, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1',
  '--allow-file-access-from-files', '--remote-debugging-port=' + PORT,
  '--window-size=390,900', '--user-data-dir=' + profile, 'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function main() {
  let list = null;
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      list = await r.json();
      if (list && list.length) break;
    } catch (e) { /* 还没起来 */ }
    await sleep(250);
  }
  if (!list || !list.length) throw new Error('Edge 没起来');
  const target = list.find(t => t.type === 'page');
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      pending.get(m.id)(m);
      pending.delete(m.id);
    }
  });
  const send = (method, params) => new Promise(res => {
    const mid = ++id;
    pending.set(mid, res);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  await new Promise(r => ws.addEventListener('open', r));
  await send('Page.enable', {});
  await send('Runtime.enable', {});
  await send('Page.navigate', { url: PAGE });
  await sleep(1200);

  const expr = `(() => {
    const de = document.documentElement;
    const out = { clientWidth: de.clientWidth, scrollWidth: de.scrollWidth, offenders: [] };
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.right > de.clientWidth + 0.5 || r.width > de.clientWidth + 0.5) {
        out.offenders.push({
          tag: el.tagName, cls: el.className || '', id: el.id || '',
          w: Math.round(r.width), right: Math.round(r.right), sw: el.scrollWidth
        });
      }
    }
    out.offenders = out.offenders.slice(0, 12);
    return JSON.stringify(out);
  })()`;
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
  console.log(r.result && r.result.result && r.result.result.value);

  const errs = await send('Runtime.evaluate', {
    expression: 'JSON.stringify(window.__errs||[])', returnByValue: true
  });
  console.log('errors:', errs.result && errs.result.result && errs.result.result.value);

  ws.close();
}

main().catch(e => { console.error('FAIL', e.message); })
  .finally(() => {
    try { proc.kill(); } catch (e) {}
    setTimeout(() => process.exit(0), 300);
  });
