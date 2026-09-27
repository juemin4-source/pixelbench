import { chromium } from 'playwright-core';
import { existsSync } from 'fs';

// find an installed Edge/Chrome channel
const cands = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
];
const exe = cands.find(p => existsSync(p));
console.log('browser:', exe || 'none');

const b = await chromium.launch({ executablePath: exe, headless: true });
const p = await b.newPage({ viewport: { width: 1600, height: 950 } });
const errs = [];
p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
p.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
p.on('response', r => { if (r.status() >= 400) errs.push(`HTTP ${r.status()} ${r.url()}`); });

await p.goto('http://127.0.0.1:8321/', { waitUntil: 'networkidle' });
await p.waitForTimeout(1800);

// create a project via UI
await p.fill('#newProj', 'ever_eclipse');
await p.click('#newProjBtn');
await p.waitForTimeout(900);
// create an entity
await p.fill('#newName', 'knight');
await p.click('#newBtn');
await p.waitForTimeout(1500);

// click the frame slot to open the parameter panel
await p.click('.frame');
await p.waitForTimeout(600);
const gen = await p.$('#fgen');
console.log('param panel present:', !!gen);
if (gen) {
  await gen.click();
  await p.waitForTimeout(3600);
}

await p.screenshot({ path: 'ui_project_layer.png', fullPage: false });

const info = await p.evaluate(() => ({
  projects: [...document.querySelectorAll('#projList .ent')].map(e => e.textContent.trim()),
  entities: [...document.querySelectorAll('#entList .ent')].map(e => e.textContent.trim()),
  tabs: [...document.querySelectorAll('.tab')].map(e => e.textContent.trim()),
  frames: document.querySelectorAll('.frame').length,
  cands: document.querySelectorAll('.cand').length,
  badges: [...document.querySelectorAll('.st')].map(e => e.textContent.trim()),
  valResult: document.querySelector('#valResult').textContent,
}));
console.log(JSON.stringify(info, null, 1));
console.log('console errors:', errs.length ? errs.slice(0, 6) : 'none');
await b.close();
