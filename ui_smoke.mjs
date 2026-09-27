/**
 * PixelBench UI 冒烟测试 —— 覆盖真实点击链路。
 * 用法：先启动 bench_server.py（PIXELBENCH_MOCK=1），再 node ui_smoke.mjs
 * 需要 playwright-core（Node 24 内置 node:test 不需要；这里只依赖 playwright-core）。
 */
import { existsSync } from 'fs';
import { pathToFileURL } from 'url';

const coreCandidates = [
  process.env.PW_CORE,
  'node_modules/playwright-core/index.mjs',
  'G:/杂活/决明工作室/黑日计划/node_modules/playwright-core/index.mjs',
].filter(Boolean);
const corePath = coreCandidates.find(p => existsSync(p));
if (!corePath) {
  console.error('缺少 playwright-core。安装：npm i -D playwright-core');
  console.error('或设置 PW_CORE=<playwright-core/index.mjs 绝对路径>');
  process.exit(2);
}
const { chromium } = await import(pathToFileURL(corePath).href);

const exe = [
  process.env.PW_BROWSER,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
].filter(Boolean).find(p => existsSync(p));
if (!exe) { console.error('找不到 Edge/Chrome，设置 PW_BROWSER=<exe 路径>'); process.exit(2); }

const BASE = process.env.PIXELBENCH_URL || 'http://127.0.0.1:8321/';
const PROJ = 'ui_smoke_' + Date.now().toString().slice(-6);
const fails = [];
const ok = (name, cond, detail = '') => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${name}${detail ? '  ' + detail : ''}`);
  if (!cond) fails.push(name);
};

const b = await chromium.launch({ executablePath: exe, headless: true });
const p = await b.newPage({ viewport: { width: 1600, height: 950 } });
const errs = [];
const dialogs = [];
p.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
p.on('console', m => { if (m.type() === 'error' && !m.text().includes('favicon')) errs.push(m.text()); });
p.on('response', async r => {
  const u = r.url().replace(/^https?:\/\/[^/]+/, '');
  if (r.status() >= 400 && !u.includes('favicon')) {
    let t = ''; try { t = (await r.text()).slice(0, 80); } catch {}
    errs.push(`HTTP ${r.status()} ${u} ${t}`);
  }
});
// 归一化/打包完成是正常的 alert，不算错误
p.on('dialog', async d => { dialogs.push(d.message()); await d.dismiss(); });

const snap = () => p.evaluate(() => {
  const st = typeof state !== 'undefined' ? state : {};
  return {
    project: st.project, entity: st.entity, anim: st.anim,
    gridFrames: document.querySelectorAll('.frame').length,
    adoptedPreviews: document.querySelectorAll('.adopted-prev').length,
    cands: document.querySelectorAll('.cand').length,
    badges: [...document.querySelectorAll('.st')].map(e => e.textContent.trim()),
  };
});

try {
  await p.goto(BASE, { waitUntil: 'networkidle' });
  await p.waitForTimeout(1200);

  await p.fill('#newProj', PROJ);
  await p.click('#newProjBtn');
  await p.waitForTimeout(1200);
  await p.fill('#newName', 'knight');
  await p.click('#newBtn');
  await p.waitForTimeout(1600);

  let s = await snap();
  ok('实体创建后自动选中并有帧槽', s.entity === 'knight' && s.gridFrames === 1, JSON.stringify(s));

  await p.click('.frame');
  await p.waitForTimeout(600);
  ok('点帧后出现生成按钮', !!(await p.$('#fgen')));

  await p.click('#fgen');
  await p.waitForTimeout(4500);
  s = await snap();
  ok('生成后候选出现（轮询未停摆）', s.cands >= 1, `cands=${s.cands}`);
  ok('候选状态为待审阅', s.badges.includes('待审阅'), JSON.stringify(s.badges));

  // ★ 曾漏测的主流程：点候选 = 采纳
  const candEls = await p.$$('.cand');
  if (candEls.length) {
    await candEls[0].click();
    await p.waitForTimeout(2500);
    s = await snap();
    ok('点候选后出现已采纳预览', s.adoptedPreviews === 1, JSON.stringify(s));
    ok('采纳后帧槽数增加', s.gridFrames === 2, `gridFrames=${s.gridFrames}`);
  } else {
    ok('点候选后出现已采纳预览', false, '没有候选可点');
  }

  // 底部三个 CPU 工具
  await p.click('#btnNorm');
  await p.waitForTimeout(3000);
  await p.click('#btnPack');
  await p.waitForTimeout(2500);
  await p.click('#btnVal');
  await p.waitForTimeout(2000);
  const modalTxt = await p.evaluate(() => document.querySelector('#modal')?.textContent || '');
  ok('校验报告弹出且为 PASS', modalTxt.includes('PASS'), modalTxt.slice(0, 60));

  ok('全程无 console 错误与 4xx/5xx', errs.length === 0, errs.slice(0, 6).join(' | '));
  if (errs.length) console.log('--- 全部错误 ---'); errs.forEach(e => console.log('   ' + e));
} finally {
  // 清理
  await p.evaluate(async (proj) => {
    try { await fetch('/api/projects/' + encodeURIComponent(proj), { method: 'DELETE' }); } catch {}
  }, PROJ).catch(() => {});
  await b.close();
}

console.log(`\n${fails.length ? 'FAILURES: ' + fails.join(', ') : 'UI SMOKE PASS'}`);
process.exit(fails.length ? 1 : 0);
