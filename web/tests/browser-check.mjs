// Browser and axe checks use only the explicitly labelled fictional fixture.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { chromium, firefox, webkit } from 'playwright';

const require = createRequire(import.meta.url);
const base = process.env.LENS_SITE_URL;
if (!base || new URL(base).hostname !== '127.0.0.1') throw new Error('Loopback fixture URL required');
const output = process.env.LENS_EVIDENCE_DIR;
if (!output) throw new Error('Evidence directory required');
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({
  httpCredentials: { username: process.env.LENS_PREVIEW_USER, password: process.env.LENS_PREVIEW_PASSWORD },
  viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce',
});
const page = await context.newPage();
const cases = [];
const engineChecks = [];
const consoleErrors = [];
page.on('pageerror', (error) => consoleErrors.push(error.message));
const axePath = require.resolve('axe-core/axe.min.js');
const link = (kind, code) => `${base}/index.html?view=${kind}&code=${code}`;
async function ready(url = `${base}/index.html`) {
  await page.goto(url);
  await page.locator('#release-id').filter({ hasText: 'Pinned snapshot' }).waitFor();
  assert.match(await page.locator('#preview-status').innerText(), /Synthetic demonstration/i);
}
async function check(id, run) {
  await run();
  cases.push({ id, result: 'pass' });
}
async function axe(id) {
  await page.addScriptTag({ path: axePath });
  const result = await page.evaluate(async () => window.axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] },
  }));
  await fs.writeFile(path.join(output, `${id}-axe.json`), JSON.stringify({
    url: new URL(page.url()).pathname + new URL(page.url()).search,
    axeVersion: result.testEngine.version, violations: result.violations,
    incomplete: result.incomplete.map((item) => ({ id: item.id, impact: item.impact,
      nodes: item.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })) })),
    passes: result.passes.length,
  }, null, 2));
  assert.deepEqual(result.violations.map((item) => ({ id: item.id, impact: item.impact,
    nodes: item.nodes.map((node) => node.target) })), [], `${id} accessibility violations`);
}
try {
  await check('J1-researcher', async () => {
    await ready();
    const requests = [];
    page.on('request', (request) => requests.push(request.url()));
    assert.ok(!requests.some((url) => url.includes('/indexes/')));
    await page.selectOption('#search-kind', 'constituency');
    await page.locator('#record-search').fill('Demo Harbour');
    await page.locator('#search-results').getByRole('option').first().waitFor();
    assert.ok(requests.some((url) => url.endsWith('/indexes/constituencies.json')));
    assert.ok(!requests.some((url) => url.endsWith('/indexes/organisations.json')));
    await page.locator('#record-search').press('ArrowDown');
    await page.locator('#record-search').press('Enter');
    await page.getByRole('heading', { name: 'Demo Harbour', exact: true }).waitFor();
    assert.match(await page.locator('#detail').innerText(), /Demo Member v1/);
    assert.match(await page.locator('#detail').innerText(), /GP extract 1 January 2026/);
    assert.match(await page.locator('#detail').innerText(), /not an exact resident count/);
    await axe('J1-desktop');
    await page.screenshot({ path: path.join(output, 'J1-researcher-desktop.png'), fullPage: true });
  });
  await check('J2-cross-boundary', async () => {
    await ready(link('organisation', 'XG00001'));
    await page.getByRole('heading', { name: 'Demo Cross Boundary Practice', exact: true }).waitFor();
    assert.match(await page.locator('#detail').innerText(), /Source practice list: 100 · Unmapped: 10/);
    await page.locator('#detail a').filter({ hasText: 'Demo Vale' }).click();
    await page.getByRole('heading', { name: 'Demo Vale', exact: true }).waitFor();
    assert.match(await page.locator('#detail').innerText(), /Vacant/);
    assert.match(await page.locator('#detail').innerText(), /Practice address is in another constituency/);
    await page.getByRole('link', { name: 'Demo Cross Boundary Practice', exact: true }).click();
    await page.getByRole('heading', { name: 'Demo Cross Boundary Practice', exact: true }).waitFor();
    await axe('J2-desktop');
    await page.screenshot({ path: path.join(output, 'J2-cross-boundary-desktop.png'), fullPage: true });
  });
  await check('J3-provider-commissioner', async () => {
    await ready(link('organisation', 'XR00001'));
    await page.getByRole('heading', { name: 'Demo Operated Site', exact: true }).waitFor();
    assert.match(await page.locator('#detail').innerText(), /Source RE6 status: effective/);
    await page.getByRole('link', { name: 'Demo Harbour Trust', exact: true }).click();
    await page.getByRole('heading', { name: 'Demo Harbour Trust', exact: true }).waitFor();
    assert.match(await page.locator('#detail').innerText(), /No GP patient distribution is inferred/);
    await ready(link('organisation', 'XI00001'));
    await page.getByRole('heading', { name: 'Demo Integrated Care Board', exact: true }).waitFor();
    assert.match(await page.locator('#detail').innerText(), /does not define the organisation’s population or service area/);
    await axe('J3-desktop');
    await page.screenshot({ path: path.join(output, 'J3-commissioner-desktop.png'), fullPage: true });
  });
  await check('J4-phone-shareable', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await ready();
    await page.selectOption('#search-kind', 'organisation');
    await page.locator('#record-search').fill('Demo Cross Boundary');
    await page.locator('#search-results').getByRole('option').first().click();
    await page.getByRole('heading', { name: 'Demo Cross Boundary Practice', exact: true }).waitFor();
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.getByRole('button', { name: 'Copy record link' }).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), page.url());
    await axe('J4-mobile390');
    await page.screenshot({ path: path.join(output, 'J4-phone-390.png'), fullPage: true });
    await page.getByRole('button', { name: 'Back to search' }).click();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'record-search');
  });
  await check('keyboard-native-escape-tab-focus', async () => {
    await ready();
    await page.locator('#record-search').fill('Demo');
    await page.locator('#search-results').getByRole('option').first().waitFor();
    await page.locator('#record-search').press('Escape');
    assert.equal(await page.locator('#record-search').inputValue(), 'Demo');
    assert.equal(await page.locator('#record-search').getAttribute('aria-expanded'), 'false');
    await page.locator('#record-search').press('ArrowDown');
    assert.equal(await page.locator('#record-search').getAttribute('aria-activedescendant'), 'search-option-0');
    await page.locator('#record-search').press('Tab');
    assert.notEqual(await page.evaluate(() => document.activeElement.id), 'record-search');
    assert.equal(await page.locator('#record-search').getAttribute('aria-expanded'), 'false');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'record-search');
    await page.goto(`${base}/index.html`);
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => document.activeElement.className), 'skip-link');
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => document.activeElement.id), 'main');
  });
  await check('delayed-startup-and-dismissal', async () => {
    let finish;
    const pending = new Promise((resolve) => { finish = resolve; });
    const indexesLoaded = page.waitForResponse((response) => response.url().endsWith('/indexes/organisations.json'));
    await page.route('**/release.json', async (route) => { await pending; await route.continue(); });
    await page.goto(`${base}/index.html`, { waitUntil: 'domcontentloaded' });
    await page.locator('#record-search').fill('Demo');
    await page.locator('#record-search').press('Escape');
    finish();
    await page.locator('#release-id').filter({ hasText: 'Pinned snapshot' }).waitFor();
    await indexesLoaded;
    await page.waitForFunction(() => document.querySelector('#search-status').textContent === 'Search results closed.');
    assert.equal(await page.locator('#search-results').isVisible(), false);
    await page.locator('#record-search').press('ArrowDown');
    await page.locator('#search-results').getByRole('option').first().waitFor();
    await page.unroute('**/release.json');
  });
  await check('vacant-inactive-unresolved-empty-partial', async () => {
    for (const [kind, code, expected] of [
      ['constituency', 'X14000002', /Vacant/],
      ['organisation', 'XB00001', /This organisation is inactive.*source RE6 dates are effective/s],
      ['organisation', 'XU00001', /Operator unresolved/],
      ['organisation', 'XE00001', /not a zero-patient assertion/],
      ['constituency', 'X14000003', /does not mean no NHS service/],
      ['constituency', 'X14000004', /Practice profile unavailable/],
    ]) {
      await ready(link(kind, code));
      await page.locator('#detail article').waitFor();
      assert.match(await page.locator('#detail').innerText(), expected);
      if (code === 'XB00001') assert.doesNotMatch(await page.locator('#detail').innerText(), /Current operator address/);
      if (code === 'X14000004') assert.equal(await page.locator('#detail a[href*="XP99999"]').count(), 0);
    }
  });
  await check('unknown-malformed-missing-snapshot-errors', async () => {
    await ready(link('organisation', 'NOEXIST'));
    await page.getByRole('alert').waitFor();
    assert.match(await page.getByRole('alert').innerText(), /No record for that code/);
    await axe('unknown-error');
    await ready(`${base}/index.html?view=organisation&code=..`);
    await page.getByRole('alert').waitFor();
    assert.match(await page.getByRole('alert').innerText(), /Invalid link/);
    await page.route('**/snapshots/**/details/organisations/XG/XG00001.json', (route) => route.fulfill({ status: 503 }));
    await ready(link('organisation', 'XG00001'));
    await page.getByRole('alert').waitFor();
    assert.match(await page.getByRole('alert').innerText(), /pinned site snapshot could not be loaded/);
    await page.unroute('**/snapshots/**/details/organisations/XG/XG00001.json');
    await page.route('**/release.json', (route) => route.fulfill({ status: 503 }));
    await page.goto(`${base}/index.html`);
    await page.getByRole('alert').waitFor();
    assert.equal(await page.locator('#clocks').innerText(), 'Source dates unavailable');
    await page.unroute('**/release.json');
  });
  await check('reflow320-390-desktop200-400-reduced-motion', async () => {
    for (const [id, width] of [
      ['mobile320', 320], ['mobile390', 390],
      ['zoom200', 720], ['zoom400', 360],
    ]) {
      await page.setViewportSize({ width, height: 1000 });
      await ready(link('organisation', 'XG00001'));
      await page.getByRole('heading', { name: 'Demo Cross Boundary Practice', exact: true }).waitFor();
      // Reflow equivalent to 1440px at 200/400% zoom; also test text enlargement independently.
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), id);
      assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior), 'auto');
      await axe(id);
      await page.screenshot({ path: path.join(output, `${id}.png`), fullPage: true });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await ready(link('organisation', 'XG00001'));
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), '200% text enlargement');
  });
  await check('accessible-semantics-smoke-and-clipboard-fallback', async () => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await ready(link('organisation', 'XG00001'));
    await page.getByRole('heading', { name: 'Demo Cross Boundary Practice', exact: true }).waitFor();
    const semantics = await page.locator('body').ariaSnapshot();
    assert.match(semantics, /combobox "Search by constituency/);
    assert.match(semantics, /button "Back to search"/);
    await fs.writeFile(path.join(output, 'accessibility-tree.txt'), semantics);
    await page.evaluate(() => { Object.defineProperty(navigator, 'clipboard', { value: undefined }); });
    await page.getByRole('button', { name: 'Copy record link' }).click();
    const fallback = page.getByRole('textbox', { name: 'Record link to copy' });
    await fallback.waitFor();
    assert.equal(await fallback.inputValue(), page.url());
    assert.equal(await fallback.evaluate((node) => node === document.activeElement), true);
  });
  assert.deepEqual(consoleErrors, []);
  for (const [name, engine] of [['Firefox', firefox], ['WebKit', webkit]]) {
    const other = await engine.launch();
    try {
      const ctx = await other.newContext({ httpCredentials: {
        username: process.env.LENS_PREVIEW_USER, password: process.env.LENS_PREVIEW_PASSWORD },
      viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
      const tab = await ctx.newPage();
      for (const [kind, code, heading] of [
        ['constituency', 'X14000001', 'Demo Harbour'],
        ['organisation', 'XG00001', 'Demo Cross Boundary Practice'],
        ['organisation', 'XR00001', 'Demo Operated Site'],
        ['organisation', 'XI00001', 'Demo Integrated Care Board'],
      ]) {
        await tab.goto(link(kind, code));
        await tab.getByRole('heading', { name: heading, exact: true }).waitFor();
        assert.ok(await tab.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      }
      await tab.getByRole('button', { name: 'Back to search' }).click();
      await tab.locator('#record-search').fill('Demo');
      await tab.locator('#search-results').getByRole('option').first().waitFor();
      await tab.locator('#record-search').press('Escape');
      assert.equal(await tab.locator('#record-search').inputValue(), 'Demo');
      assert.equal(await tab.locator('#record-search').getAttribute('aria-expanded'), 'false');
      await tab.locator('#record-search').press('ArrowDown');
      assert.equal(await tab.locator('#record-search').getAttribute('aria-activedescendant'), 'search-option-0');
      await tab.addScriptTag({ path: axePath });
      const violations = await tab.evaluate(async () => (await window.axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] },
      })).violations.map((item) => item.id));
      assert.deepEqual(violations, [], `${name} axe`);
      engineChecks.push({ engine: name, version: other.version(), fourDeepLinks: 'pass',
        mobile390: 'pass', keyboard: 'pass', axeViolations: violations.length });
    } finally { await other.close(); }
  }
  await fs.writeFile(path.join(output, 'browser-results.json'), JSON.stringify({
    browser: `Chromium ${browser.version()}`, axeVersion: require('axe-core/package.json').version,
    fixtureOnly: true, cases, engineChecks, pageErrors: consoleErrors,
    limits: ['Local loopback, not provider CDN', 'Reflow/text zoom emulation, not native browser UI zoom',
      'Accessibility-tree smoke; no spoken VoiceOver/NVDA or human user sessions'],
  }, null, 2));
  console.log(`Browser checks passed: ${cases.length} cases; evidence ${output}`);
} finally {
  await browser.close();
}
