import test from 'node:test';
import assert from 'node:assert/strict';

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.hidden = false;
    this.textContent = '';
  }

  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  getAttribute(name) { return this.attributes.get(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  scrollIntoView() {}
  dispatch(name, key) {
    let prevented = false;
    this.listeners.get(name)({ key, preventDefault: () => { prevented = true; } });
    return prevented;
  }
}

function descendants(root) {
  return [root, ...root.children.flatMap(descendants)];
}

const release = {
  snapshot_id: 'abc123456789',
  dates: { parliament: '2026-10-10', gp_patients: '2026-07-01',
    ods: ['2026-10-10'], pcon: 'July 2024',
    postcode: 'NHS Postcode Directory full, August 2026' },
  counts: { constituencies: 1, organisations: 1 },
  coverage: { active_unmapped_provider_addresses: 0,
    unresolved_active_re6_links: 1, unmapped_registered_patients: 0 },
  indexes: { constituencies: { path: 'indexes/constituencies.json' },
    organisations: { path: 'indexes/organisations.json' } },
  details_base: 'details',
};

async function openApp(search, detail, caseName, { releaseResponse = release, requestPaths = [] } = {}) {
  const nodes = new Map();
  for (const id of ['detail', 'record-search', 'search-results', 'search-status',
    'clocks', 'release-id', 'coverage']) nodes.set(`#${id}`, new Element('div'));
  globalThis.document = {
    title: '', querySelector: (selector) => nodes.get(selector),
    createElement: (tag) => new Element(tag),
    createTextNode: (value) => { const node = new Element('#text'); node.textContent = value; return node; },
  };
  globalThis.window = { location: { search, href: '' } };
  globalThis.fetch = async (url) => {
    const path = url.pathname;
    requestPaths.push(path);
    let value;
    if (path.endsWith('/release.json')) value = await releaseResponse;
    else if (path.endsWith('/indexes/constituencies.json')) value = [['E14001063', 'Aldershot']];
    else if (path.endsWith('/indexes/organisations.json')) value = [['R00001', 'Aldershot Trust', 'nhs_trust']];
    else value = detail;
    return { ok: true, json: async () => value };
  };
  await import(`../app.js?case=${caseName}`);
  await new Promise((resolve) => setImmediate(resolve));
  return nodes;
}

test('early search waits for release metadata and honours an edited or cleared query', async () => {
  for (const [caseName, nextQuery, expectedCodes] of [
    ['early', undefined, ['E14001063', 'R00001']],
    ['edited', 'R00001', ['R00001']],
    ['cleared', '', []],
  ]) {
    let finishRelease;
    const releaseResponse = new Promise((resolve) => { finishRelease = resolve; });
    const requestPaths = [];
    const nodes = await openApp('', null, caseName, { releaseResponse, requestPaths });
    const input = nodes.get('#record-search');
    const results = nodes.get('#search-results');
    const status = nodes.get('#search-status');
    input.value = 'Aldershot';
    input.dispatch('input');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(status.textContent, 'Searching the pinned snapshot…');
    assert.equal(results.hidden, true);
    assert.equal(results.children.length, 0);
    assert.equal(requestPaths.length, 1);
    if (nextQuery !== undefined) {
      input.value = nextQuery;
      input.dispatch('input');
    }
    finishRelease(release);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(input.value, nextQuery ?? 'Aldershot');
    assert.deepEqual(results.children.map((option) => option.children[2].textContent), expectedCodes);
    assert.equal(results.hidden, expectedCodes.length === 0);
    assert.equal(input.getAttribute('aria-expanded'), String(expectedCodes.length > 0));
    assert.equal(requestPaths.filter((path) => path.includes('/indexes/')).length, 2);
    assert.equal(requestPaths.filter((path) => path.endsWith('/release.json')).length, 1);
    assert.ok(!status.textContent.includes('could not be loaded'));
    if (nextQuery === '') {
      assert.equal(status.textContent, 'Start typing to search the dated snapshot.');
    }
  }
});

test('Escape then either arrow reopens matches, updates ARIA, and Enter routes', async () => {
  const nodes = await openApp('', null, 'keyboard');
  const input = nodes.get('#record-search');
  const results = nodes.get('#search-results');
  assert.ok(nodes.get('#clocks').children.some((node) =>
    node.textContent.includes('Postcode allocation · NHS Postcode Directory full, August 2026')));
  input.value = 'Aldershot';
  input.dispatch('input');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(results.children.length, 2);
  assert.equal(input.dispatch('keydown', 'Escape'), true);
  assert.equal(input.value, 'Aldershot');
  assert.equal(results.hidden, true);
  assert.equal(input.getAttribute('aria-expanded'), 'false');
  assert.equal(input.getAttribute('aria-activedescendant'), undefined);
  assert.equal(input.dispatch('keydown', 'ArrowUp'), true);
  assert.equal(results.hidden, false);
  assert.equal(input.getAttribute('aria-expanded'), 'true');
  assert.equal(input.getAttribute('aria-activedescendant'), 'search-option-1');
  assert.equal(results.children[1].getAttribute('aria-selected'), 'true');
  assert.equal(input.dispatch('keydown', 'Enter'), true);
  assert.equal(window.location.href, '?view=organisation&code=R00001');
  assert.equal(input.dispatch('keydown', 'Escape'), true);
  assert.equal(input.value, 'Aldershot');
  input.dispatch('keydown', 'ArrowDown');
  assert.equal(input.getAttribute('aria-activedescendant'), 'search-option-0');
  input.dispatch('keydown', 'Enter');
  assert.equal(window.location.href, '?view=constituency&code=E14001063');
});

test('patient-only practice stays visible with no missing ODS page link', async () => {
  const detail = {
    kind: 'constituency', code: 'E14001063', name: 'Aldershot',
    member: { name: 'Member', party: 'Party', status: 'current', snapshot_date: '2026-10-10' },
    site_organisation_count: 0, serving_gp_practice_count: 1,
    registered_patients_mapped: 7, patient_source_period: '2026-07-01',
    sites: [], operators: [], practices: [{ org_code: 'A81999', org_name: 'Patient-only practice',
      profile_available: false, registered_patients: 7, share_of_practice_list: 1 }],
  };
  const nodes = await openApp('?view=constituency&code=E14001063', detail, 'patient');
  const all = descendants(nodes.get('#detail'));
  assert.ok(all.some((node) => node.textContent.includes('Practice profile unavailable')));
  assert.ok(all.some((node) => node.textContent === 'A81999'));
  assert.ok(!all.some((node) => node.tag === 'a' && node.href?.includes('A81999')));
});

test('unresolved RE6 code stays visible with no missing operator link', async () => {
  const detail = {
    kind: 'organisation', code: 'B00002', name: 'Unresolved branch',
    organisation_type: 'gp_branch_surgery', status: 'ACTIVE', source_snapshot_date: '2026-10-10',
    source_version: 'ODS',
    address: { pcon24cd: null, pcon24nm: null, member_name: null,
      member_status: null, member_snapshot_date: '2026-10-10', unmapped_reason: 'source gap' },
    operator: { code: 'Y00001', name: null, profile_available: false,
      temporal_status: 'effective', current_at_snapshot: true,
      start_date: '2020-01-01', end_date: null, current_address_pcon24cd: null },
    served_constituencies: [],
  };
  const nodes = await openApp('?view=organisation&code=B00002', detail, 'operator');
  const all = descendants(nodes.get('#detail'));
  assert.ok(all.some((node) => node.textContent.includes('Operator unresolved')));
  assert.ok(all.some((node) => node.textContent.includes('Y00001')));
  assert.ok(!all.some((node) => node.tag === 'a' && node.href?.includes('Y00001')));
});
