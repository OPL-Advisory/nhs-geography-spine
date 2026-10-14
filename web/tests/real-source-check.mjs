// Local acceptance only; no real screenshots, fixtures or record JSON are exported.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const base = process.env.LENS_SITE_URL;
if (!base || new URL(base).hostname !== '127.0.0.1') throw new Error('Loopback URL required');
if (process.env.LENS_REAL_SOURCE !== '1') throw new Error('Explicit local real-source check required');
const browser = await chromium.launch();
const context = await browser.newContext({ httpCredentials: {
  username: process.env.LENS_PREVIEW_USER, password: process.env.LENS_PREVIEW_PASSWORD } });
const page = await context.newPage();
const cases = [];
const timings = {};
const requests = [];
page.on('request', (request) => requests.push(request.url()));
const open = async (kind, code) => {
  await page.goto(`${base}/index.html?view=${kind}&code=${code}`);
  await page.locator('#detail article').waitFor();
};
try {
  let start = performance.now();
  await open('constituency', 'E14001518');
  timings.initialDeepLinkMs = Math.round(performance.now() - start);
  assert.match(await page.locator('#detail').innerText(), /Chris McDonald/);
  assert.match(await page.locator('#detail').innerText(), /104,828/);
  assert.match(await page.locator('#detail').innerText(), /GP extract 1 July 2026/);
  assert.match(await page.locator('#clocks').innerText(), /August 2026/);
  assert.ok(!requests.some((url) => url.includes('/indexes/')));
  cases.push({ id: 'J1-real-E14001518', result: 'pass' });
  await open('organisation', 'A81001');
  assert.match(await page.locator('#detail').innerText(), /Source practice list: 3,733 · Unmapped: 3/);
  await page.locator('#detail a').filter({ hasText: 'Stockton West' }).click();
  await page.getByRole('heading', { name: 'Stockton West', exact: true }).waitFor();
  const practice = page.locator('li').filter({ has: page.getByRole('link', { name: 'THE DENSHAM SURGERY', exact: true }) });
  assert.match(await practice.innerText(), /1,725 registrations/);
  assert.match(await practice.innerText(), /Practice address is in another constituency/);
  await practice.getByRole('link', { name: 'THE DENSHAM SURGERY', exact: true }).click();
  await page.getByRole('heading', { name: 'THE DENSHAM SURGERY', exact: true }).waitFor();
  cases.push({ id: 'J2-real-A81001-E14001519-return', result: 'pass' });
  await open('organisation', 'A0J1B');
  assert.match(await page.locator('#detail').innerText(), /Source RE6 status: effective/);
  await page.getByRole('link', { name: 'OXFORD UNIVERSITY HOSPITALS NHS FOUNDATION TRUST', exact: true }).click();
  await page.getByRole('heading', { name: 'OXFORD UNIVERSITY HOSPITALS NHS FOUNDATION TRUST', exact: true }).waitFor();
  assert.match(await page.locator('#detail').innerText(), /No GP patient distribution is inferred/);
  await open('organisation', 'QE1');
  assert.match(await page.locator('#detail').innerText(), /No GP patient distribution is inferred/);
  cases.push({ id: 'J3-real-A0J1B-RTH-QE1', result: 'pass' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${base}/index.html`);
  await page.selectOption('#search-kind', 'organisation');
  start = performance.now();
  await page.locator('#record-search').fill('Densham');
  await page.locator('#search-results').getByRole('option').first().waitFor();
  timings.firstOrganisationSearchMs = Math.round(performance.now() - start);
  await page.locator('#search-results').getByRole('option').filter({ hasText: 'A81001' }).click();
  await page.getByRole('heading', { name: 'THE DENSHAM SURGERY', exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', { name: 'Copy record link' }).click();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), page.url());
  await page.getByRole('button', { name: 'Back to search' }).click();
  assert.equal(await page.evaluate(() => document.activeElement.id), 'record-search');
  cases.push({ id: 'J4-real-mobile-Densham-share', result: 'pass' });
  assert.ok(requests.every((url) => new URL(url).hostname === '127.0.0.1'));
  await fs.writeFile(process.env.LENS_REAL_REPORT, JSON.stringify({ scope: 'Local real-source automated navigation; no user trial',
    browser: browser.version(), cases, timings, noExternalRequests: true,
    limits: ['Loopback timings, not mobile network or human completion times', 'No exported real screenshots or record JSON'] }, null, 2));
  console.log(`Real-source browser journeys passed: ${cases.length}`);
} finally { await browser.close(); }
