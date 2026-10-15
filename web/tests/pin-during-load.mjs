// Exercise a real browser at the old-script/new-metadata race boundary.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import { chromium } from 'playwright';

const base = process.env.LENS_SITE_URL;
if (!base || new URL(base).hostname !== '127.0.0.1') throw new Error('Loopback fixture required');
const { LENS_LEGACY_SHA: legacy, LENS_NEW_SHA: current } = process.env;
const pin = (candidate) => execFileSync(process.env.LENS_PYTHON,
  ['-m', 'web.release', 'pin', '--store', process.env.LENS_TEST_STORE, '--candidate', candidate],
  { stdio: 'pipe' });
const browser = await chromium.launch();
const cases = [];
try {
  for (const [from, to, schema] of [[legacy, current, 1], [current, legacy, 2]]) {
    pin(from);
    const context = await browser.newContext({ httpCredentials: {
      username: process.env.LENS_PREVIEW_USER, password: process.env.LENS_PREVIEW_PASSWORD } });
    const page = await context.newPage();
    const errors = [];
    const paths = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('request', (request) => paths.push(new URL(request.url()).pathname));
    let resume;
    const waiting = page.waitForRequest((request) => request.url().endsWith('/release.json'));
    const pending = new Promise((resolve) => { resume = resolve; });
    await page.route('**/release.json', async (route) => {
      await pending;
      await route.continue();
    });
    const metadataResponse = page.waitForResponse((response) => response.url().endsWith('/release.json'));
    const navigation = page.goto(`${base}/index.html?view=organisation&code=XG00001`);
    await waiting;
    assert.equal(new URL(page.url()).pathname, `/candidates/${from}/index.html`);
    assert.ok(paths.includes(`/candidates/${from}/app.js`));
    pin(to); // The old script is loaded, but its first release request is blocked.
    resume();
    await navigation;
    const release = await (await metadataResponse).json();
    assert.equal(release.schema_version, schema);
    await page.getByRole('heading', { name: 'Demo Cross Boundary Practice', exact: true }).waitFor();
    const shard = schema === 1 ? '' : 'XG/';
    assert.ok(paths.includes(`/candidates/${from}/${release.details_base}/organisations/${shard}XG00001.json`));
    assert.deepEqual(errors, []);
    cases.push({ direction: schema === 1 ? 'schema1-to-schema2' : 'schema2-to-schema1',
      switchAfterScriptBeforeRelease: true, originalCandidateRetained: true, detailLoaded: true });
    await context.close();
  }
  await fs.writeFile(process.env.LENS_PIN_REPORT, JSON.stringify({
    scope: 'Fictional initial-load race regression on authenticated loopback',
    browser: browser.version(), cases,
  }, null, 2));
  console.log('Initial-load pin regression passed in both upgrade and rollback directions');
} finally { await browser.close(); }
