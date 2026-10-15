// Deliberately retain the v0.4 client's flat organisation-fetch contract.
// The fixture must fail if it is handed a sharded schema-2 release mid-load.
const release = await (await fetch(new URL('release.json', import.meta.url))).json();
const code = new URLSearchParams(window.location.search).get('code');
const response = await fetch(new URL(`${release.details_base}/organisations/${code}.json`, import.meta.url));
if (!response.ok) throw new Error(`Legacy flat record HTTP ${response.status}`);
const record = await response.json();
const title = document.createElement('h2');
title.textContent = record.name;
document.querySelector('#detail').replaceChildren(title);
