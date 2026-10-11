const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const base = process.env.C3_TEST_URL || 'http://127.0.0.1:8878';
const out = path.join(__dirname, '../output/automatic_import_20261010/browser');
fs.mkdirSync(out, { recursive: true });
let browser;
(async () => {
  browser = await chromium.launch({ headless: true, channel: 'msedge' });
  const results = [], errors = [];
  for (const width of [390, 1280]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.waitForFunction(() => window.__C3_APP_READY);
    await page.evaluate(() => window.App.openImport());
    await page.waitForSelector('.saved-craft-row');
    const count = await page.locator('.saved-craft-row').count();
    assert(count > 0);
    await page.screenshot({ path: path.join(out, `craft-library-${width}.png`), fullPage: true });
    const libraryWidth = await page.evaluate(() => ({ scroll: document.getElementById('mo-import').scrollWidth, viewport: innerWidth }));
    assert(libraryWidth.scroll <= libraryWidth.viewport + 1);
    await page.locator('#im-close').click();
    await page.evaluate(() => window.App.selectRocket('neutron_i_2630', 'charts'));
    await page.waitForFunction(() => document.getElementById('ch-c3').data?.length);
    const before = await page.evaluate(() => document.getElementById('ch-c3').data[0].y[0]);
    await page.locator('#chart-fairing-enabled').check();
    await page.locator('#chart-fairing-mass').fill('0.2');
    await page.waitForFunction(value => Math.abs(document.getElementById('ch-c3').data[0].y[0] - value) < 1e-8, before - 0.2);
    const after = await page.evaluate(() => ({ payload: document.getElementById('ch-c3').data[0].y[0],
      note: document.getElementById('chart-fairing-note').textContent,
      width: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert(after.width <= after.viewport + 1);
    assert(after.note.includes('不模拟抛弃'));
    await page.screenshot({ path: path.join(out, `fairing-${width}.png`), fullPage: true });
    await page.locator('#chart-fairing-enabled').uncheck();
    await page.waitForFunction(value => document.getElementById('ch-c3').data[0].y[0] === value, before);
    results.push({ width, craftCount: count, before, after });
    await page.close();
  }
  fs.writeFileSync(path.join(out, 'checks.json'), JSON.stringify({ results, errors }, null, 2));
  console.log(JSON.stringify({ results, errors }));
  assert.equal(errors.length, 0);
  await browser.close();
})().catch(async error => { console.error(error); if (browser) await browser.close(); process.exitCode = 1; });
