// Every number on the dashboard is computed here, from the JSON the pipeline writes.
// Pure functions with no DOM access, so the same file is imported by the tests (tests/js).
//
// A "hood" is one data/hood/<beat>.json file: parallel arrays sorted by day
//   day[i]   day the offense occurred, counted from meta.epoch
//   lag[i]   days from the offense to the report being approved
//   off[i]   index into meta.offenses (which gives category and severity)
//   place[i] index into hood.places ([label, x, y]), or -1
//   sec[i]   index into hood.sections (the code sections on the report, as text)
// A "mask" is a Uint8Array over meta.offenses with 1 for the offense types the filters keep.

const DAY_MS = 86400000;

export function dayNumber(iso, epochIso) {
  return Math.round((Date.parse(iso + 'T00:00:00Z') - Date.parse(epochIso + 'T00:00:00Z')) / DAY_MS);
}

export function dayToDate(day, epochIso) {
  return new Date(Date.parse(epochIso + 'T00:00:00Z') + day * DAY_MS);
}

/** Whole calendar days from a moment (an ISO timestamp) to now, on the reader's own clock. */
export function daysSince(iso, now = new Date()) {
  const midnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((midnight(now) - midnight(new Date(iso))) / DAY_MS);
}

/** Everything derived from meta.json that the other functions need. */
export function context(meta) {
  const epochMs = Date.parse(meta.epoch + 'T00:00:00Z');
  const today = dayNumber(meta.data_through, meta.epoch);   // last day that has approved reports
  const settled = today - meta.settle_days;                 // comparisons never look past this day
  const end = new Date(epochMs + today * DAY_MS);
  const months = [];
  const dayMonth = new Int16Array(today + 1);
  let y = new Date(epochMs).getUTCFullYear();
  let m = new Date(epochMs).getUTCMonth();
  while (y < end.getUTCFullYear() || (y === end.getUTCFullYear() && m <= end.getUTCMonth())) {
    const start = Math.round((Date.UTC(y, m, 1) - epochMs) / DAY_MS);
    const last = Math.round((Date.UTC(y, m + 1, 1) - epochMs) / DAY_MS) - 1;
    // final: fully reported for practical purposes; provisional: reports still arriving;
    // partial: the calendar month is not over yet.
    const status = last <= settled ? 'final' : last <= today ? 'provisional' : 'partial';
    for (let d = Math.max(start, 0); d <= Math.min(last, today); d++) dayMonth[d] = months.length;
    months.push({ year: y, month: m, start, end: last, status });
    if (++m === 12) { m = 0; y++; }
  }
  return {
    meta, today, settled, year: meta.year_days, months, dayMonth,
    nCat: meta.categories.length,
    nSev: meta.severities.length,
    offCat: meta.offenses.map((o) => o.cat),
    offSev: meta.offenses.map((o) => o.sev),
  };
}

/** cats / sevs: Sets of category and severity indices to keep (null keeps all). */
export function offenseMask(ctx, cats, sevs) {
  return Uint8Array.from(ctx.meta.offenses, (o) => ((!cats || cats.has(o.cat)) && (!sevs || sevs.has(o.sev)) ? 1 : 0));
}

/** The same filter over the pipeline's (category, severity) cells, for citywide tables. */
export function cellMask(ctx, cats, sevs) {
  const mask = new Uint8Array(ctx.nCat * ctx.nSev);
  for (let c = 0; c < ctx.nCat; c++) {
    for (let s = 0; s < ctx.nSev; s++) {
      mask[c * ctx.nSev + s] = (!cats || cats.has(c)) && (!sevs || sevs.has(s)) ? 1 : 0;
    }
  }
  return mask;
}

function lowerBound(arr, value) {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < value) lo = mid + 1; else hi = mid;
  }
  return lo;
}

/** Index range [a, b) of the records with after < day <= through. */
export function span(hood, after, through) {
  return [lowerBound(hood.day, after + 1), lowerBound(hood.day, through + 1)];
}

export function count(hood, mask, after, through) {
  const [a, b] = span(hood, after, through);
  let n = 0;
  for (let i = a; i < b; i++) n += mask[hood.off[i]];
  return n;
}

/** Like count(), but only reports that had been approved by day `asOf`. */
export function countAsOf(hood, mask, after, through, asOf) {
  const [a, b] = span(hood, after, through);
  let n = 0;
  for (let i = a; i < b; i++) if (mask[hood.off[i]] && hood.day[i] + hood.lag[i] <= asOf) n++;
  return n;
}

/**
 * Is the difference between two counts more than chance? Counts this size wobble by roughly their
 * square root, so a change is only called "up" or "down" when it is at least twice that wobble
 * and at least 5%. Otherwise it is "flat" (no clear change) or "few" (too few to say).
 */
export function judge(cur, prior) {
  const change = prior > 0 ? cur / prior - 1 : null;
  if (change === null || cur + prior < 30) return { change, verdict: 'few' };
  const z = (cur - prior) / Math.sqrt(cur + prior);
  if (Math.abs(z) >= 2 && Math.abs(change) >= 0.05) return { change, verdict: change < 0 ? 'down' : 'up' };
  return { change, verdict: 'flat' };
}

/**
 * Year-over-year comparison for a window of `windowDays`.
 * The window ends meta.settle_days before the newest data, and the year-earlier window counts only
 * reports that had been approved by the same point a year ago. See pipeline/build.py (SETTLE_DAYS)
 * for why; the pipeline computes the same figures for every neighborhood and the city.
 */
export function compare(hood, mask, ctx, windowDays) {
  const to = ctx.settled;
  const from = to - windowDays;
  const cur = count(hood, mask, from, to);
  const prior = countAsOf(hood, mask, from - ctx.year, to - ctx.year, ctx.today - ctx.year);
  return { cur, prior, from: from + 1, to, ...judge(cur, prior) };
}

/** The pipeline's precomputed comparison for a beat (or 'city'), under the same filters. */
export function compareScope(city, scope, windowDays, cmask) {
  const entry = city.compare[scope] && city.compare[scope][windowDays];
  if (!entry) return { cur: 0, prior: 0, ...judge(0, 0) };
  let cur = 0;
  let prior = 0;
  for (let c = 0; c < cmask.length; c++) {
    if (cmask[c]) { cur += entry.cur[c]; prior += entry.prior[c]; }
  }
  return { cur, prior, ...judge(cur, prior) };
}

/**
 * Offenses for every 1,000 residents of a place ({ residents, rated }, as in meta.hoods). Null
 * where the pipeline gives the place no rate: few people live in a park or a stadium district,
 * nearly everyone there is a visitor, and dividing by the residents says nothing.
 */
export function perThousand(n, place) {
  return place.rated && place.residents ? (n / place.residents) * 1000 : null;
}

/** Where a rate stands among all the rates there are (nulls are skipped): place 1 is the highest. */
export function standing(rate, rates) {
  const known = rates.filter((r) => r !== null);
  return { place: 1 + known.filter((r) => r > rate).length, of: known.length };
}

/** Per category: this window vs the year before, compared the same way as compare(). */
export function compareByCategory(hood, mask, ctx, windowDays) {
  const to = ctx.settled;
  const from = to - windowDays;
  const cur = new Array(ctx.nCat).fill(0);
  const prior = new Array(ctx.nCat).fill(0);
  let [a, b] = span(hood, from, to);
  for (let i = a; i < b; i++) if (mask[hood.off[i]]) cur[ctx.offCat[hood.off[i]]]++;
  [a, b] = span(hood, from - ctx.year, to - ctx.year);
  const asOf = ctx.today - ctx.year;
  for (let i = a; i < b; i++) {
    if (mask[hood.off[i]] && hood.day[i] + hood.lag[i] <= asOf) prior[ctx.offCat[hood.off[i]]]++;
  }
  return cur.map((n, c) => ({ cur: n, prior: prior[c], ...judge(n, prior[c]) }));
}

/** Monthly counts split by severity: result[severity][month]. */
export function monthly(hood, mask, ctx) {
  const out = Array.from({ length: ctx.nSev }, () => new Array(ctx.months.length).fill(0));
  const [, b] = span(hood, -1, ctx.today);
  for (let i = 0; i < b; i++) {
    const o = hood.off[i];
    if (mask[o] && hood.day[i] >= 0) out[ctx.offSev[o]][ctx.dayMonth[hood.day[i]]]++;
  }
  return out;
}

/** Citywide monthly totals under the filters, from city.json. */
export function cityMonthly(city, cmask, nMonths) {
  const out = new Array(nMonths).fill(0);
  for (let c = 0; c < cmask.length; c++) {
    if (!cmask[c]) continue;
    const row = city.monthly[c];
    for (let m = 0; m < nMonths; m++) out[m] += row[m] || 0;
  }
  return out;
}

/** Index of the last month that is fully reported (-1 if none). */
export function lastFinalMonth(ctx) {
  let last = -1;
  ctx.months.forEach((mo, i) => { if (mo.status === 'final') last = i; });
  return last;
}

/** Mean of the trailing `n` months, for each month from n-1 to `through`; null elsewhere. */
export function trailingMean(totals, n, through) {
  const out = new Array(totals.length).fill(null);
  let sum = 0;
  for (let i = 0; i <= through && i < totals.length; i++) {
    sum += totals[i];
    if (i >= n) sum -= totals[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

/** Rescale a series so its first non-null value is 100. */
export function indexTo100(series) {
  const base = series.find((v) => v !== null && v > 0);
  return series.map((v) => (v === null || !base ? null : (v / base) * 100));
}

/**
 * How each calendar month usually compares with an average month (1.0 = average), from the
 * citywide totals of the most recent complete, fully reported calendar years (up to four).
 */
export function seasonalIndex(totals, ctx) {
  const years = [];
  for (let i = 0; i + 11 < ctx.months.length; i++) {
    if (ctx.months[i].month === 0 && ctx.months[i + 11].status === 'final') years.push(i);
  }
  const use = years.slice(-4);
  const index = new Array(12).fill(1);
  if (!use.length) return index;
  for (let m = 0; m < 12; m++) {
    let sum = 0;
    for (const start of use) {
      const yearMean = totals.slice(start, start + 12).reduce((a, v) => a + v, 0) / 12;
      sum += yearMean > 0 ? totals[start + m] / yearMean : 1;
    }
    index[m] = sum / use.length;
  }
  return index;
}

/**
 * A rough expectation for the months ahead: the average of the last 12 fully reported months,
 * nudged by the usual seasonal pattern. It is an "if nothing changes" yardstick, not a prediction.
 *
 * The range is measured rather than assumed: the same rule is replayed over the series' own
 * history (forecasting each past month from the 12 months ending `GAP` months before it), and the
 * range is where 9 of 10 of those months actually landed relative to the estimate.
 */
export function outlook(totals, index, ctx, nMonths = 3) {
  const GAP = 4;
  const last = lastFinalMonth(ctx);
  if (last < 11) return [];
  const mean12 = (end) => { let s = 0; for (let i = end - 11; i <= end; i++) s += totals[i]; return s / 12; };
  const level = mean12(last);
  if (level < 3) return [];          // a couple of reports a month is too little to project

  const ratios = [];
  for (let m = 11 + GAP; m <= last; m++) {
    const expected = mean12(m - GAP) * index[ctx.months[m].month];
    if (expected > 0) ratios.push(totals[m] / expected);
  }
  ratios.sort((a, b) => a - b);
  const quantile = (q) => ratios[Math.min(ratios.length - 1, Math.max(0, Math.round(q * (ratios.length - 1))))];
  // With little history, fall back to a generous allowance for chance (variance 3x a Poisson count).
  const band = ratios.length >= 24 ? [quantile(0.05), quantile(0.95)] : null;

  const out = [];
  let { year: y, month: m } = ctx.months[ctx.months.length - 1];
  for (let k = 0; k < nMonths; k++) {
    if (++m === 12) { m = 0; y++; }
    const expected = level * index[m];
    const spread = 1.64 * Math.sqrt(expected * 3);
    out.push({
      year: y, month: m, expected,
      low: band ? expected * band[0] : Math.max(0, expected - spread),
      high: band ? expected * band[1] : expected + spread,
    });
  }
  return out;
}

/** Counts per place (hundred-block) in a range: Map(placeIndex -> { n, sev: [high, medium, low] }). */
export function placeCounts(hood, mask, ctx, after, through) {
  const out = new Map();
  const [a, b] = span(hood, after, through);
  for (let i = a; i < b; i++) {
    const o = hood.off[i];
    const p = hood.place[i];
    if (!mask[o] || p < 0) continue;
    let e = out.get(p);
    if (!e) out.set(p, (e = { n: 0, sev: new Array(ctx.nSev).fill(0) }));
    e.n++;
    e.sev[ctx.offSev[o]]++;
  }
  return out;
}

export function countByCategory(hood, mask, ctx, after, through) {
  const out = new Array(ctx.nCat).fill(0);
  const [a, b] = span(hood, after, through);
  for (let i = a; i < b; i++) if (mask[hood.off[i]]) out[ctx.offCat[hood.off[i]]]++;
  return out;
}

export function countByOffense(hood, mask, ctx, after, through) {
  const out = new Array(ctx.meta.offenses.length).fill(0);
  const [a, b] = span(hood, after, through);
  for (let i = a; i < b; i++) if (mask[hood.off[i]]) out[hood.off[i]]++;
  return out;
}

/** Record indices in a range, newest first, optionally only one place. */
export function records(hood, mask, after, through, { place = null, limit = Infinity } = {}) {
  const out = [];
  const [a, b] = span(hood, after, through);
  for (let i = b - 1; i >= a && out.length < limit; i--) {
    if (mask[hood.off[i]] && (place === null || hood.place[i] === place)) out.push(i);
  }
  return out;
}

// ---- the area around a point ---------------------------------------------------------------------
// A point is x, y in 1e-5 degrees, as in hood.places. Over a mile or so the ground is flat enough for
// a fixed number of metres per degree, the same figures pipeline/build.py uses for a beat's area.

const M_PER_Y = 1.1095;                                                   // metres per 1e-5 degree of latitude
const mPerX = (y) => 1.1132 * Math.cos((y / 1e5) * (Math.PI / 180));      // and of longitude, at latitude y

/** Metres between two points. */
export function distance(x1, y1, x2, y2) {
  return Math.hypot((x2 - x1) * mPerX((y1 + y2) / 2), (y2 - y1) * M_PER_Y);
}

/** The circle of `metres` around a point as a closed ring of [longitude, latitude], for the map. */
export function ring(x, y, metres, steps = 72) {
  const out = [];
  for (let i = 0; i <= steps; i++) {
    const angle = ((i % steps) / steps) * 2 * Math.PI;
    out.push([(x + (metres * Math.cos(angle)) / mPerX(y)) / 1e5, (y + (metres * Math.sin(angle)) / M_PER_Y) / 1e5]);
  }
  return out;
}

/**
 * The records within `metres` of a point, from every hood the circle reaches, as one hood: the same
 * parallel arrays, sorted by day, so each function above reads an area as it reads a neighborhood.
 * A record is inside when its block's point is. One with no point cannot be placed and is left out.
 */
export function within(hoods, x, y, metres) {
  const places = [];
  const sections = [];
  const placeAt = new Map();        // "x,y" -> index in places; a block on a boundary is in two hoods
  const sectionAt = new Map();
  const rows = [];
  for (const hood of hoods) {
    const moved = hood.places.map(([label, px, py]) => {
      if (px === null || distance(x, y, px, py) > metres) return -1;
      const key = `${px},${py}`;
      if (!placeAt.has(key)) {
        placeAt.set(key, places.length);
        places.push([label, px, py]);
      }
      return placeAt.get(key);
    });
    for (let i = 0; i < hood.day.length; i++) {
      const place = hood.place[i] < 0 ? -1 : moved[hood.place[i]];
      if (place < 0) continue;
      const text = hood.sections[hood.sec[i]];
      if (!sectionAt.has(text)) {
        sectionAt.set(text, sections.length);
        sections.push(text);
      }
      rows.push([hood.day[i], hood.lag[i], hood.off[i], place, sectionAt.get(text)]);
    }
  }
  rows.sort((a, b) => a[0] - b[0]);
  const column = (k) => rows.map((r) => r[k]);
  return { places, sections, day: column(0), lag: column(1), off: column(2), place: column(3), sec: column(4) };
}
