export function normalizeSearch(value) {
  return String(value).normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ').trim();
}

export function routeUrl(kind, code) {
  if (!['constituency', 'organisation'].includes(kind) || !/^[A-Z0-9]+$/.test(code)) {
    throw new Error('Invalid record route');
  }
  return `?view=${kind}&code=${encodeURIComponent(code)}`;
}

export function detailPath(release, kind, code) {
  routeUrl(kind, code);
  const folder = kind === 'constituency' ? 'constituencies' : 'organisations';
  const layout = release.detail_layout?.[folder] || 'flat';
  if (!['flat', 'code-prefix-2'].includes(layout)) throw new Error('Unsupported snapshot layout');
  const shard = layout === 'code-prefix-2' ? `${code.slice(0, 2)}/` : '';
  return `${release.details_base}/${folder}/${shard}${code}.json`;
}

export function parseRoute(search) {
  const params = new URLSearchParams(search);
  if (![...params.keys()].length) return null;
  if (params.size !== 2 || !params.has('view') || !params.has('code')) return { error: 'Invalid link' };
  const kind = params.get('view');
  const code = params.get('code').toUpperCase();
  if (!['constituency', 'organisation'].includes(kind) || !/^[A-Z0-9]+$/.test(code)) {
    return { error: 'Invalid link' };
  }
  return { kind, code };
}

export function searchRecords(query, constituencyRows, organisationRows, limit = 12, kindFilter = 'all') {
  const needle = normalizeSearch(query);
  if (!needle) return [];
  const results = [];
  for (const [kind, rows] of [['constituency', constituencyRows], ['organisation', organisationRows]]) {
    if (kindFilter !== 'all' && kind !== kindFilter) continue;
    for (const row of rows) {
      const [code, name, type] = row;
      const codeSearch = normalizeSearch(code);
      const nameSearch = normalizeSearch(name);
      let rank = 4;
      if (codeSearch === needle) rank = 0;
      else if (codeSearch.startsWith(needle)) rank = 1;
      else if (nameSearch.startsWith(needle)) rank = 2;
      else if (nameSearch.includes(needle)) rank = 3;
      if (rank < 4) results.push({ kind, code, name, type, rank });
    }
  }
  return results.sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name, 'en-GB') ||
    a.code.localeCompare(b.code)).slice(0, limit);
}

export function nextActiveIndex(key, current, count) {
  if (!count) return -1;
  if (key === 'ArrowDown') return (current + 1) % count;
  if (key === 'ArrowUp') return current <= 0 ? count - 1 : current - 1;
  return current;
}
