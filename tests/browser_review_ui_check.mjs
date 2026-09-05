import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import net from 'node:net';

const url = process.argv[2];
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
if (!url) throw new Error('URL argument required');

const port = await new Promise((resolve, reject) => {
  const server = net.createServer();
  server.on('error', reject);
  server.listen(0, '127.0.0.1', () => {
    const value = server.address().port;
    server.close(() => resolve(value));
  });
});
const profile = mkdtempSync(join(tmpdir(), 'review-ui-chrome-'));
const child = spawn(chrome, [
  '--headless', '--disable-gpu', '--no-sandbox', `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, url,
], { stdio: 'ignore' });

async function pages() {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const value = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      if (value.some(item => item.type === 'page')) return value;
    } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error('Chrome DevTools endpoint did not become ready');
}

try {
  const page = (await pages()).find(item => item.type === 'page');
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let sequence = 0;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  };
  const call = (method, params = {}) => new Promise(resolve => {
    const id = ++sequence;
    pending.set(id, resolve);
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const message = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (message.result.exceptionDetails) {
      throw new Error(message.result.exceptionDetails.exception?.description || message.result.exceptionDetails.text);
    }
    return message.result.result.value;
  };

  await call('Runtime.enable');
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    ready = await evaluate(`typeof UI !== 'undefined' && typeof DATA !== 'undefined' && DATA.cards.length === 84 && !!document.querySelector('.question')`);
    if (ready) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  if (!ready) throw new Error('review UI did not become ready');
  const initial = await evaluate(`(() => ({
    cards: DATA.cards.length,
    counter: UI.counter.textContent,
    firstId: document.querySelector('.meta .tag').textContent,
    question: document.querySelector('.question').textContent,
    hasGold: [...document.querySelectorAll('h2')].some(x => x.textContent === 'Gold Evidence'),
    hasAlternative: [...document.querySelectorAll('h2')].some(x => x.textContent === 'Alternative Evidence'),
    hasDecision: [...document.querySelectorAll('h2')].some(x => x.textContent === 'Human Decision'),
    decisionOptions: [...document.querySelectorAll('#review_decision option')].map(x => x.textContent),
    diagnosticCollapsed: !document.querySelector('details.diagnostic').open,
    evidenceText: document.querySelector('.evidence pre').textContent,
  }))()`);
  if (initial.cards !== 84 || !initial.counter.startsWith('Question 1 / 84')) throw new Error(`bad initial state ${JSON.stringify(initial)}`);
  if (initial.firstId !== 'full-company_section_title-012' || !initial.question.includes('고려아연')) throw new Error('first card is not visible');
  if (!initial.hasGold || !initial.hasAlternative || !initial.hasDecision || !initial.diagnosticCollapsed || !initial.evidenceText) throw new Error('required card content missing');
  for (const decision of ['ACCEPT','ACCEPT_MULTI_GOLD','CHANGE_GOLD','REWRITE_QUESTION','CHANGE_TARGET','AMBIGUOUS_KEEP','DROP']) {
    if (!initial.decisionOptions.includes(decision)) throw new Error(`missing decision ${decision}`);
  }

  const navigation = await evaluate(`(() => { UI.next.click(); const next=UI.counter.textContent; UI.prev.click(); return {next,prev:UI.counter.textContent}; })()`);
  if (!navigation.next.startsWith('Question 2 / 84') || !navigation.prev.startsWith('Question 1 / 84')) throw new Error('navigation failed');

  const filters = await evaluate(`(() => {
    UI.category.value='company_concept'; UI.category.onchange(); const category=UI.counter.textContent;
    UI.category.value=''; UI.category.onchange(); UI.difficulty.value='hard'; UI.difficulty.onchange(); const difficulty=UI.counter.textContent;
    UI.difficulty.value=''; UI.difficulty.onchange(); UI.status.value='ambiguous'; UI.status.onchange(); const status=UI.counter.textContent;
    UI.status.value=''; UI.status.onchange(); UI.search.value='full-company_section_title-013'; UI.search.oninput();
    const search={counter:UI.counter.textContent,id:document.querySelector('.meta .tag').textContent};
    UI.search.value=''; UI.search.oninput(); return {category,difficulty,status,search};
  })()`);
  if (!filters.category.startsWith('Question 1 / 11') || !filters.difficulty.startsWith('Question 1 / 19') || !filters.status.startsWith('Question 1 / 10')) throw new Error(`filters failed ${JSON.stringify(filters)}`);
  if (!filters.search.counter.startsWith('Question 1 / 1') || filters.search.id !== 'full-company_section_title-013') throw new Error('search failed');

  const stored = await evaluate(`(() => {
    document.getElementById('review_decision').value='ACCEPT';
    document.getElementById('reviewer_notes').value='browser persistence check';
    document.getElementById('save').click();
    return JSON.parse(localStorage.getItem(KEY))['full-company_section_title-012'];
  })()`);
  if (stored.review_decision !== 'ACCEPT' || stored.reviewer_notes !== 'browser persistence check') throw new Error('localStorage save failed');

  await call('Page.enable');
  const loaded = new Promise(resolve => {
    const previous = socket.onmessage;
    socket.onmessage = event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Page.loadEventFired') resolve();
      if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
    };
  });
  await call('Page.reload', { ignoreCache: true });
  await loaded;
  const restored = await evaluate(`({decision:document.getElementById('review_decision').value,notes:document.getElementById('reviewer_notes').value,counter:UI.counter.textContent})`);
  if (restored.decision !== 'ACCEPT' || restored.notes !== 'browser persistence check' || !restored.counter.startsWith('Question 1 / 84')) throw new Error('localStorage restore failed');

  const exported = await evaluate(`(async () => {
    let captured=null; const originalClick=HTMLAnchorElement.prototype.click, originalRevoke=URL.revokeObjectURL;
    HTMLAnchorElement.prototype.click=function(){captured={href:this.href,download:this.download}}; URL.revokeObjectURL=()=>{};
    UI.exportButton.click(); const rows=await (await fetch(captured.href)).json();
    HTMLAnchorElement.prototype.click=originalClick; URL.revokeObjectURL=originalRevoke;
    return {download:captured.download,count:rows.length,first:rows[0]};
  })()`);
  if (exported.download !== 'periodic_full_benchmark_review_export.json' || exported.count !== 84) throw new Error('JSON export failed');
  if (exported.first.review_decision !== 'ACCEPT' || exported.first.reviewer_notes !== 'browser persistence check') throw new Error('export did not contain saved review');

  process.stdout.write(JSON.stringify({ url, initial, navigation, filters, restored, exportCount: exported.count }));
  socket.close();
} finally {
  child.kill();
  try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); } catch (_) {}
}
