import test from 'node:test';
import assert from 'node:assert/strict';
import { detailPath, normalizeSearch, nextActiveIndex, parseRoute, routeUrl, searchRecords } from '../search.mjs';

const constituencies = [['E14001063', 'Aldershot'], ['W07000102', 'Montgomeryshire and Glyndŵr']];
const organisations = [['A81001', 'Cross Boundary Surgery', 'gp_practice'],
  ['R00001', 'Aldershot Trust', 'nhs_trust']];

test('direct routes are code validated and round trip', () => {
  for (const [kind, code] of [['constituency', 'E14001063'], ['organisation', 'A81001']]) {
    assert.deepEqual(parseRoute(routeUrl(kind, code)), { kind, code });
    assert.throws(() => routeUrl(kind, code.toLowerCase()), /Invalid record route/);
    assert.deepEqual(parseRoute(`?view=${kind}&code=${code.toLowerCase()}`), { kind, code });
  }
  assert.equal(parseRoute(''), null);
  assert.deepEqual(parseRoute('?view=organisation&code=A81001&code=R00001'), { error: 'Invalid link' });
  assert.deepEqual(parseRoute('?view=organisation&code=../A81001'), { error: 'Invalid link' });
});

test('name, code, accent and ranked mixed-record search', () => {
  assert.equal(normalizeSearch('Glyndŵr'), 'glyndwr');
  assert.equal(searchRecords('glyndwr', constituencies, organisations)[0].code, 'W07000102');
  assert.equal(searchRecords('A81001', constituencies, organisations)[0].kind, 'organisation');
  assert.deepEqual(searchRecords('aldershot', constituencies, organisations).map((row) => row.code),
    ['E14001063', 'R00001']);
  assert.deepEqual(searchRecords('absent', constituencies, organisations), []);
});

test('keyboard cycling respects the visible result count', () => {
  assert.equal(nextActiveIndex('ArrowDown', -1, 3), 0);
  assert.equal(nextActiveIndex('ArrowUp', 0, 3), 2);
  assert.equal(nextActiveIndex('ArrowDown', 2, 3), 0);
  assert.equal(nextActiveIndex('ArrowDown', -1, 0), -1);
});

test('sharded detail lookup preserves deep links and supports v0.4 rollback releases', () => {
  const legacy = { details_base: 'snapshots/old/details' };
  const current = { details_base: 'snapshots/new/details',
    detail_layout: { constituencies: 'flat', organisations: 'code-prefix-2' } };
  assert.equal(detailPath(legacy, 'organisation', 'A81001'), 'snapshots/old/details/organisations/A81001.json');
  assert.equal(detailPath(current, 'organisation', 'A81001'), 'snapshots/new/details/organisations/A8/A81001.json');
  assert.equal(detailPath(current, 'organisation', 'X'), 'snapshots/new/details/organisations/X/X.json');
  assert.equal(detailPath(current, 'constituency', 'E14001063'), 'snapshots/new/details/constituencies/E14001063.json');
  assert.throws(() => detailPath(current, 'organisation', '../A81001'), /Invalid record route/);
  assert.throws(() => detailPath({ ...current, detail_layout: { organisations: 'unknown' } },
    'organisation', 'A81001'), /Unsupported snapshot layout/);
  assert.equal(routeUrl('organisation', 'A81001'), '?view=organisation&code=A81001');
});

test('entry points filter constituency and organisation searches', () => {
  assert.deepEqual(searchRecords('aldershot', constituencies, organisations, 12, 'constituency')
    .map((row) => row.code), ['E14001063']);
  assert.deepEqual(searchRecords('aldershot', constituencies, organisations, 12, 'organisation')
    .map((row) => row.code), ['R00001']);
});
