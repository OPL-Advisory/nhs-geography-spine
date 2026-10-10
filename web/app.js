import { nextActiveIndex, parseRoute, routeUrl, searchRecords } from './search.mjs';

const $ = (selector) => document.querySelector(selector);
const number = new Intl.NumberFormat('en-GB');
const percent = new Intl.NumberFormat('en-GB', { style: 'percent', maximumFractionDigits: 1 });
const detailHost = $('#detail');
const searchInput = $('#record-search');
const resultsHost = $('#search-results');
const searchStatus = $('#search-status');
let release;
let indexes;
let matches = [];
let active = -1;

function el(tag, className = '', value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = String(value);
  return element;
}

function append(parent, ...children) {
  for (const child of children) parent.append(child);
  return parent;
}

function link(kind, code, label, className = '') {
  const anchor = el('a', className, label);
  anchor.href = routeUrl(kind, code);
  return anchor;
}

function heading(label, title) {
  const wrap = el('div', 'record-heading');
  append(wrap, el('p', 'section-label', label), el('h2', '', title));
  return wrap;
}

function date(value) {
  if (!value) return 'date not supplied';
  const parsed = new Date(`${String(value).slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(parsed.valueOf()) ? value : new Intl.DateTimeFormat('en-GB', {
    day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC',
  }).format(parsed);
}

function metric(label, value, note) {
  const box = el('div', 'metric');
  append(box, el('span', 'metric-label', label), el('strong', 'metric-value', value));
  if (note) box.append(el('span', 'metric-note', note));
  return box;
}

function typeLabel(type) {
  return String(type || 'other').replaceAll('_', ' ');
}

function share(value) {
  if (value > 0 && value < 0.001) return '<0.1%';
  return percent.format(value);
}

function registrations(count) {
  return `${number.format(count)} ${count === 1 ? 'registration' : 'registrations'}`;
}

function evidence(tag, text) {
  return el('span', `basis basis-${tag}`, text);
}

function empty(text) {
  return el('p', 'empty-state', text);
}

function section(title, basis, description) {
  const container = el('section', 'record-section');
  const top = el('div', 'section-top');
  append(top, el('h3', '', title));
  if (basis) top.append(evidence(basis[0], basis[1]));
  append(container, top, el('p', 'section-intro', description));
  return container;
}

function pagedList(container, rows, render, pageSize = 30) {
  const list = el('ol', 'record-list');
  const more = el('button', 'show-more', 'Show more');
  more.type = 'button';
  let shown = 0;
  function addPage() {
    for (const row of rows.slice(shown, shown + pageSize)) list.append(render(row));
    shown = Math.min(rows.length, shown + pageSize);
    more.textContent = `Show more (${number.format(rows.length - shown)} remaining)`;
    more.hidden = shown >= rows.length;
  }
  more.addEventListener('click', addPage);
  addPage();
  append(container, list, more);
}

function renderConstituency(data) {
  const root = el('article', 'record');
  append(root, heading(`Westminster constituency · ${data.code}`, data.name));
  const member = data.member;
  const memberText = member.status === 'vacant' ? 'Vacant' : member.name || 'Member not supplied';
  const cards = el('div', 'metrics');
  append(cards,
    metric('Current MP', memberText, member.party ? `${member.party} · ${date(member.snapshot_date)}` :
      `As at ${date(member.snapshot_date)}`),
    metric('Coded site addresses', number.format(data.site_organisation_count), 'Physical location evidence'),
    metric('GP practices serving this area', number.format(data.serving_gp_practice_count), 'Registered-patient evidence'),
    metric('Mapped registrations', number.format(data.registered_patients_mapped),
      `GP extract ${date(data.patient_source_period)}`));
  root.append(cards);
  root.append(el('p', 'record-warning', 'The MP is current at the Parliament snapshot. GP registrations use an earlier extract and LSOA21 best-fit allocation; they are not an exact resident count.'));

  const siteSection = section('Physical NHS locations', ['site', 'Site location'],
    'Active ODS coded provider and commissioner addresses mapped to this constituency. Codes count organisations or sites, not distinct buildings.');
  if (!data.sites.length) siteSection.append(empty('No active coded site address maps here in this source snapshot. This does not mean no NHS service is present.'));
  else {
    const groups = new Map();
    for (const site of data.sites) {
      const rows = groups.get(site.organisation_type) || [];
      rows.push(site);
      groups.set(site.organisation_type, rows);
    }
    for (const [type, rows] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
      const details = el('details', 'site-group');
      details.append(el('summary', '', `${typeLabel(type)} · ${number.format(rows.length)}`));
      pagedList(details, rows.sort((a, b) => (a.org_name || a.org_code).localeCompare(b.org_name || b.org_code)),
        (row) => {
          const item = el('li');
          append(item, link('organisation', row.org_code, row.org_name || row.org_code),
            el('span', 'code', row.org_code));
          return item;
        });
      siteSection.append(details);
    }
  }
  root.append(siteSection);

  const patientSection = section('GP practices serving registered patients', ['patient', 'Registered patients'],
    'Ranked by patients allocated to this constituency. A practice address may be in another constituency. Shares use that practice’s full source list as denominator.');
  if (!data.practices.length) patientSection.append(empty('No mapped GP registrations for this constituency appear in the selected patient extract. This is an absence of source evidence, not a zero resident population.'));
  else pagedList(patientSection, data.practices, (row) => {
    const item = el('li');
    const line = el('div', 'list-main');
    append(line, link('organisation', row.org_code, row.org_name || row.org_code),
      el('span', 'code', row.org_code));
    append(item, line, el('span', 'list-meta',
      `${registrations(row.registered_patients)} · ${share(row.share_of_practice_list)} of practice list`));
    return item;
  });
  root.append(patientSection);

  const operatorSection = section('Explicit operator links', ['operator', 'ODS RE6'],
    'Current RE6 relationships whose operator address maps here. This is an operator link, not a service catchment.');
  if (!data.operators.length) operatorSection.append(empty('No current, mapped RE6 operator-address link is recorded here.'));
  else pagedList(operatorSection, data.operators, (row) => {
    const item = el('li');
    append(item, link('organisation', row.org_code, row.org_name || row.org_code),
      el('span', 'list-meta', `Operated by ${row.parent_org_code || 'unresolved code'} · ODS ${date(row.source_snapshot_date)}`));
    return item;
  });
  root.append(operatorSection);
  detailHost.replaceChildren(root);
  document.title = `${data.name} · NHS Parliamentary Lens`;
}

function renderOrganisation(data) {
  const root = el('article', 'record');
  append(root, heading(`${typeLabel(data.organisation_type)} · ODS ${data.code}`, data.name || data.code));
  const cards = el('div', 'metrics');
  append(cards,
    metric('ODS status', data.status || 'Not supplied', `ODS snapshot ${date(data.source_snapshot_date)}`),
    metric('Address constituency', data.address.pcon24nm || 'Not mapped',
      data.address.pcon24cd || data.address.unmapped_reason || 'No source allocation'),
    metric('Address MP', data.address.member_status === 'vacant' ? 'Vacant' :
      data.address.member_name || 'Unavailable',
      `Parliament ${date(data.address.member_snapshot_date)}`));
  root.append(cards);
  const address = section('Physical address', ['site', 'Site location'],
    'A postcode-to-PCON24 allocation of this ODS address. It does not define the organisation’s population or service area.');
  if (data.address.pcon24cd) {
    const p = el('p');
    append(p, link('constituency', data.address.pcon24cd,
      `${data.address.pcon24nm} (${data.address.pcon24cd})`),
    document.createTextNode(` · ${data.address.mapping_method || 'source mapping'}`));
    address.append(p);
  } else address.append(empty(`No mapped address constituency: ${data.address.unmapped_reason || 'postcode/geography unavailable in source'}. No MP has been inferred.`));
  root.append(address);

  const operator = section('ODS operating relationship', ['operator', 'ODS RE6'],
    'Only an explicit RE6 link effective at the ODS snapshot counts as current parliamentary evidence.');
  if (!data.operator.code) operator.append(empty('No RE6 operator code is recorded for this organisation in the selected ODS report.'));
  else {
    const identity = el('p');
    append(identity, link('organisation', data.operator.code,
      data.operator.name || data.operator.code),
    document.createTextNode(` · ${data.operator.code}`));
    operator.append(identity);
    const temporal = data.operator.temporal_status || 'undated';
    operator.append(el('p', data.operator.current_at_snapshot ? 'temporal-current' : 'temporal-old',
      `Source RE6 status: ${temporal}. Start ${date(data.operator.start_date)}; end ${date(data.operator.end_date)}.`));
    if (data.operator.current_at_snapshot && data.operator.current_address_pcon24cd) {
      const p = el('p');
      append(p, document.createTextNode('Current operator address: '),
        link('constituency', data.operator.current_address_pcon24cd,
          data.operator.current_address_pcon24cd),
        document.createTextNode(` · MP ${data.operator.current_address_member_name ||
          data.operator.current_address_member_status || 'unavailable'}`));
      operator.append(p);
    } else if (!data.operator.current_at_snapshot) {
      operator.append(empty('This source link is historical or future at the ODS snapshot. Its parent address is not a current parliamentary relationship.'));
    } else operator.append(empty('Current RE6 operator address cannot be mapped; no parent MP is inferred.'));
  }
  root.append(operator);

  const patients = section('Constituencies with GP registrations', ['patient', 'Registered patients'],
    'GP-only LSOA21 best-fit allocations, ranked by patient count. Shares use this practice’s full list, including the explicit unmapped bucket.');
  if (data.organisation_type !== 'gp_practice') {
    patients.append(empty('No GP patient distribution is inferred for this provider or commissioner type. Its address is location evidence only.'));
  } else if (!data.served_constituencies.length) {
    patients.append(empty('No mapped GP registrations for this code are present in the selected patient extract. This is not a zero-patient assertion.'));
  } else {
    const summary = el('p', 'patient-summary',
      `Source practice list: ${number.format(data.registered_patients_total)} · Unmapped: ${number.format(data.unmapped_patient_count || 0)} · Extract ${date(data.patient_source_period)}`);
    patients.append(summary);
    pagedList(patients, data.served_constituencies, (row) => {
      const item = el('li');
      append(item, link('constituency', row.code, `${row.name} (${row.code})`),
        el('span', 'list-meta',
          `${registrations(row.registered_patients)} · ${share(row.share_of_practice_list)} of practice list`));
      return item;
    });
  }
  root.append(patients, el('p', 'source-footnote', `Organisation source: ${data.source_version || 'ODS'} · snapshot ${date(data.source_snapshot_date)}.`));
  detailHost.replaceChildren(root);
  document.title = `${data.name || data.code} · NHS Parliamentary Lens`;
}

function renderError(message) {
  const box = el('section', 'detail-error');
  append(box, el('p', 'section-label', 'Record unavailable'), el('h2', '', message),
    el('p', '', 'Check the code and the dated source snapshot. Search by name or code above.'));
  detailHost.replaceChildren(box);
}

async function fetchJson(path) {
  const response = await fetch(new URL(path, import.meta.url));
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function setActive(next) {
  active = next;
  for (const [index, option] of [...resultsHost.children].entries()) {
    option.setAttribute('aria-selected', String(index === active));
  }
  if (active >= 0) {
    searchInput.setAttribute('aria-activedescendant', `search-option-${active}`);
    resultsHost.children[active].scrollIntoView({ block: 'nearest' });
  } else searchInput.removeAttribute('aria-activedescendant');
}

function closeResults() {
  resultsHost.hidden = true;
  searchInput.setAttribute('aria-expanded', 'false');
  setActive(-1);
}

function drawResults() {
  resultsHost.replaceChildren();
  active = -1;
  if (!matches.length) {
    closeResults();
    searchStatus.textContent = 'No matching record in this pinned snapshot. Try a different name or code.';
    return;
  }
  for (const [index, row] of matches.entries()) {
    const option = el('li', 'search-option');
    option.id = `search-option-${index}`;
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', 'false');
    option.tabIndex = -1;
    append(option, el('span', 'search-name', row.name || row.code),
      el('span', 'search-kind', row.kind === 'constituency' ? 'Constituency' : typeLabel(row.type)),
      el('span', 'code', row.code));
    option.addEventListener('mousedown', (event) => event.preventDefault());
    option.addEventListener('click', () => { window.location.href = routeUrl(row.kind, row.code); });
    resultsHost.append(option);
  }
  resultsHost.hidden = false;
  searchInput.setAttribute('aria-expanded', 'true');
  searchStatus.textContent = `${matches.length} matching records shown. Use arrow keys and Enter, or select a result.`;
}

async function loadIndexes() {
  if (!indexes) {
    indexes = Promise.all([fetchJson(release.indexes.constituencies.path),
      fetchJson(release.indexes.organisations.path)]);
  }
  return indexes;
}

async function updateSearch() {
  const query = searchInput.value.trim();
  if (!query) {
    matches = [];
    closeResults();
    searchStatus.textContent = 'Start typing to search the dated snapshot.';
    return;
  }
  matches = [];
  closeResults();
  searchStatus.textContent = 'Searching the pinned snapshot…';
  try {
    const [constituencies, organisations] = await loadIndexes();
    if (query !== searchInput.value.trim()) return;
    matches = searchRecords(query, constituencies, organisations);
    drawResults();
  } catch {
    indexes = undefined;
    closeResults();
    searchStatus.textContent = 'Search index could not be loaded. Try again later.';
  }
}

searchInput.addEventListener('input', updateSearch);
searchInput.addEventListener('keydown', (event) => {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    if (!matches.length) return;
    event.preventDefault();
    setActive(nextActiveIndex(event.key, active, matches.length));
  } else if (event.key === 'Enter' && matches.length && !resultsHost.hidden) {
    event.preventDefault();
    const chosen = matches[active >= 0 ? active : 0];
    window.location.href = routeUrl(chosen.kind, chosen.code);
  } else if (event.key === 'Escape') {
    closeResults();
  } else if (event.key === 'Tab') {
    closeResults();
  }
});
searchInput.addEventListener('focus', () => { if (matches.length) drawResults(); });

async function start() {
  try {
    release = await fetchJson('release.json');
    const d = release.dates;
    $('#clocks').replaceChildren(
      el('span', '', `Parliament · ${date(d.parliament)}`),
      el('span', '', `GP registrations · ${date(d.gp_patients)}`),
      el('span', '', `ODS · ${d.ods.map(date).join(', ')}`),
      el('span', '', `Constituency geography · ${d.pcon}`));
    $('#release-id').textContent = `Pinned snapshot ${release.snapshot_id.slice(0, 12)} · ${number.format(release.counts.constituencies)} constituencies · ${number.format(release.counts.organisations)} ODS codes`;
    const coverage = release.coverage;
    $('#coverage').replaceChildren(el('h3', '', 'Coverage visible in this release'),
      el('p', '', `${number.format(coverage.active_unmapped_provider_addresses)} active provider/commissioner addresses are unmapped; ${number.format(coverage.unresolved_active_re6_links)} active RE6 links have an unresolved operator code; ${number.format(coverage.unmapped_registered_patients)} registered patients remain in the explicit unmapped bucket.`));
    const route = parseRoute(window.location.search);
    if (!route) return;
    if (route.error) { renderError(route.error); return; }
    const folder = route.kind === 'constituency' ? 'constituencies' : 'organisations';
    const detail = await fetchJson(`${release.details_base}/${folder}/${route.code}.json`);
    if (detail.code !== route.code || detail.kind !== route.kind) throw new Error('Record mismatch');
    if (route.kind === 'constituency') renderConstituency(detail);
    else renderOrganisation(detail);
  } catch (error) {
    renderError(error.message === 'HTTP 404' ? 'No record for that code in this snapshot' :
      'The pinned site snapshot could not be loaded');
    if (!release) $('#clocks').textContent = 'Source dates unavailable';
  }
}

start();
