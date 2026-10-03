// Page logic: load the pipeline's JSON, keep the filter state in the URL, and redraw.
// All arithmetic lives in stats.js; this file only decides what to show and how to word it.

import * as S from './stats.js';
import * as F from './format.js';
import { trendChart, indexChart, hideTip } from './charts.js';
import { createMap } from './map.js';

const SEV_COLORS = ['#b7d3f6', '#5598e7', '#1c5cab'];     // high, medium, low: --sev-* in styles.css
const SEV_VARS = ['var(--sev-high)', 'var(--sev-medium)', 'var(--sev-low)'];
const CHATTER_COLOR = '#d95926';                           // --chatter
const RANGES = [
  { id: '30', label: '30 days', days: 30, phrase: 'the last 30 days' },
  { id: '90', label: '3 months', days: 90, phrase: 'the last 3 months' },
  { id: '365', label: '12 months', days: 365, phrase: 'the last 12 months' },
  { id: 'all', label: 'Since 2020', days: null, phrase: 'since 2020' },
];
const MODES = [{ id: 'dots', label: 'Blocks' }, { id: 'heat', label: 'Density' }];
const TAGS = {
  violence: 'Violence', vehicle: 'Vehicle', burglary: 'Burglary', theft: 'Theft', harassment: 'Harassment',
  police: 'Police activity', vandalism: 'Vandalism', traffic: 'Traffic', court: 'Court',
};
const CHATTER_KINDS = [{ id: 'all', label: 'All' }, { id: 'news', label: 'News' }, { id: 'reddit', label: 'Reddit' }];

const $ = (id) => document.getElementById(id);

function el(tag, className, text, parent) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

const state = { beat: null, range: '365', sev: null, cat: null, mode: 'dots', chatterKind: 'all', chatterShown: 12 };
const data = { meta: null, city: null, beats: null, chatter: null, hoods: new Map() };
let ctx = null;
let map = null;
let hoodInfo = null;      // Map(beat -> entry of meta.hoods)
const current = { beat: null, drawn: false, hood: null, mask: null, range: null };   // what is on screen

// ---- loading -------------------------------------------------------------------------------------

async function json(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function loadHood(beat) {
  if (!data.hoods.has(beat)) data.hoods.set(beat, await json(`data/hood/${beat}.json`));
  return data.hoods.get(beat);
}

// ---- state <-> URL -------------------------------------------------------------------------------

function parseSet(text, n) {
  if (text === null) return null;
  const set = new Set(text.split('.').filter(Boolean).map(Number).filter((v) => Number.isInteger(v) && v >= 0 && v < n));
  return set.size === 0 || set.size === n ? null : set;
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const beat = Number(p.get('b'));
  state.beat = hoodInfo.has(beat) ? beat : data.meta.home_beat;
  state.range = RANGES.some((r) => r.id === p.get('r')) ? p.get('r') : '365';
  state.sev = parseSet(p.get('s'), ctx.nSev);
  state.cat = parseSet(p.get('c'), ctx.nCat);
  state.mode = p.get('m') === 'heat' ? 'heat' : 'dots';
}

function writeHash() {
  const p = new URLSearchParams();
  if (state.beat !== data.meta.home_beat) p.set('b', state.beat);
  if (state.range !== '365') p.set('r', state.range);
  if (state.sev) p.set('s', [...state.sev].sort().join('.'));
  if (state.cat) p.set('c', [...state.cat].sort().join('.'));
  if (state.mode !== 'dots') p.set('m', state.mode);
  const hash = p.toString();
  history.replaceState(null, '', hash ? `#${hash}` : location.pathname + location.search);
}

// ---- small building blocks -----------------------------------------------------------------------

function swatch(sev, parent) {
  const s = el('span', 'swatch round', null, parent);
  s.style.background = SEV_VARS[sev];
  s.title = `${data.meta.severities[sev].label} severity`;
  return s;
}

/** A change, in words: arrow (the only colored part), figure, and what it is compared with. */
function delta(parent, cmp, basis = 'than the year before') {
  parent.replaceChildren();
  parent.classList.add('delta');
  const kind = cmp.verdict === 'down' || cmp.verdict === 'up' ? cmp.verdict : 'flat';
  const arrow = el('span', `arrow ${kind}`, kind === 'down' ? '▼' : kind === 'up' ? '▲' : '●', parent);
  arrow.setAttribute('aria-hidden', 'true');
  if (cmp.verdict === 'few') {
    el('span', null, 'Too few to compare', parent);
    return;
  }
  const direction = cmp.change < 0 ? 'lower' : 'higher';
  if (cmp.verdict === 'flat') {
    el('strong', null, 'No clear change', parent);
    el('span', null, Math.abs(cmp.change) < 0.005
      ? '(about the same as the year before)'
      : `(${F.pct(cmp.change)} ${direction}, within the usual ups and downs)`, parent);
  } else if (cmp.prior < 20) {
    // a percentage of a handful is noise dressed up as precision; give the two counts instead
    el('strong', null, direction === 'lower' ? 'Lower' : 'Higher', parent);
    el('span', null, `(${F.int(cmp.cur)}, ${direction === 'lower' ? 'down' : 'up'} from ${F.int(cmp.prior)})`, parent);
  } else {
    el('strong', null, `${F.pct(cmp.change)} ${direction}`, parent);
    el('span', null, basis, parent);
  }
}

function segmented(root, options, selected, onPick) {
  root.replaceChildren();
  for (const o of options) {
    const b = el('button', null, o.label, root);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(o.id === selected));
    b.addEventListener('click', () => onPick(o.id));
  }
}

function barList(root, rows) {
  root.replaceChildren();
  if (!rows.length) {
    el('p', 'empty', 'Nothing in this period matches the filters.', root);
    return;
  }
  const list = el('div', 'bars', null, root);
  const max = Math.max(...rows.map((r) => r.value), 1);
  for (const r of rows) {
    const row = el('div', 'bar-row', null, list);
    el('span', 'bar-name', r.name, row).title = r.name;
    el('span', 'bar-value', F.int(r.value), row);
    const track = el('div', 'bar-track', null, row);
    el('div', 'bar-fill', null, track).style.width = `${(r.value / max) * 100}%`;
    if (r.cmp) delta(el('div', 'bar-delta', null, row), r.cmp);
  }
}

function table(root, head, rows) {
  root.replaceChildren();
  const t = el('table', null, null, root);
  const hr = el('tr', null, null, el('thead', null, null, t));
  for (const h of head) el('th', h.num ? 'num' : null, h.label, hr).scope = 'col';
  const body = el('tbody', null, null, t);
  for (const r of rows) {
    const tr = el('tr', r.className || null, null, body);
    r.cells.forEach((cell, i) => {
      const classes = [head[i].num && 'num', cell && cell.dim && 'dim', cell && cell.nowrap && 'nowrap'].filter(Boolean);
      const td = el('td', classes.join(' ') || null, null, tr);
      if (cell instanceof Node) td.appendChild(cell);
      else if (cell && typeof cell === 'object') td.textContent = cell.text;
      else td.textContent = cell;
    });
  }
  return t;
}

// ---- sections ------------------------------------------------------------------------------------

function renderHeader() {
  const select = $('hood-select');
  if (!select.options.length) {
    const byDivision = new Map();
    for (const h of data.meta.hoods) {
      if (!byDivision.has(h.division)) byDivision.set(h.division, []);
      byDivision.get(h.division).push(h);
    }
    for (const division of [...byDivision.keys()].sort()) {
      const group = el('optgroup', null, null, select);
      group.label = `${division || 'Other'} division`;
      for (const h of byDivision.get(division).sort((a, b) => a.name.localeCompare(b.name))) {
        el('option', null, h.name, group).value = h.beat;
      }
    }
    select.addEventListener('change', () => { state.beat = Number(select.value); update(); });
  }
  select.value = state.beat;
  const through = S.dayToDate(ctx.today, data.meta.epoch);
  $('asof').textContent = `SDPD reports approved through ${F.date(through)} · refreshed ${F.ago(new Date(data.meta.built_at))}`;
  document.title = `${hoodInfo.get(state.beat).name} crime · San Diego`;
}

function toggle(set, i, n) {
  const next = set ? new Set(set) : new Set();
  if (!set) next.add(i);                       // everything was on: the first click isolates one
  else if (next.has(i)) next.delete(i); else next.add(i);
  return next.size === 0 || next.size === n ? null : next;
}

function renderFilters() {
  const sev = $('sev-chips');
  sev.replaceChildren();
  data.meta.severities.forEach((s, i) => {
    const b = el('button', 'chip', null, sev);
    b.type = 'button';
    swatch(i, b);
    el('span', null, s.label, b);
    b.title = s.about;
    b.setAttribute('aria-pressed', String(!state.sev || state.sev.has(i)));
    b.addEventListener('click', () => { state.sev = toggle(state.sev, i, ctx.nSev); update(); });
  });
  const cat = $('cat-chips');
  cat.replaceChildren();
  data.meta.categories.forEach((c, i) => {
    const b = el('button', 'chip', c.label, cat);
    b.type = 'button';
    b.setAttribute('aria-pressed', String(!state.cat || state.cat.has(i)));
    b.addEventListener('click', () => { state.cat = toggle(state.cat, i, ctx.nCat); update(); });
  });
  $('reset').hidden = !state.sev && !state.cat;
}

function renderSummary(hood, mask, cmask) {
  const name = hoodInfo.get(state.beat).name;
  const year = S.compare(hood, mask, ctx, 365);
  const quarter = S.compare(hood, mask, ctx, 90);
  const cityYear = S.compareScope(data.city, 'city', 365, cmask);
  const to = F.date(S.dayToDate(year.to, data.meta.epoch));
  const filtered = state.sev || state.cat ? ' (filtered)' : '';

  $('hero-label').textContent = `Reported offenses in ${name}${filtered}, 12 months to ${to}`;
  $('hero-value').textContent = F.int(year.cur);
  delta($('hero-delta'), year, 'than the 12 months before');
  const perWeek = year.cur / (365 / 7);
  $('hero-note').textContent = `About ${perWeek >= 10 ? F.int(perWeek) : perWeek.toFixed(1)} a week. `
    + `Comparisons stop ${data.meta.settle_days} days short of today because recent reports are still arriving.`;

  const tiles = $('tiles');
  tiles.replaceChildren();
  const tile = (label, value, fill, note) => {
    const t = el('div', 'card tile', null, tiles);
    el('p', 'tile-label', label, t);
    el('p', 'tile-value', value, t);
    if (fill) fill(el('p', null, null, t));
    if (note) el('p', 'tile-note', note, t);
  };

  const highMask = S.offenseMask(ctx, state.cat, new Set([0]));
  const high = S.compare(hood, highMask, ctx, 365);
  tile('High-severity offenses, same 12 months', F.int(high.cur), (p) => delta(p, high),
    'Homicide, rape, robbery, aggravated assault, kidnapping.');
  tile(`Latest 3 months, to ${to}`, F.int(quarter.cur), (p) => delta(p, quarter, 'than the same months a year before'));
  const recent = S.count(hood, mask, ctx.today - 30, ctx.today);
  tile('Last 30 days, so far', F.int(recent), null,
    'Still filling in. Reports take days to weeks to be approved, so this is not compared with anything yet.');
  tile(`City of San Diego${filtered}, same 12 months`, F.int(cityYear.cur), (p) => delta(p, cityYear));
}

function renderTrend(hood, mask, cmask) {
  const name = hoodInfo.get(state.beat).name;
  const bySev = S.monthly(hood, mask, ctx);
  const totals = ctx.months.map((_, m) => bySev.reduce((a, s) => a + s[m], 0));
  const last = S.lastFinalMonth(ctx);
  const cityTotals = S.cityMonthly(data.city, cmask, ctx.months.length);
  const outlook = S.outlook(totals, S.seasonalIndex(cityTotals, ctx), ctx);

  const legend = $('trend-legend');
  legend.replaceChildren();
  data.meta.severities.forEach((s, i) => {
    const li = el('li', null, null, legend);
    el('span', 'swatch', null, li).style.background = SEV_VARS[i];
    el('span', null, `${s.label} severity`, li);
  });
  let li = el('li', null, null, legend);
  el('span', 'key-line', null, li);
  el('span', null, '12-month average', li);
  li = el('li', null, null, legend);
  el('span', 'swatch key-faded', null, li).style.background = SEV_VARS[1];
  el('span', null, 'Reports still arriving', li);
  if (outlook.length) {
    li = el('li', null, null, legend);
    el('span', 'key-estimate', null, li);
    el('span', null, 'Outlook', li);
  }

  const caption = $('trend-caption');
  let trimmed = '';
  const setCaption = () => {
    const parts = ['Lighter columns are the newest months, where reports are still coming in; they will rise.'];
    if (outlook.length) {
      const lo = Math.min(...outlook.map((o) => o.low));
      const hi = Math.max(...outlook.map((o) => o.high));
      const until = F.month(outlook[outlook.length - 1].year, outlook[outlook.length - 1].month, { long: true });
      parts.push(`Dashed outlines: if the past year’s level holds, expect roughly ${F.int(lo)}–${F.int(hi)} a month through ${until}.`);
    }
    caption.textContent = parts.join(' ') + trimmed;
  };
  trendChart($('trend-chart'), {
    months: ctx.months,
    series: data.meta.severities.map((s, i) => ({ name: `${s.label} severity`, color: SEV_VARS[i], values: bySev[i] })),
    average: S.trailingMean(totals, 12, last),
    outlook,
    onTrim: (firstShown) => {
      trimmed = firstShown ? ` Showing ${F.month(firstShown.year, firstShown.month)} onward; a wider screen or the table shows every year.` : '';
      setCaption();
    },
  });
  setCaption();

  // The same figures as a table: one row per calendar year.
  const years = [...new Set(ctx.months.map((m) => m.year))];
  const rows = years.map((y) => {
    const idx = ctx.months.map((m, i) => (m.year === y ? i : -1)).filter((i) => i >= 0);
    const sums = bySev.map((s) => idx.reduce((a, i) => a + s[i], 0));
    const partial = idx.some((i) => ctx.months[i].status !== 'final') || idx.length < 12;
    return { cells: [partial ? `${y} (so far)` : String(y), ...sums.map(F.int), F.int(sums.reduce((a, v) => a + v, 0))] };
  });
  table($('trend-table'), [{ label: 'Year' }, ...data.meta.severities.map((s) => ({ label: s.label, num: true })), { label: 'Total', num: true }], rows);

  // The neighborhood against the city, both as 12-month running totals indexed to their first value.
  const running = (series) => series.map((_, i) => {
    if (i < 11 || i > last) return null;
    let sum = 0;
    for (let k = i - 11; k <= i; k++) sum += series[k];
    return sum;
  });
  const mine = S.indexTo100(running(totals));
  const theirs = S.indexTo100(running(cityTotals));
  $('index-title').textContent = `${name} compared with the city`;
  const indexLegend = $('index-legend');
  indexLegend.replaceChildren();
  for (const [label, color] of [[name, 'var(--accent)'], ['City of San Diego', 'var(--muted)']]) {
    const item = el('li', null, null, indexLegend);
    el('span', 'key-line', null, item).style.borderTopColor = color;
    el('span', null, label, item);
  }
  indexChart($('index-chart'), {
    months: ctx.months,
    series: [
      { name, color: 'var(--accent)', values: mine, strong: true },
      { name: 'City of San Diego', color: 'var(--muted)', values: theirs },
    ],
  });
  const firstYear = ctx.months[0].year;
  $('index-caption').textContent = mine[last] !== null && theirs[last] !== null
    ? `12-month running totals, with ${firstYear} set to 100. ${name} is now at ${F.int(mine[last])}; the city is at ${F.int(theirs[last])}.`
    : `12-month running totals, with ${firstYear} set to 100.`;
}

function rangeBounds() {
  const r = RANGES.find((x) => x.id === state.range);
  return { ...r, after: r.days === null ? -1 : ctx.today - r.days, through: ctx.today };
}

function incidentList(hood, indices, limit = 40) {
  const list = el('div', 'pop-list');
  for (const i of indices.slice(0, limit)) {
    const o = data.meta.offenses[hood.off[i]];
    const item = el('div', 'pop-item', null, list);
    swatch(o.sev, item);
    const line = el('div', null, null, item);
    el('span', null, `${o.label} `, line);
    el('span', 'pop-date', F.date(S.dayToDate(hood.day[i], data.meta.epoch)), line);
    el('div', 'pop-code', hood.sections[hood.sec[i]], item);
  }
  if (indices.length > limit) el('div', 'pop-date', `…and ${F.int(indices.length - limit)} more`, list);
  return list;
}

function openPlace(p, lngLat) {
  if (!map || !current.hood) return;
  const { hood, mask, range } = current;
  const place = hood.places[p];
  const indices = S.records(hood, mask, range.after, range.through, { place: p });
  const node = el('div');
  el('div', 'pop-title', place[0] || 'Address not given', node);
  el('div', 'pop-sub', `${F.int(indices.length)} ${indices.length === 1 ? 'report' : 'reports'}, ${range.phrase}. Location is the block, not the exact address.`, node);
  node.appendChild(incidentList(hood, indices));
  map.popup(lngLat || [place[1] / 1e5, place[2] / 1e5], node);
}

function openStory(id, lngLat) {
  const story = data.chatter && data.chatter.stories.find((s) => s.id === id);
  if (!story || !map) return;
  const node = el('div');
  el('div', 'pop-sub', `Unverified · ${story.outlet} · ${F.date(new Date(`${story.date}T00:00:00Z`))}`, node);
  const a = el('a', 'pop-title', story.title, node);
  a.href = story.url;
  a.target = '_blank';
  a.rel = 'noopener';
  if (story.where) el('div', 'pop-sub', `Placed at ${story.where.label}, from the wording of the post.`, node);
  map.popup(lngLat || [story.where.x / 1e5, story.where.y / 1e5], node);
}

function showOnMap(lngLat, open) {
  if (!map) return;
  $('map').scrollIntoView({ behavior: 'smooth', block: 'center' });
  map.flyTo(lngLat);
  open();
}

function renderExplore(hood, mask) {
  const range = rangeBounds();
  Object.assign(current, { hood, mask, range });
  segmented($('range'), RANGES, state.range, (id) => { state.range = id; update(); });
  segmented($('mode'), MODES, state.mode, (id) => { state.mode = id; update(); });

  // map: one feature per block
  const places = S.placeCounts(hood, mask, ctx, range.after, range.through);
  const total = S.count(hood, mask, range.after, range.through);
  const features = [];
  let mapped = 0;
  let busiest = 1;
  for (const e of places.values()) busiest = Math.max(busiest, e.n);
  for (const [p, e] of places) {
    const [, x, y] = hood.places[p];
    if (x === null) continue;
    mapped += e.n;
    features.push({
      type: 'Feature', geometry: { type: 'Point', coordinates: [x / 1e5, y / 1e5] },
      // w: density weight relative to the busiest block in view (square root, so quiet blocks still show)
      properties: { p, n: e.n, w: Math.sqrt(e.n / busiest), sev: e.sev.findIndex((v) => v > 0) },
    });
  }
  const isHome = state.beat === data.meta.home_beat;
  const pins = isHome && data.chatter
    ? data.chatter.stories.filter((s) => s.where && s.day > range.after && s.day <= range.through)
    : [];
  if (map) {
    map.setPlaces({ type: 'FeatureCollection', features });
    map.setChatter({
      type: 'FeatureCollection',
      features: pins.map((s) => ({
        type: 'Feature', geometry: { type: 'Point', coordinates: [s.where.x / 1e5, s.where.y / 1e5] },
        properties: { id: s.id },
      })),
    });
    map.setMode(state.mode);
  }
  $('map-note').textContent = total
    ? `${F.int(mapped)} of ${F.int(total)} reports ${range.phrase} are on the map. `
      + `${mapped < total ? 'The rest have no usable address. ' : ''}Circles sit on the block, not the exact address. Click one for its reports, or another neighborhood to switch to it.`
    : 'Nothing in this period matches the filters.';

  const legend = $('map-legend');
  legend.replaceChildren();
  if (state.mode === 'dots') {
    el('div', null, 'Most serious report on the block', legend);
    const row = el('div', 'row', null, legend);
    data.meta.severities.forEach((s, i) => {
      swatch(i, row);
      el('span', null, s.label, row);
    });
    el('div', null, 'Bigger circle = more reports', legend);
  } else {
    el('div', null, 'Brighter = more reports nearby', legend);
  }
  if (pins.length) {
    const row = el('div', 'row', null, legend);
    el('span', 'ring', null, row);
    el('span', null, 'Unverified news or Reddit post', row);
  }

  // by type, with the year-over-year change where the period allows one
  const byCat = S.countByCategory(hood, mask, ctx, range.after, range.through);
  const cmp = range.days === 90 || range.days === 365 ? S.compareByCategory(hood, mask, ctx, range.days) : null;
  barList($('types'), data.meta.categories
    .map((c, i) => ({ name: c.label, value: byCat[i], cmp: cmp ? cmp[i] : null }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value));
  $('types-sub').textContent = cmp
    ? `Counts: ${range.phrase}. Change: ${range.days === 365 ? '12' : '3'} months to ${F.date(S.dayToDate(ctx.settled, data.meta.epoch), { year: false })} vs a year earlier`
    : `Counts: ${range.phrase}`;

  const byOffense = S.countByOffense(hood, mask, ctx, range.after, range.through);
  barList($('offenses'), data.meta.offenses
    .map((o, i) => ({ name: o.label, value: byOffense[i] }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 8));

  // busiest blocks
  const top = [...places.entries()].filter(([p]) => hood.places[p][0]).sort((a, b) => b[1].n - a[1].n).slice(0, 10);
  const blocks = $('blocks');
  if (!top.length) {
    blocks.replaceChildren();
    el('p', 'empty', 'Nothing in this period matches the filters.', blocks);
  } else {
    table(blocks, [{ label: 'Block' }, { label: 'Reports', num: true }, { label: 'High severity', num: true }],
      top.map(([p, e]) => {
        const [label, x] = hood.places[p];
        let cell = label;
        if (x !== null && map) {
          cell = el('button', 'row-button', label);
          cell.type = 'button';
          cell.addEventListener('click', () => showOnMap([hood.places[p][1] / 1e5, hood.places[p][2] / 1e5], () => openPlace(p)));
        }
        return { cells: [cell, F.int(e.n), e.sev[0] ? F.int(e.sev[0]) : { text: '0', dim: true }] };
      }));
  }

  // latest reports
  const latest = S.records(hood, mask, range.after, range.through, { limit: 15 });
  $('latest-sub').textContent = latest.length ? 'Date the offense happened, not when it was reported' : '';
  if (!latest.length) {
    $('latest').replaceChildren();
    el('p', 'empty', 'Nothing in this period matches the filters.', $('latest'));
  } else {
    table($('latest'), [{ label: 'Date' }, { label: 'Offense' }, { label: 'Block' }], latest.map((i) => {
      const o = data.meta.offenses[hood.off[i]];
      const offense = el('span', 'offense');
      swatch(o.sev, offense);
      el('span', null, o.label, offense);
      const p = hood.place[i];
      let where = { text: 'Not given', dim: true };
      if (p >= 0 && hood.places[p][0]) {
        where = hood.places[p][0];
        if (hood.places[p][1] !== null && map) {
          where = el('button', 'row-button', hood.places[p][0]);
          where.type = 'button';
          where.addEventListener('click', () => showOnMap([hood.places[p][1] / 1e5, hood.places[p][2] / 1e5], () => openPlace(p)));
        }
      }
      return { cells: [{ text: F.date(S.dayToDate(hood.day[i], data.meta.epoch)), dim: true, nowrap: true }, offense, where] };
    }));
  }
}

function renderNearby(cmask) {
  const home = hoodInfo.get(state.beat);
  const highMask = S.cellMask(ctx, state.cat, new Set([0]));
  const row = (h) => {
    const scope = String(h.beat);
    const cmp = S.compareScope(data.city, scope, 365, cmask);
    const change = el('span');
    delta(change, cmp, '');
    let nameCell = h.name;
    if (h.beat !== state.beat) {
      nameCell = el('button', 'row-button', h.name);
      nameCell.type = 'button';
      nameCell.addEventListener('click', () => { state.beat = h.beat; update(); window.scrollTo({ top: 0, behavior: 'smooth' }); });
    }
    return {
      sort: cmp.cur,
      className: h.beat === state.beat ? 'is-home' : null,
      cells: [nameCell, F.int(cmp.cur), h.sq_mi ? F.int(cmp.cur / h.sq_mi) : '–',
        F.int(S.compareScope(data.city, scope, 365, highMask).cur), change],
    };
  };
  const neighbors = home.neighbors.map((b) => hoodInfo.get(b)).filter(Boolean).map(row).sort((a, b) => b.sort - a.sort);
  const cityCmp = S.compareScope(data.city, 'city', 365, cmask);
  const cityChange = el('span');
  delta(cityChange, cityCmp, '');
  const cityRow = {
    cells: ['City of San Diego', F.int(cityCmp.cur), { text: '–', dim: true },
      F.int(S.compareScope(data.city, 'city', 365, highMask).cur), cityChange],
  };
  table($('nearby'),
    [{ label: 'Neighborhood' }, { label: 'Offenses', num: true }, { label: 'Per sq. mile', num: true },
      { label: 'High severity', num: true }, { label: 'Change from the year before' }],
    [row(home), ...neighbors, cityRow]);
  $('nearby-sub').textContent = `12 months to ${F.date(S.dayToDate(ctx.settled, data.meta.epoch))}, with the filters above. `
    + 'Busy commercial areas draw more reports than their size suggests; there are no population figures here.';
}

function renderChatter() {
  const section = $('chatter-section');
  const list = $('chatter');
  const more = $('chatter-more');
  list.replaceChildren();
  $('chatter-filter').replaceChildren();
  if (!data.chatter || !data.chatter.stories.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  if (state.beat !== data.meta.home_beat) {
    more.hidden = true;
    el('li', 'feed-excerpt', `News and Reddit posts are collected for ${data.chatter.places.join(', ')} only. `
      + 'To follow a different neighborhood, change HOME_BEAT and CHATTER_PLACES in pipeline/config.py.', list);
    return;
  }
  segmented(el('div', 'segmented', null, $('chatter-filter')), CHATTER_KINDS, state.chatterKind, (id) => {
    state.chatterKind = id;
    state.chatterShown = 12;
    renderChatter();
  });
  const stories = data.chatter.stories.filter((s) => state.chatterKind === 'all' || s.kind === state.chatterKind);
  for (const s of stories.slice(0, state.chatterShown)) {
    const li = el('li', null, null, list);
    const meta = el('div', 'feed-meta', null, li);
    el('span', null, F.date(new Date(`${s.date}T00:00:00Z`)), meta);
    el('span', null, s.more.length ? `${s.outlet} +${s.more.length} more` : s.outlet, meta);
    for (const t of s.tags) el('span', 'tag', TAGS[t] || t, meta);
    const a = el('a', 'feed-title', s.title, li);
    a.href = s.url;
    a.target = '_blank';
    a.rel = 'noopener';
    if (s.excerpt) el('p', 'feed-excerpt', s.excerpt, li);
    if (s.where && map) {
      const b = el('button', 'link', `On the map: ${s.where.label}`, li);
      b.type = 'button';
      b.addEventListener('click', async () => {
        if (!(s.day > current.range.after)) {       // widen the period so the pin is on the map
          state.range = 'all';
          await update();
        }
        showOnMap([s.where.x / 1e5, s.where.y / 1e5], () => openStory(s.id));
      });
    }
  }
  more.hidden = stories.length <= state.chatterShown;
  more.textContent = `Show more (${F.int(stories.length - state.chatterShown)} left)`;
}

function renderMethod() {
  const body = $('method-body');
  if (body.childElementCount) return;
  const { meta } = data;
  const qa = meta.qa;
  const block = (title, build) => {
    const d = el('details', null, null, body);
    el('summary', null, title, d);
    build(d);
  };
  block('Where the numbers come from', (d) => {
    el('p', null, `San Diego Police Department offense reports (FBI NIBRS format) from the City of San Diego open data portal: ${F.int(qa.rows_kept)} records since ${F.date(new Date(`${meta.first_date}T00:00:00Z`))}. `
      + 'They are counted the way SDPD counts them: one per victim for crimes against people, one per incident for property crimes. '
      + 'These are reports, not convictions, and crimes nobody reported are not here.', d);
  });
  block('What is left out', (d) => {
    el('p', null, `${F.int(qa.administrative_excluded)} records (${F.int((100 * qa.administrative_excluded) / qa.rows_kept)}%) are paperwork rather than crime reports: 72-hour mental-health holds, warrant arrests, `
      + 'parole and probation violations, missing-person and suicide-attempt reports. SDPD files them under “All Other Offenses,” its largest single code. They are not counted or mapped here.', d);
  });
  block('What the severity levels mean', (d) => {
    const rows = meta.severities.map((s, i) => ({
      cells: [s.label, s.about, meta.offenses.filter((o) => o.sev === i).map((o) => o.label).join(', ')],
    }));
    table(el('div', 'table-scroll', null, d), [{ label: 'Level' }, { label: 'Meaning' }, { label: 'Offense types in the data' }], rows);
    el('p', null, 'The grouping is this project’s own judgment (pipeline/categories.py), not an official scale.', d);
  });
  block('How change is measured', (d) => {
    el('p', null, 'Reports keep arriving for weeks after an offense: about a quarter of a month’s reports are still missing when the month ends. '
      + `Comparing a fresh period with a fully reported old one would always make the present look safer than it is. So every comparison here (1) stops ${meta.settle_days} days before the newest data and `
      + '(2) counts the year-earlier period only as it looked at the same point last year.', d);
    el('p', null, 'Replayed on every month since 2022 for one neighborhood (North Park), the 12-month figure landed within 2 percentage points of the eventual number every time, 0.7 on average. '
      + 'The 3-month figure was off by about 2 points on average and 7 at worst. Raw counts ran 2 to 30 points too low. '
      + 'Nothing was reliable for the newest 30 days, which is why those are shown but never compared. (Reproduce with: python -m analysis.backtest)', d);
    el('p', null, 'A change is called “lower” or “higher” only when it is at least 5% and at least twice the size of ordinary chance variation for counts that small. Otherwise it is “no clear change.”', d);
  });
  block('How offenses are placed in a neighborhood', (d) => {
    el('p', null, `A neighborhood is an SDPD beat. Each record is placed by where its block address falls on the map when the city’s geocoding is confident; otherwise by the beat SDPD recorded. `
      + `SDPD’s label and the address agree ${qa.label_matches_address_pct}% of the time, so totals here differ slightly from SDPD’s own dashboard. `
      + `${qa.mapped_pct}% of offenses can be drawn on the map. Addresses are rounded to the hundred-block, so a circle marks a block, never a building.`, d);
  });
  block('The outlook', (d) => {
    el('p', null, 'The dashed columns take the average of the last 12 fully reported months and adjust it for the time of year, using the citywide seasonal pattern. '
      + 'The range is where 9 in 10 past months actually landed when the same rule was applied to this neighborhood’s own history. It says what “no change” would look like; it does not predict events.', d);
  });
  block('News and Reddit posts', (d) => {
    el('p', null, 'Headlines come from Google News and posts from Reddit’s public feeds, kept when they mention the neighborhood and read like a crime or police-activity report. '
      + 'A simple keyword filter makes that call, so expect some misses and a few wrong picks. Posts are unverified, a post is placed on the map only when it names an intersection or block, '
      + 'and the number of posts says nothing about the amount of crime. No usernames are stored.', d);
  });
}

// ---- main loop -----------------------------------------------------------------------------------

async function update() {
  const main = $('main');
  main.setAttribute('aria-busy', 'true');
  hideTip();
  map?.closePopup();          // its contents were built for the previous filters
  writeHash();
  const beat = state.beat;
  const beatChanged = current.beat !== beat;
  const hood = await loadHood(beat);
  if (beat !== state.beat) return;                 // a newer selection overtook this one

  const mask = S.offenseMask(ctx, state.cat, state.sev);
  const cmask = S.cellMask(ctx, state.cat, state.sev);
  renderHeader();
  renderFilters();
  renderSummary(hood, mask, cmask);
  renderTrend(hood, mask, cmask);
  renderExplore(hood, mask);
  if (map && beatChanged) map.setBeat(beat, hoodInfo.get(beat).bbox, current.drawn);
  current.beat = beat;
  current.drawn = true;
  renderNearby(cmask);
  renderChatter();
  renderMethod();
  main.setAttribute('aria-busy', 'false');
}

async function start() {
  try {
    const [meta, city, beats] = await Promise.all([json('data/meta.json'), json('data/city.json'), json('data/beats.geojson')]);
    Object.assign(data, { meta, city, beats });
  } catch (err) {
    $('boot').textContent = 'No data yet. In the project folder run:  python -m pipeline refresh';
    console.error(err);
    return;
  }
  data.chatter = await json('data/chatter.json').catch(() => null);
  ctx = S.context(data.meta);
  hoodInfo = new Map(data.meta.hoods.map((h) => [h.beat, h]));
  readHash();

  $('boot').remove();
  for (const s of document.querySelectorAll('main > section')) s.hidden = false;
  map = createMap($('map'), {
    beats: data.beats,
    sevColors: SEV_COLORS,
    chatterColor: CHATTER_COLOR,
    onBeat: (beat) => { state.beat = beat; update(); },
    onPlace: (p, lngLat) => openPlace(p, lngLat),
    onChatter: (id, lngLat) => openStory(id, lngLat),
    onHoverBeat: (name) => {
      $('map-hover').hidden = !name;
      if (name) $('map-hover').textContent = `${name} · click to switch`;
    },
  });

  $('reset').addEventListener('click', () => { state.sev = null; state.cat = null; update(); });
  $('trend-table-toggle').addEventListener('click', (e) => {
    const box = $('trend-table');
    box.hidden = !box.hidden;
    e.currentTarget.setAttribute('aria-expanded', String(!box.hidden));
    e.currentTarget.textContent = box.hidden ? 'Show as table' : 'Hide table';
  });
  $('chatter-more').addEventListener('click', () => { state.chatterShown += 12; renderChatter(); });
  window.addEventListener('hashchange', () => { readHash(); update(); });
  await update();
}

start();
