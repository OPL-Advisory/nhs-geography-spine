import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeSearch, nextActiveIndex, parseRoute, routeUrl, searchRecords } from '../search.mjs';

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
