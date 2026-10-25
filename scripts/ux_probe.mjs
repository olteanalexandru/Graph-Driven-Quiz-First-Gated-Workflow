#!/usr/bin/env node
// Evidence for a component UX audit: the same checks on every state and viewport, so audit
// rounds can be compared. It reports; it never decides. Uses the project's Playwright.
//
//   node ux_probe.mjs --states docs/items/ITEM-042/ux-states.json --out .flow/state/ux/ITEM-042/round-1
//   node ux_probe.mjs --url http://localhost:3000/practice --root "[data-testid=quiz]" --out <dir>
//
// States file: {"base_url", "root", "viewports": [[375, 667], [1280, 800]], "reflow_width": 320,
//   "states": [{"name", "path" or "url", "mock": [{"url": "**/api/x", "action": "hang|json|status",
//   "body", "status"}], "steps": [{"click": sel} | {"fill": [sel, text]} | {"press": key} | {"wait": ms}],
//   "wait_for": selector}]}
// Per state and viewport: screenshot, horizontal overflow, controls without an accessible name,
// pointer targets under 24x24 CSS px (WCAG 2.5.8), images without alt, console errors, time until
// the root is visible, and axe-core violations when @axe-core/playwright or axe-core is installed.
// Once, on the first state at the widest viewport: a Tab walk (order, focus without a visible
// indicator, focus hidden behind other content, traps), reflow at 320 px, and animations that
// keep running under prefers-reduced-motion. Writes report.json and prints one line per run.

import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const projectRequire = createRequire(path.join(process.cwd(), 'noop.js'));
const load = (name) => { try { return projectRequire(name); } catch { return null; } };

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--headed') args.headed = true;
    else if (a.startsWith('--')) args[a.slice(2)] = argv[++i];
  }
  return args;
}

const CONTROLS = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [contenteditable="true"], ' +
  '[role=button], [role=link], [role=checkbox], [role=radio], [role=switch], [role=tab], [role=menuitem], ' +
  '[role=option], [role=slider], [tabindex]:not([tabindex="-1"])';

// Runs in the page: what the root offers, measured the same way every round.
function inspect({ root, selector }) {
  const scope = root ? document.querySelector(root) : document.body;
  if (!scope) return { missingRoot: true };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const describe = (el) => {
    const id = el.id ? `#${el.id}` : '';
    const tid = el.getAttribute('data-testid') ? `[data-testid=${el.getAttribute('data-testid')}]` : '';
    return `${el.tagName.toLowerCase()}${id}${tid}`;
  };
  const nameOf = (el) => {
    const by = el.getAttribute('aria-labelledby');
    const labelled = by ? by.split(/\s+/).map((i) => document.getElementById(i)?.textContent || '').join(' ') : '';
    const label = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.textContent : '';
    const wrapped = el.closest('label')?.textContent;
    const img = el.querySelector('img[alt]')?.getAttribute('alt');
    const svg = el.querySelector('svg title')?.textContent;
    return (el.getAttribute('aria-label') || labelled || label || wrapped || el.innerText || el.value ||
      el.getAttribute('title') || img || svg || el.getAttribute('placeholder') || '').trim();
  };
  const controls = [...scope.querySelectorAll(selector)].filter(visible);
  const unnamed = controls.filter((el) => !nameOf(el)).map(describe);
  const small = controls.map((el) => ({ el, r: el.getBoundingClientRect() }))
    .filter(({ el, r }) => (r.width < 24 || r.height < 24) && !(el.tagName === 'A' && getComputedStyle(el).display === 'inline'))
    .map(({ el, r }) => `${describe(el)} ${Math.round(r.width)}x${Math.round(r.height)} "${nameOf(el).slice(0, 30)}"`);
  const doc = document.documentElement;
  return {
    missingRoot: false,
    controls: controls.length,
    unnamed,
    smallTargets: small,
    imagesWithoutAlt: [...scope.querySelectorAll('img:not([alt])')].filter(visible).length,
    overflowX: doc.scrollWidth > doc.clientWidth + 1,
    text: (scope.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 200),
  };
}

// Runs in the page after each Tab: where focus is and whether a person could see it.
function focusInfo({ root }) {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  if (!el.__flowUid) el.__flowUid = (window.__flowNext = (window.__flowNext || 0) + 1);
  const s = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const ring = (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== 'none');
  const cx = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
  const cy = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
  const top = document.elementFromPoint(cx, cy);
  const scope = root ? document.querySelector(root) : document.body;
  return {
    uid: el.__flowUid,
    multiStop: el.tagName === 'INPUT' && ['date', 'time', 'datetime-local', 'month', 'week'].includes(el.type),
    id: `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}`,
    name: (el.getAttribute('aria-label') || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.textContent) ||
      el.innerText || el.value || '').trim().slice(0, 40),
    inRoot: !!scope && scope.contains(el),
    visibleFocus: ring,
    obscured: !(top && (top === el || el.contains(top) || top.contains(el))),
  };
}

async function applyMocks(page, mocks = []) {
  for (const m of mocks) {
    await page.route(m.url, async (route) => {
      if (m.action === 'hang') return;                         // never answers: the loading state
      if (m.action === 'json') return route.fulfill({ status: m.status || 200, contentType: 'application/json', body: JSON.stringify(m.body ?? []) });
      if (m.action === 'status') return route.fulfill({ status: m.status || 500, body: m.body || '' });
      return route.continue();
    });
  }
}

async function runSteps(page, steps = []) {
  for (const s of steps) {
    if (s.click) await page.locator(s.click).first().click();
    else if (s.fill) await page.locator(s.fill[0]).first().fill(s.fill[1]);
    else if (s.press) await page.keyboard.press(s.press);
    else if (s.wait) await page.waitForTimeout(s.wait);
  }
}

async function open(browser, cfg, state, viewport, extra = {}) {
  const [width, height] = viewport;
  const mobile = width < 600;
  const context = await browser.newContext({ viewport: { width, height }, isMobile: mobile, hasTouch: mobile, ...extra });
  const page = await context.newPage();
  const errors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text().slice(0, 200)); });
  page.on('pageerror', (err) => errors.push(String(err.message || err).slice(0, 200)));
  await applyMocks(page, state.mock);
  const url = state.url || new URL(state.path || '/', cfg.base_url).toString();
  const started = Date.now();
  await page.goto(url, { waitUntil: (state.mock || []).some((m) => m.action === 'hang') ? 'domcontentloaded' : 'load' });
  await runSteps(page, state.steps);
  let msToRoot = null;
  try {
    await page.locator(state.wait_for || cfg.root || 'body').first().waitFor({ state: 'visible', timeout: 10000 });
    msToRoot = Date.now() - started;
  } catch { /* reported as missingRoot below */ }
  await page.waitForTimeout(state.settle_ms ?? 300);
  return { context, page, errors, msToRoot };
}

async function axeScan(page, root) {
  const builder = load('@axe-core/playwright');
  const tags = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
  let results = null;
  if (builder) {
    const AxeBuilder = builder.default || builder.AxeBuilder || builder;
    let b = new AxeBuilder({ page }).withTags(tags);
    if (root) b = b.include(root);
    results = await b.analyze();
  } else {
    const axe = load('axe-core');
    if (!axe) return null;
    await page.addScriptTag({ content: axe.source });
    results = await page.evaluate(([r, t]) => window.axe.run(r ? document.querySelector(r) : document, { runOnly: t }), [root, tags]);
  }
  return results.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, target: v.nodes[0]?.target }));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const pw = load('playwright') || load('@playwright/test');
  if (!pw) {
    console.error('ux_probe: Playwright is not installed in this project (npm i -D @playwright/test)');
    process.exit(2);
  }
  const cfg = args.states ? JSON.parse(readFileSync(args.states, 'utf8')) : {};
  cfg.base_url = args.url || cfg.base_url || 'http://localhost:3000';
  if (!cfg.states?.length) cfg.states = [{ name: 'default', url: cfg.base_url }];
  cfg.root = args.root || cfg.root || '';
  const viewports = cfg.viewports || [[375, 667], [1280, 800]];
  const out = args.out || path.join('.flow', 'state', 'ux', 'probe');
  mkdirSync(out, { recursive: true });
  const browser = await pw.chromium.launch({ headless: !args.headed, ...(args.channel ? { channel: args.channel } : {}) });
  const axeName = load('@axe-core/playwright') ? '@axe-core/playwright' : load('axe-core') ? 'axe-core' : null;
  const report = { at: new Date().toISOString(), base_url: cfg.base_url, root: cfg.root, axe: axeName, states: [] };
  for (const state of cfg.states) {
    for (const vp of viewports) {
      let opened;
      try {
        opened = await open(browser, cfg, state, vp);
      } catch (e) {                                   // one unreachable state must not lose the others
        report.states.push({ name: state.name, viewport: vp, error: String(e.message || e).slice(0, 300) });
        console.log(`${state.name} ${vp[0]}x${vp[1]}: could not reach the state (${String(e.message || e).split('\n')[0]})`);
        continue;
      }
      const { context, page, errors, msToRoot } = opened;
      const info = await page.evaluate(inspect, { root: cfg.root, selector: CONTROLS });
      const shot = path.join(out, `${state.name}-${vp[0]}x${vp[1]}.png`);
      const target = cfg.root && !info.missingRoot ? page.locator(cfg.root).first() : page;
      await target.screenshot({ path: shot }).catch(() => page.screenshot({ path: shot, fullPage: true }));
      let aria = null;
      if (cfg.root && !info.missingRoot && page.locator(cfg.root).first().ariaSnapshot) {
        aria = await page.locator(cfg.root).first().ariaSnapshot().catch(() => null);
        if (aria) writeFileSync(path.join(out, `${state.name}-${vp[0]}x${vp[1]}.aria.yml`), aria);
      }
      const axe = axeName ? await axeScan(page, cfg.root).catch((e) => [{ id: 'axe-error', help: String(e.message || e) }]) : null;
      const entry = { name: state.name, viewport: vp, screenshot: shot, msToRoot, consoleErrors: errors, axe, ...info };
      report.states.push(entry);
      console.log(`${state.name} ${vp[0]}x${vp[1]}: ` + (info.missingRoot ? 'root not found' :
        `${info.controls} controls, ${info.unnamed.length} unnamed, ${info.smallTargets.length} small targets, ` +
        `overflow ${info.overflowX ? 'yes' : 'no'}, ${errors.length} console errors` +
        (axe ? `, axe ${axe.length} violations` : '')));
      await context.close();
    }
  }
  const first = cfg.states[0];
  const wide = [...viewports].sort((a, b) => b[0] - a[0])[0];
  try {
    const { context, page } = await open(browser, cfg, first, wide);
    const order = [];
    let same = 0;
    let last = null;
    for (let i = 0; i < (cfg.max_tabs || 40); i++) {
      await page.keyboard.press('Tab');
      const f = await page.evaluate(focusInfo, { root: cfg.root });
      if (!f) continue;
      same = f.uid === last ? same + 1 : 0;                   // the same element, not just the same name
      last = f.uid;
      if (same >= 2 && !f.multiStop) { order.push({ ...f, trap: true }); break; }
      if (same) continue;                                       // date and time inputs take several Tabs
      if (order.length && order[0].uid === f.uid) break;        // wrapped around
      order.push(f);
    }
    const inRoot = order.filter((f) => f.inRoot);
    report.keyboard = {
      state: first.name, viewport: wide,
      order: inRoot.map((f) => `${f.id} "${f.name}"`),
      rootReachable: inRoot.length > 0,
      noVisibleFocus: inRoot.filter((f) => !f.visibleFocus).map((f) => `${f.id} "${f.name}"`),
      obscured: inRoot.filter((f) => f.obscured).map((f) => `${f.id} "${f.name}"`),
      trap: order.some((f) => f.trap),
    };
    await context.close();
    console.log(`keyboard: ${inRoot.length} stops in the root, ${report.keyboard.noVisibleFocus.length} without visible focus, ` +
      `${report.keyboard.obscured.length} obscured, trap ${report.keyboard.trap ? 'yes' : 'no'}`);
  } catch (e) {
    report.keyboard = { error: String(e.message || e).slice(0, 300) };
    console.log(`keyboard: not checked (${String(e.message || e).split('\n')[0]})`);
  }
  try {
    const width = cfg.reflow_width || 320;
    const { context, page } = await open(browser, cfg, first, [width, 640], { isMobile: false, hasTouch: false });
    report.reflow = { width, overflowX: (await page.evaluate(inspect, { root: cfg.root, selector: CONTROLS })).overflowX };
    await context.close();
    const reduced = await open(browser, cfg, first, wide, { reducedMotion: 'reduce' });
    report.reducedMotion = { runningAnimations: await reduced.page.evaluate(() =>
      document.getAnimations().filter((a) => a.playState === 'running').length) };
    await reduced.context.close();
    console.log(`reflow ${width}px: overflow ${report.reflow.overflowX ? 'yes' : 'no'}; ` +
      `reduced motion: ${report.reducedMotion.runningAnimations} animations still running`);
  } catch (e) {
    report.reflow = report.reflow || { error: String(e.message || e).slice(0, 300) };
    console.log(`reflow and reduced motion: not checked (${String(e.message || e).split('\n')[0]})`);
  }
  await browser.close();
  writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  console.log(`report: ${path.join(out, 'report.json')}` + (axeName ? '' : ' (axe-core not installed: no automated WCAG rule scan)'));
}

main().catch((e) => { console.error(`ux_probe: ${e.message || e}`); process.exit(2); });
