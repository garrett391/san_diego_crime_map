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
// How far around one block the page can be narrowed to (see loadArea).
const RADII = [{ id: '25', label: '¼ mile', miles: 0.25 }, { id: '50', label: '½ mile', miles: 0.5 }];
const METRES_PER_MILE = 1609.344;
// The city publishes new reports every morning, so a copy this many days old is missing some.
const STALE_DAYS = 2;
const TAGS = {
  violence: 'Violence', vehicle: 'Vehicle', burglary: 'Burglary', theft: 'Theft', harassment: 'Harassment',
  police: 'Police activity', vandalism: 'Vandalism', traffic: 'Traffic', court: 'Court',
};
const CHATTER_KINDS = [
  { id: 'all', label: 'All' }, { id: 'news', label: 'News' }, { id: 'reddit', label: 'Reddit' }, { id: 'dispatch', label: 'Dispatch' },
];

const $ = (id) => document.getElementById(id);

function el(tag, className, text, parent) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

// near: { x, y, r } when the page is narrowed to the area around one block: the block's point (as in
// hood.places) and the id of one of RADII. It is replaced, never changed in place.
const state = { beat: null, near: null, range: '365', sev: null, cat: null, mode: 'dots', chatterKind: 'all', chatterShown: 12 };
const data = { meta: null, city: null, beats: null, hoods: new Map(), feeds: new Map() };
let ctx = null;
let map = null;
let hoodInfo = null;      // Map(beat -> entry of meta.hoods)
// What is on screen. hood is what the page counts: the neighborhood's own file, or the part of it and
// its neighbors inside `area` (see loadArea). stories and calls are the unverified items on the map.
const current = {
  beat: null, view: null, drawn: false, hood: null, area: null, neighborhood: null, feed: null, stories: [], calls: [],
  mask: null, range: null,
};

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

// A neighborhood's unverified layers: news and Reddit stories, and police dispatch calls. Either
// is null when the pipeline has not produced it, and the rest of the page works without them.
async function loadFeed(beat) {
  if (!data.feeds.has(beat)) {
    const [chatter, dispatch] = await Promise.all(
      ['chatter', 'dispatch'].map((kind) => json(`data/${kind}/${beat}.json`).catch(() => null)));
    data.feeds.set(beat, { stories: chatter && chatter.stories, dispatch });
  }
  return data.feeds.get(beat);
}

// The page narrowed to the area around one block: every record within a distance of the block's
// point, from each neighborhood the circle reaches, in the shape of a hood (S.within), so the rest
// of the page reads it as it reads a neighborhood. Only the newest area is kept.
let lastArea = null;

async function loadArea(near) {
  const key = `${near.x}.${near.y}.${near.r}`;
  if (lastArea && lastArea.key === key) return lastArea;
  const radius = RADII.find((r) => r.id === near.r);
  const metres = radius.miles * METRES_PER_MILE;
  const ring = S.ring(near.x, near.y, metres);
  const lons = ring.map((point) => point[0]);
  const lats = ring.map((point) => point[1]);
  const bbox = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
  // every neighborhood whose bounding box the circle's touches; a few more files than needed, never fewer
  const beats = data.meta.hoods
    .filter((h) => h.bbox[0] <= bbox[2] && h.bbox[2] >= bbox[0] && h.bbox[1] <= bbox[3] && h.bbox[3] >= bbox[1])
    .map((h) => h.beat);
  const hood = S.within(await Promise.all(beats.map(loadHood)), near.x, near.y, metres);
  const centre = hood.places.find((p) => p[1] === near.x && p[2] === near.y);
  lastArea = {
    key, x: near.x, y: near.y, radius, metres, ring, bbox, beats, hood,
    label: (centre && centre[0]) || 'this spot',
    sq_mi: Math.PI * radius.miles ** 2,
  };
  return lastArea;
}

// ---- state <-> URL -------------------------------------------------------------------------------

function parseSet(text, n) {
  if (text === null) return null;
  const set = new Set(text.split('.').filter(Boolean).map(Number).filter((v) => Number.isInteger(v) && v >= 0 && v < n));
  return set.size === 0 || set.size === n ? null : set;
}

// Where the page is: the neighborhood, and the block an area is drawn around. Going somewhere else
// is a new step in the browser's history, so Back returns to where the reader was (a stray click on
// the map switches neighborhoods). The period, the filters, the map style and an area's distance
// are settings of a place, and changing one rewrites its step instead of adding another.
const whereKey = () => `${state.beat}|${state.near ? `${state.near.x}.${state.near.y}` : ''}`;
let urlPlace = null;      // the place the address bar names

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const beat = Number(p.get('b'));
  state.beat = hoodInfo.has(beat) ? beat : data.meta.home_beat;
  const a = /^(-?\d+)\.(-?\d+)\.(\d+)$/.exec(p.get('a') || '');      // x.y.radius
  state.near = a && RADII.some((r) => r.id === a[3]) ? { x: Number(a[1]), y: Number(a[2]), r: a[3] } : null;
  state.range = RANGES.some((r) => r.id === p.get('r')) ? p.get('r') : '365';
  state.sev = parseSet(p.get('s'), ctx.nSev);
  state.cat = parseSet(p.get('c'), ctx.nCat);
  state.mode = p.get('m') === 'heat' ? 'heat' : 'dots';
  urlPlace = whereKey();
}

function writeHash() {
  const p = new URLSearchParams();
  // An area is read against its own neighborhood, so that one is always named, opening one or not.
  if (state.beat !== data.meta.home_beat || state.near) p.set('b', state.beat);
  if (state.near) p.set('a', [state.near.x, state.near.y, state.near.r].join('.'));
  if (state.range !== '365') p.set('r', state.range);
  if (state.sev) p.set('s', [...state.sev].sort().join('.'));
  if (state.cat) p.set('c', [...state.cat].sort().join('.'));
  if (state.mode !== 'dots') p.set('m', state.mode);
  const hash = p.toString();
  const moved = whereKey() !== urlPlace;
  urlPlace = whereKey();
  history[moved ? 'pushState' : 'replaceState'](null, '', hash ? `#${hash}` : location.pathname + location.search);
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

/** The city as a place with residents, in the shape of an entry of meta.hoods (see S.perThousand). */
const cityPlace = () => ({ residents: data.meta.city_residents, rated: data.meta.city_residents !== null });

/** What the page is counting, in words: a neighborhood, or the area around one block. */
function subject() {
  const { name } = hoodInfo.get(state.beat);
  const { area } = current;
  return area
    ? { where: `within ${area.radius.label} of ${area.label}`, short: 'This area' }
    : { where: `in ${name}`, short: name };
}

/** Choose a neighborhood. That always means all of it, so an area around a block is let go. */
function goTo(beat) {
  state.beat = beat;
  state.near = null;
  return update();
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
    select.addEventListener('change', () => goTo(Number(select.value)));
  }
  select.value = state.beat;
  const { name } = hoodInfo.get(state.beat);
  const { area } = current;
  $('area').hidden = !area;
  if (area) {
    segmented($('radius'), RADII, area.radius.id, (id) => { state.near = { ...state.near, r: id }; update(); });
    $('area-of').textContent = `of ${area.label}`;
    $('area-of').title = `The page is counting reports within ${area.radius.label} of ${area.label}`;
    $('area-clear').title = `Back to all of ${name}`;
    $('area-clear').setAttribute('aria-label', `Back to all of ${name}`);
  }
  document.title = area ? `Near ${area.label}, ${name} · San Diego crime` : `${name} crime · San Diego`;

  // Only the date the reports run through. The city's files are up to two days behind on the day
  // it publishes them, so when this copy was made tells a reader nothing until the copy is old
  // enough that the city has newer ones. Then its age is said.
  const asof = $('asof');
  asof.textContent = `SDPD reports approved through ${F.date(S.dayToDate(ctx.today, data.meta.epoch))}`;
  const age = data.meta.checked_at ? S.daysSince(data.meta.checked_at) : 0;
  if (age >= STALE_DAYS) el('span', 'stale', ` · downloaded ${age} days ago`, asof);
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
  const home = hoodInfo.get(state.beat);
  const { area } = current;
  const year = S.compare(hood, mask, ctx, 365);
  const quarter = S.compare(hood, mask, ctx, 90);
  const cityYear = S.compareScope(data.city, 'city', 365, cmask);
  const homeYear = S.compareScope(data.city, String(state.beat), 365, cmask);     // all of the neighborhood
  const to = F.date(S.dayToDate(year.to, data.meta.epoch));
  const filtered = state.sev || state.cat ? ' (filtered)' : '';

  $('hero-label').textContent = `Reported offenses ${subject().where}${filtered}, 12 months to ${to}`;
  $('hero-value').textContent = F.int(year.cur);
  delta($('hero-delta'), year, 'than the 12 months before');
  const cityRate = S.perThousand(cityYear.cur, cityPlace());
  const rateLine = $('hero-rate');
  rateLine.replaceChildren();
  if (area) {
    // Nobody has counted an area's residents, but its size is known: what the neighborhood's own
    // count comes to for an area this size is a figure to read the headline against.
    rateLine.hidden = !home.sq_mi;
    if (home.sq_mi) {
      el('span', null, `Across ${home.name}, an area this size averages ${F.int((homeYear.cur / home.sq_mi) * area.sq_mi)}.`, rateLine);
    }
  } else {
    // The same count per resident, beside the city's rate and this neighborhood's place among all that have one.
    rateLine.hidden = home.residents === null;        // the pipeline had no census file
    const rate = S.perThousand(year.cur, home);
    if (rate !== null) {
      const rank = S.standing(rate, data.meta.hoods.map((h) => S.perThousand(S.compareScope(data.city, String(h.beat), 365, cmask).cur, h)));
      el('strong', null, `${F.rate(rate)} per 1,000 residents`, rateLine);
      el('span', null, `City as a whole: ${F.rate(cityRate)}. ${F.ordinal(rank.place)} highest of ${rank.of} neighborhoods.`, rateLine);
    } else if (home.residents !== null) {
      el('span', null, home.residents >= data.meta.min_residents
        ? 'No rate per resident: most of the people counted here are in one census block, so the count is too rough.'
        : `Too few residents (${F.int(home.residents)}) for a rate per resident.`, rateLine);
    }
  }
  const perWeek = year.cur / (365 / 7);
  $('hero-note').textContent = `About ${perWeek >= 10 ? F.int(perWeek) : perWeek.toFixed(1)} a week. `
    + `Comparisons stop ${data.meta.settle_days} days before the newest report, because recent reports are still arriving.`;

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
  tile(`Newest 30 days, to ${F.date(S.dayToDate(ctx.today, data.meta.epoch))}`, F.int(recent), null,
    'Still filling in. Reports take days to weeks to be approved, so this is not compared with anything yet.');
  // the next size up: the neighborhood for an area, the city for a neighborhood
  if (area) {
    const homeRate = S.perThousand(homeYear.cur, home);
    tile(`All of ${home.name}${filtered}, same 12 months`, F.int(homeYear.cur), (p) => delta(p, homeYear),
      homeRate === null ? null : `${F.rate(homeRate)} per 1,000 residents.`);
  } else {
    tile(`City of San Diego${filtered}, same 12 months`, F.int(cityYear.cur), (p) => delta(p, cityYear),
      cityRate === null ? null : `${F.rate(cityRate)} per 1,000 residents.`);
  }
}

function renderTrend(hood, mask, cmask) {
  const { short: name } = subject();
  const sumSeverities = (bySev) => ctx.months.map((_, m) => bySev.reduce((a, s) => a + s[m], 0));
  const bySev = S.monthly(hood, mask, ctx);
  const totals = sumSeverities(bySev);
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

  // Against the next size up (the city for a neighborhood, the neighborhood for an area), both as
  // 12-month running totals indexed to their first value.
  const hoodName = hoodInfo.get(state.beat).name;
  const wider = current.area
    ? { name: hoodName, words: hoodName, totals: sumSeverities(S.monthly(current.neighborhood, mask, ctx)) }
    : { name: 'City of San Diego', words: 'the city', totals: cityTotals };
  const running = (series) => series.map((_, i) => {
    if (i < 11 || i > last) return null;
    let sum = 0;
    for (let k = i - 11; k <= i; k++) sum += series[k];
    return sum;
  });
  const mine = S.indexTo100(running(totals));
  const theirs = S.indexTo100(running(wider.totals));
  $('index-title').textContent = `${name} compared with ${wider.words}`;
  const indexLegend = $('index-legend');
  indexLegend.replaceChildren();
  for (const [label, color] of [[name, 'var(--accent)'], [wider.name, 'var(--muted)']]) {
    const item = el('li', null, null, indexLegend);
    el('span', 'key-line', null, item).style.borderTopColor = color;
    el('span', null, label, item);
  }
  indexChart($('index-chart'), {
    months: ctx.months,
    series: [
      { name, color: 'var(--accent)', values: mine, strong: true },
      { name: wider.name, color: 'var(--muted)', values: theirs },
    ],
  });
  const firstYear = ctx.months[0].year;
  $('index-caption').textContent = mine[last] !== null && theirs[last] !== null
    ? `12-month running totals, with ${firstYear} set to 100. ${name} is now at ${F.int(mine[last])}; ${wider.words} is at ${F.int(theirs[last])}.`
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

// The unverified items that have a point on the map: the neighborhood's own stories and police
// calls, or in an area those of every neighborhood it reaches that fall inside it. A story listed
// under several neighborhoods (one that says "City Heights") is kept once.
function mapItems(feeds, area) {
  const inside = (x, y) => !area || S.distance(area.x, area.y, x, y) <= area.metres;
  const stories = new Map();
  for (const feed of feeds) {
    for (const s of feed.stories || []) if (s.where && inside(s.where.x, s.where.y)) stories.set(s.id, s);
  }
  const calls = feeds.flatMap((feed) => (feed.dispatch ? feed.dispatch.calls : [])).filter((c) => c.x !== null && inside(c.x, c.y));
  return { stories: [...stories.values()], calls };
}

// The police calls on the map in the period. A call's map point is its block's, so several calls
// can share one spot; `spot` is that point as "x,y".
function callsInRange(spot) {
  return current.calls.filter((c) => c.day > current.range.after && (!spot || `${c.x},${c.y}` === spot));
}

const callsSince = () => dayLabel(current.feed.dispatch.first, { year: false });

const plural = (n, word) => `${F.int(n)} ${word}${n === 1 ? '' : 's'}`;
const dayLabel = (iso, options) => F.date(new Date(`${iso}T00:00:00Z`), options);

function callList(calls) {
  const list = el('div', 'pop-list');
  for (const c of calls) {
    const item = el('div', 'pop-item', null, list);
    el('span', 'ring dot', null, item);
    const line = el('div', null, null, item);
    el('span', null, `${c.what} `, line);
    el('span', 'pop-date', `${dayLabel(c.date, { year: false })}, ${F.clock(c.time)}`, line);
    if (c.outcome) el('div', 'pop-code', c.outcome[0].toUpperCase() + c.outcome.slice(1), item);
  }
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
  // The way into an area: the page narrows to what is around this block (see loadArea).
  const { area } = current;
  if (!area || area.x !== place[1] || area.y !== place[2]) {
    const radius = area ? area.radius : RADII[0];
    const around = el('button', 'link pop-around', `See everything within ${radius.label} of this block`, node);
    around.type = 'button';
    around.addEventListener('click', () => { state.near = { x: place[1], y: place[2], r: radius.id }; update(); });
  }
  const reports = node.appendChild(incidentList(hood, indices));
  const calls = callsInRange(`${place[1]},${place[2]}`);
  if (calls.length) {
    reports.classList.add('short');                  // two lists have to fit inside the map
    el('div', 'pop-sub pop-more', `${plural(calls.length, 'police call')} here since ${callsSince()}, not confirmed crimes:`, node);
    node.appendChild(callList(calls)).classList.add('short');
  }
  map.popup(lngLat || [place[1] / 1e5, place[2] / 1e5], node);
}

function openCalls(spot, lngLat) {
  const calls = callsInRange(spot);
  if (!calls.length || !map) return;
  const node = el('div');
  el('div', 'pop-title', calls[0].place, node);
  el('div', 'pop-sub', `${plural(calls.length, 'police call')} since ${callsSince()}. A call is what someone reported, not a confirmed crime.`, node);
  node.appendChild(callList(calls));
  map.popup(lngLat || [calls[0].x / 1e5, calls[0].y / 1e5], node);
}

function openStory(id, lngLat) {
  const story = current.stories.find((s) => s.id === id);
  if (!story || !map) return;
  const node = el('div');
  el('div', 'pop-sub', `Unverified · ${story.outlet} · ${F.date(new Date(`${story.date}T00:00:00Z`))}`, node);
  const a = el('a', 'pop-title', story.title, node);
  a.href = story.url;
  a.target = '_blank';
  a.rel = 'noopener';
  el('div', 'pop-sub', `Placed at ${story.where.label}, from the wording of the post.`, node);
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
  const pins = current.stories.filter((s) => s.day > range.after && s.day <= range.through);
  const spots = new Map();                           // "x,y" -> a police call there
  for (const c of callsInRange()) spots.set(`${c.x},${c.y}`, c);
  if (map) {
    const point = (x, y, properties) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [x / 1e5, y / 1e5] }, properties });
    map.setPlaces({ type: 'FeatureCollection', features });
    map.setChatter({
      type: 'FeatureCollection',
      features: [
        ...[...spots].map(([spot, c]) => point(c.x, c.y, { id: `calls:${spot}`, dispatch: true })),
        ...pins.map((s) => point(s.where.x, s.where.y, { id: s.id })),
      ],
    });
    map.setMode(state.mode);
  }
  const note = $('map-note');
  const { area } = current;
  if (area) {
    note.textContent = `${plural(total, 'report')} ${range.phrase} within ${area.radius.label} of ${area.label}. `
      + 'A report counts when its block is inside the circle, so one with no usable address is left out. Click a circle for its reports. ';
    const back = el('button', 'link', `Show all of ${hoodInfo.get(state.beat).name}`, note);
    back.type = 'button';
    back.addEventListener('click', () => goTo(state.beat));
  } else {
    note.textContent = total
      ? `${F.int(mapped)} of ${F.int(total)} reports ${range.phrase} are on the map. `
        + `${mapped < total ? 'The rest have no usable address. ' : ''}Circles sit on the block, not the exact address. `
        + 'Click one for its reports and for the area around it, or another neighborhood to switch to it.'
      : 'Nothing in this period matches the filters.';
  }

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
  if (spots.size) {
    const row = el('div', 'row', null, legend);
    el('span', 'ring dot', null, row);
    el('span', null, `Police call since ${callsSince()}, unverified`, row);
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
  const counted = data.meta.city_residents !== null;        // false when the pipeline had no census file
  const dash = { text: '–', dim: true };
  // the two per-resident cells of a row, left off the table altogether when nobody was counted
  const people = (n, place) => {
    const rate = S.perThousand(n, place);
    return counted ? [rate === null ? dash : F.rate(rate), F.int(place.residents)] : [];
  };
  const row = (h) => {
    const scope = String(h.beat);
    const cmp = S.compareScope(data.city, scope, 365, cmask);
    const change = el('span');
    delta(change, cmp, '');
    let nameCell = h.name;
    if (h.beat !== state.beat) {
      nameCell = el('button', 'row-button', h.name);
      nameCell.type = 'button';
      nameCell.addEventListener('click', () => { goTo(h.beat); window.scrollTo({ top: 0, behavior: 'smooth' }); });
    }
    return {
      sort: cmp.cur,
      className: h.beat === state.beat ? 'is-home' : null,
      cells: [nameCell, F.int(cmp.cur), ...people(cmp.cur, h), h.sq_mi ? F.int(cmp.cur / h.sq_mi) : dash,
        F.int(S.compareScope(data.city, scope, 365, highMask).cur), change],
    };
  };
  const neighbors = home.neighbors.map((b) => hoodInfo.get(b)).filter(Boolean).map(row).sort((a, b) => b.sort - a.sort);
  const cityCmp = S.compareScope(data.city, 'city', 365, cmask);
  const cityChange = el('span');
  delta(cityChange, cityCmp, '');
  const cityRow = {
    cells: ['City of San Diego', F.int(cityCmp.cur), ...people(cityCmp.cur, cityPlace()), dash,
      F.int(S.compareScope(data.city, 'city', 365, highMask).cur), cityChange],
  };
  table($('nearby'),
    [{ label: 'Neighborhood' }, { label: 'Offenses', num: true },
      ...(counted ? [{ label: 'Per 1,000 residents', num: true }, { label: 'Residents', num: true }] : []),
      { label: 'Per sq. mile', num: true }, { label: 'High severity', num: true }, { label: 'Change from the year before' }],
    [row(home), ...neighbors, cityRow]);
  $('nearby-sub').textContent = `12 months to ${F.date(S.dayToDate(ctx.settled, data.meta.epoch))}, with the filters above. `
    + (counted
      ? `Residents are the ${data.meta.census_year} Census count; under ${F.int(data.meta.min_residents)} there is no rate. `
        + 'Where many people visit (shops, bars, a beach) the rate runs high, because most of the people there live somewhere else.'
      : 'Busy commercial areas draw more reports than their size suggests; there are no population figures here.');
}

/** Scroll to a spot on the map and open what is there, first widening the period or leaving the area if either hides the pin. */
async function findOnMap(day, x, y, open) {
  const { area } = current;
  const tooOld = !(day > current.range.after);
  const outside = area && S.distance(area.x, area.y, x, y) > area.metres;
  if (tooOld) state.range = 'all';
  if (outside) state.near = null;
  if (tooOld || outside) await update();
  showOnMap([x / 1e5, y / 1e5], open);
}

function feedStory(li, s) {
  const meta = el('div', 'feed-meta', null, li);
  el('span', null, dayLabel(s.date), meta);
  el('span', null, s.more.length ? `${s.outlet} +${s.more.length} more` : s.outlet, meta);
  if (s.area) el('span', null, s.area, meta).title = `The story says ${s.area}, the wider area this neighborhood is part of.`;
  for (const t of s.tags) el('span', 'tag', TAGS[t] || t, meta);
  const a = el('a', 'feed-title', s.title, li);
  a.href = s.url;
  a.target = '_blank';
  a.rel = 'noopener';
  if (s.excerpt) el('p', 'feed-excerpt', s.excerpt, li);
  if (s.where && map) {
    const b = el('button', 'link', `On the map: ${s.where.label}`, li);
    b.type = 'button';
    b.addEventListener('click', () => findOnMap(s.day, s.where.x, s.where.y, () => openStory(s.id)));
  }
}

/** One day's police calls as a single feed entry; a call's block is a button when it is on the map. */
function feedCalls(li, date, calls) {
  const meta = el('div', 'feed-meta', null, li);
  el('span', null, dayLabel(date), meta);
  el('span', null, 'SDPD dispatch', meta);
  for (const t of new Set(calls.map((c) => c.tag))) el('span', 'tag', TAGS[t] || t, meta);
  el('div', 'feed-title', plural(calls.length, 'police call'), li);
  const rows = el('ul', 'calls', null, li);
  for (const c of calls) {
    const row = el('li', null, null, rows);
    el('span', 'call-time', F.clock(c.time), row);
    const what = el('span', null, `${c.what} · `, row);
    if (c.x !== null && map) {
      const b = el('button', 'link', c.place, what);
      b.type = 'button';
      b.addEventListener('click', () => findOnMap(c.day, c.x, c.y, () => openCalls(`${c.x},${c.y}`)));
    } else {
      el('span', null, c.place, what);
    }
    if (c.outcome) el('span', 'call-outcome', ` · ${c.outcome}`, what);
  }
}

function renderChatter() {
  const section = $('chatter-section');
  const list = $('chatter');
  const more = $('chatter-more');
  list.replaceChildren();
  $('chatter-filter').replaceChildren();
  const { dispatch } = current.feed;
  const stories = current.feed.stories || [];
  section.hidden = !current.feed.stories && !dispatch;      // the pipeline has collected neither
  const days = new Map();                            // date -> that day's police calls, newest first
  for (const c of dispatch ? dispatch.calls : []) {
    if (!days.has(c.date)) days.set(c.date, []);
    days.get(c.date).push(c);
  }
  if (!stories.length && !days.size) {
    more.hidden = true;
    el('li', 'feed-excerpt', `Nothing for ${hoodInfo.get(state.beat).name}. No collected news story or Reddit post names it`
      + `${dispatch ? `, and police logged no call about a possible crime there in the ${dispatch.days} days to ${dayLabel(dispatch.through)}` : ''}.`, list);
    return;
  }
  const everything = [
    ...stories.map((s) => ({ kind: s.kind, date: s.date, story: s })),
    ...[...days].map(([date, calls]) => ({ kind: 'dispatch', date, calls })),
  ];
  // Offer only the kinds this neighborhood has, and no choice at all when it has just one.
  const kinds = CHATTER_KINDS.filter((k) => k.id === 'all' || everything.some((e) => e.kind === k.id));
  if (!kinds.some((k) => k.id === state.chatterKind)) state.chatterKind = 'all';
  if (kinds.length > 2) {
    segmented(el('div', 'segmented', null, $('chatter-filter')), kinds, state.chatterKind, (id) => {
      state.chatterKind = id;
      state.chatterShown = 12;
      renderChatter();
    });
  }
  // Newest day first. A day's police calls are one entry, after that day's stories.
  const entries = everything.filter((e) => state.chatterKind === 'all' || e.kind === state.chatterKind)
    .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : (a.kind === 'dispatch') - (b.kind === 'dispatch')));
  for (const e of entries.slice(0, state.chatterShown)) {
    const li = el('li', null, null, list);
    if (e.story) feedStory(li, e.story);
    else feedCalls(li, e.date, e.calls);
  }
  more.hidden = entries.length <= state.chatterShown;
  more.textContent = `Show more (${F.int(entries.length - state.chatterShown)} left)`;
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
    const through = F.date(S.dayToDate(ctx.today, meta.epoch));
    el('p', null, 'The city rewrites its files every morning, and how far behind they are varies: the newest report in them is sometimes from the night before and sometimes two days old. '
      + (meta.checked_at
        ? `This page’s copy was downloaded on ${F.date(F.localDay(new Date(meta.checked_at)))}, and the newest report in it was approved on ${through}. `
        : `The newest report in this page’s copy was approved on ${through}. `)
      + 'Approval itself comes days to weeks after an offense, so the most recent weeks are always still filling in.', d);
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
  block('The area around one block', (d) => {
    el('p', null, 'A block’s pop-up on the map can narrow the whole page to a quarter or half mile around that block, across neighborhood lines. '
      + 'A report counts when the point of its block falls inside the circle, so the edge is only as exact as a block. '
      + `The ${(100 - qa.mapped_pct).toFixed(1)}% of offenses with no usable address cannot be placed and are left out, so an area’s counts run slightly low beside a neighborhood’s. `
      + 'Nobody has counted the residents of such an area. In place of a rate per resident, its count is set beside what the neighborhood’s own count comes to for an area that size.', d);
    el('p', null, 'The unverified posts and police calls on the map are then the ones inside the circle. The list under “What people are saying” stays the whole neighborhood’s.', d);
  });
  if (meta.city_residents !== null) {
    block('Rates per 1,000 residents', (d) => {
      el('p', null, `Residents are the ${meta.census_year} Census count: ${F.int(meta.city_residents)} people across the city. The census counts by block, and a block belongs to the neighborhood its centre falls in. `
        + `People living in barracks or on ships, in jails and in college dorms (${F.int(qa.census_left_out)}) are left out, because crime there is mostly recorded by another agency (the Navy, the Sheriff, campus police) and is not in SDPD’s reports.`, d);
      el('p', null, 'A rate divides the offenses reported in a neighborhood by the people who live there, whoever the victim was. '
        + 'Where many people come to work, shop, drink or swim (downtown, Mission Valley, Old Town, the beaches) it runs far above anything a resident experiences, so compare like with like. '
        + `A neighborhood with fewer than ${F.int(meta.min_residents)} residents gets no rate at all; most of those are parks, or stadium and shopping districts. `
        + 'Nor does one where a single census block holds more than half the residents, because the count then hangs on which side of the line that block’s centre falls.', d);
      el('p', null, `The count is from ${meta.census_year}. Where a lot of housing has been built since, more people live there now and the true rate is lower than the one shown.`, d);
    });
  }
  block('The outlook', (d) => {
    el('p', null, 'The dashed columns take the average of the last 12 fully reported months and adjust it for the time of year, using the citywide seasonal pattern. '
      + 'The range is where 9 in 10 past months actually landed when the same rule was applied to this neighborhood’s own history. It says what “no change” would look like; it does not predict events.', d);
  });
  block('News, Reddit and police dispatch', (d) => {
    el('p', null, 'Headlines come from Google News and local outlets’ own feeds, and posts from Reddit’s public feeds, kept when they name the neighborhood (or were posted in its own subreddit) and read like a crime or police-activity report. '
      + 'A story that names a wider area, such as City Heights or Clairemont, is listed under every neighborhood in that area. '
      + 'A simple keyword filter makes these calls, so expect some misses and a few wrong picks, more of them for neighborhoods whose names are also ordinary words or places elsewhere. '
      + 'Posts are unverified, a post is placed on the map only when it names an intersection or block, '
      + 'and the number of posts says nothing about the amount of crime. No usernames are stored.', d);
    callsNote = el('p', null, null, d);
  });
}

let callsNote = null;     // the one paragraph of the method section that is about the neighborhood on screen

function renderCallsNote() {
  const calls = current.feed.dispatch;
  callsNote.hidden = !calls;
  if (!calls) return;
  callsNote.textContent = `Police calls come from SDPD’s dispatch log (calls for service), which the city publishes about two days behind. This page lists the ${calls.days} days to ${dayLabel(calls.through)} `
    + `for ${hoodInfo.get(calls.beat).name}: ${F.int(calls.calls.length)} of the ${F.int(calls.logged)} calls logged there. The rest were about something other than a possible crime `
    + '(noise, parking, welfare checks, traffic stops, alarms) or were cancelled, duplicates or unfounded. '
    + 'A call is what someone told a dispatcher, not a confirmed crime, and most end without a report. “Reported afterwards” means the caller found out later instead of seeing it happen. '
    + 'Calls are never added to the counts on this page, and their place on the map is the block or intersection, not the address.';
}

// ---- main loop -----------------------------------------------------------------------------------

async function update() {
  const main = $('main');
  main.setAttribute('aria-busy', 'true');
  hideTip();
  map?.closePopup();          // its contents were built for the previous filters
  writeHash();
  const { beat, near } = state;
  const beatChanged = current.beat !== beat;
  const [neighborhood, feed, area] = await Promise.all([loadHood(beat), loadFeed(beat), near ? loadArea(near) : null]);
  const feeds = area ? await Promise.all(area.beats.map(loadFeed)) : [feed];
  if (beat !== state.beat || near !== state.near) return;      // a newer selection overtook this one
  const hood = area ? area.hood : neighborhood;
  const view = area ? area.key : String(beat);                  // what the map is framed on
  const viewChanged = current.view !== view;
  Object.assign(current, { feed, area, neighborhood, ...mapItems(feeds, area) });
  if (beatChanged) state.chatterShown = 12;

  const mask = S.offenseMask(ctx, state.cat, state.sev);
  const cmask = S.cellMask(ctx, state.cat, state.sev);
  renderHeader();
  renderFilters();
  renderSummary(hood, mask, cmask);
  renderTrend(hood, mask, cmask);
  renderExplore(hood, mask);
  if (map && viewChanged) {
    map.setBeat(beat, area ? area.bbox : hoodInfo.get(beat).bbox, current.drawn);
    map.setArea(area ? area.ring : null);
  }
  Object.assign(current, { beat, view, drawn: true });
  renderNearby(cmask);
  renderChatter();
  renderMethod();
  renderCallsNote();
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
  ctx = S.context(data.meta);
  hoodInfo = new Map(data.meta.hoods.map((h) => [h.beat, h]));
  readHash();

  $('boot').remove();
  for (const s of document.querySelectorAll('main > section')) s.hidden = false;
  map = createMap($('map'), {
    beats: data.beats,
    sevColors: SEV_COLORS,
    chatterColor: CHATTER_COLOR,
    onBeat: goTo,
    onPlace: (p, lngLat) => openPlace(p, lngLat),
    onChatter: (id, lngLat) => (id.startsWith('calls:') ? openCalls(id.slice(6), lngLat) : openStory(id, lngLat)),
    onHoverBeat: (name) => {
      $('map-hover').hidden = !name;
      if (name) $('map-hover').textContent = `${name} · click to switch`;
    },
  });

  $('reset').addEventListener('click', () => { state.sev = null; state.cat = null; update(); });
  $('area-clear').addEventListener('click', () => goTo(state.beat));
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
