// Tests for site/js/stats.js. Run with:  node --test tests/js/
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import * as S from '../../site/js/stats.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

// A small world: 2 categories x 3 severities, data through 2022-03-15.
function meta(overrides = {}) {
  return {
    epoch: '2020-01-01',
    data_through: '2022-03-15',
    settle_days: 30,
    year_days: 365,
    windows: [90, 365],
    categories: [{ id: 'violent', label: 'Violent' }, { id: 'theft', label: 'Theft' }],
    severities: [{ id: 'high', label: 'High' }, { id: 'medium', label: 'Medium' }, { id: 'low', label: 'Low' }],
    offenses: [
      { code: '13A', label: 'Aggravated assault', cat: 0, sev: 0 },
      { code: '23H', label: 'Other theft', cat: 1, sev: 2 },
      { code: '240', label: 'Vehicle theft', cat: 1, sev: 1 },
    ],
    ...overrides,
  };
}

const day = (iso) => S.dayNumber(iso, '2020-01-01');

/** records: [isoDate, lagDays, offenseIndex, placeIndex] in any order */
function hood(records, places = []) {
  const rows = records.map(([iso, lag = 0, off = 1, place = -1]) => ({ d: day(iso), lag, off, place }))
    .sort((a, b) => a.d - b.d);
  return {
    places,
    day: rows.map((r) => r.d), lag: rows.map((r) => r.lag), off: rows.map((r) => r.off), place: rows.map((r) => r.place),
  };
}

test('context: day numbers, months and their reporting status', () => {
  const ctx = S.context(meta());
  assert.equal(ctx.today, day('2022-03-15'));
  assert.equal(ctx.settled, day('2022-02-13'));
  assert.equal(ctx.months.length, 27);                       // Jan 2020 .. Mar 2022
  assert.deepEqual(ctx.months.slice(-3).map((m) => m.status), ['final', 'provisional', 'partial']);
  assert.equal(ctx.dayMonth[0], 0);
  assert.equal(ctx.dayMonth[day('2020-02-29')], 1);          // leap day belongs to February
  assert.equal(ctx.dayMonth[day('2020-03-01')], 2);
  assert.equal(ctx.dayMonth[ctx.today], 26);
  assert.equal(S.lastFinalMonth(ctx), 24);                   // January 2022
  assert.equal(S.dayToDate(day('2021-07-04'), '2020-01-01').toISOString().slice(0, 10), '2021-07-04');
});

test('count: the range excludes its first day and includes its last', () => {
  const ctx = S.context(meta());
  const h = hood([['2022-01-01'], ['2022-01-02'], ['2022-01-31'], ['2022-02-01']]);
  const all = S.offenseMask(ctx, null, null);
  assert.equal(S.count(h, all, day('2022-01-01'), day('2022-01-31')), 2);
  assert.equal(S.count(h, all, day('2021-12-31'), day('2022-02-01')), 4);
  assert.equal(S.count(h, all, day('2022-02-01'), day('2022-03-01')), 0);
});

test('filters: masks follow category and severity selections', () => {
  const ctx = S.context(meta());
  assert.deepEqual([...S.offenseMask(ctx, null, null)], [1, 1, 1]);
  assert.deepEqual([...S.offenseMask(ctx, new Set([1]), null)], [0, 1, 1]);
  assert.deepEqual([...S.offenseMask(ctx, new Set([1]), new Set([1]))], [0, 0, 1]);
  assert.deepEqual([...S.cellMask(ctx, new Set([1]), new Set([1]))], [0, 0, 0, 0, 1, 0]);
});

test('compare: the window stops short of today and last year counts only what had arrived', () => {
  const ctx = S.context(meta());
  // settled window of 90 days: 2021-11-16 .. 2022-02-13
  const h = hood([
    ['2022-02-13', 0], ['2022-01-10', 3], ['2021-11-16', 1],        // in the window (3)
    ['2022-02-14', 0], ['2022-03-10', 0], ['2021-11-15', 0],        // just outside it
    ['2021-02-13', 1], ['2020-12-01', 20],                          // a year earlier, approved in time (2)
    ['2021-02-10', 40],                                             // a year earlier, approved 2021-03-22: too late
    ['2021-02-14', 0],                                              // a year earlier, one day past the window
  ]);
  const cmp = S.compare(h, S.offenseMask(ctx, null, null), ctx, 90);
  assert.equal(cmp.cur, 3);
  assert.equal(cmp.prior, 2);
  assert.equal(cmp.to, day('2022-02-13'));
  assert.equal(cmp.from, day('2021-11-16'));
  // the cut-off for "had arrived" is one year before today: 2021-03-15
  assert.equal(S.countAsOf(h, S.offenseMask(ctx, null, null), day('2021-02-01'), day('2021-02-13'), day('2021-03-22')), 2);
});

test('judge: only clear differences are called up or down', () => {
  assert.equal(S.judge(100, 100).verdict, 'flat');
  assert.equal(S.judge(104, 100).verdict, 'flat');
  assert.equal(S.judge(1000, 940).verdict, 'flat');       // +6%, but within chance for counts this size
  assert.equal(S.judge(1100, 1000).verdict, 'up');
  assert.equal(S.judge(80, 120).verdict, 'down');
  assert.equal(S.judge(10000, 9700).verdict, 'flat');     // clearly not chance, but under 5%
  assert.equal(S.judge(10, 5).verdict, 'few');
  assert.deepEqual(S.judge(0, 0), { change: null, verdict: 'few' });
  assert.ok(Math.abs(S.judge(80, 100).change + 0.2) < 1e-12);
});

test('compareScope sums the selected cells of the pipeline table', () => {
  const ctx = S.context(meta());
  const city = { compare: { 813: { 365: { cur: [10, 0, 0, 0, 5, 85], prior: [8, 0, 0, 0, 10, 100] } } } };
  const all = S.compareScope(city, '813', 365, S.cellMask(ctx, null, null));
  assert.deepEqual([all.cur, all.prior], [100, 118]);
  const high = S.compareScope(city, '813', 365, S.cellMask(ctx, null, new Set([0])));
  assert.deepEqual([high.cur, high.prior, high.verdict], [10, 8, 'few']);
  assert.equal(S.compareScope(city, '999', 365, S.cellMask(ctx, null, null)).cur, 0);
});

test('perThousand: a rate only where enough people live, and standing among the rates there are', () => {
  assert.equal(S.perThousand(1575, { residents: 38572, rated: true }).toFixed(1), '40.8');
  assert.equal(S.perThousand(437, { residents: 256, rated: false }), null);    // a park: nearly everyone is a visitor
  assert.equal(S.perThousand(0, { residents: 5000, rated: true }), 0);
  assert.equal(S.perThousand(10, { residents: null, rated: false }), null);    // the pipeline had no census file
  assert.deepEqual(S.standing(40, [300, null, 40, 12, 55, null]), { place: 3, of: 4 });
  assert.deepEqual(S.standing(300, [300, 300, 40]), { place: 1, of: 3 });     // a tie shares the better place
});

test('compareByCategory adds up to compare', () => {
  const ctx = S.context(meta());
  const h = hood([
    ['2022-01-10', 0, 0], ['2022-01-11', 0, 1], ['2022-01-12', 0, 2], ['2022-02-01', 2, 1],
    ['2021-01-10', 0, 0], ['2021-01-11', 90, 1], ['2021-01-12', 0, 2],
  ]);
  const all = S.offenseMask(ctx, null, null);
  const byCat = S.compareByCategory(h, all, ctx, 90);
  const whole = S.compare(h, all, ctx, 90);
  assert.deepEqual(byCat.map((c) => c.cur), [1, 3]);
  assert.deepEqual(byCat.map((c) => c.prior), [1, 1]);
  assert.equal(byCat.reduce((a, c) => a + c.cur, 0), whole.cur);
  assert.equal(byCat.reduce((a, c) => a + c.prior, 0), whole.prior);
});

test('monthly: every record lands in its month and severity', () => {
  const ctx = S.context(meta());
  const h = hood([['2020-01-01', 0, 0], ['2020-01-31', 0, 1], ['2020-02-01', 0, 1], ['2022-03-15', 0, 2]]);
  const bySev = S.monthly(h, S.offenseMask(ctx, null, null), ctx);
  assert.equal(bySev.length, 3);
  assert.deepEqual(bySev[0].slice(0, 2), [1, 0]);     // high: the assault, January 2020
  assert.deepEqual(bySev[2].slice(0, 2), [1, 1]);     // low: one theft in January, one in February
  assert.equal(bySev[1][26], 1);                      // medium: the vehicle theft, March 2022
  assert.equal(bySev.flat().reduce((a, v) => a + v, 0), 4);
  assert.equal(S.monthly(h, S.offenseMask(ctx, new Set([0]), null), ctx).flat().reduce((a, v) => a + v, 0), 1);
});

test('cityMonthly sums the selected cells', () => {
  const ctx = S.context(meta());
  const city = { monthly: [[1, 2], [0, 0], [0, 0], [0, 0], [10, 20], [100, 200]] };
  assert.deepEqual(S.cityMonthly(city, S.cellMask(ctx, null, null), 2), [111, 222]);
  assert.deepEqual(S.cityMonthly(city, S.cellMask(ctx, new Set([1]), new Set([2])), 2), [100, 200]);
});

test('trailingMean and indexTo100', () => {
  assert.deepEqual(S.trailingMean([10, 20, 30, 40, 50], 3, 3), [null, null, 20, 30, null]);
  assert.deepEqual(S.indexTo100([null, 50, 75, 25, null]), [null, 100, 150, 50, null]);
  assert.deepEqual(S.indexTo100([null, null]), [null, null]);
});

test('seasonalIndex recovers a known seasonal pattern from complete years only', () => {
  const ctx = S.context(meta({ data_through: '2023-06-20' }));       // 2020-2022 complete; 2023 is not
  const totals = ctx.months.map((m) => (m.month === 6 ? 110 : 100) * (m.year === 2023 ? 5 : 1));
  const index = S.seasonalIndex(totals, ctx);
  const yearMean = (11 * 100 + 110) / 12;
  assert.ok(Math.abs(index[6] - 110 / yearMean) < 1e-9);
  assert.ok(Math.abs(index[0] - 100 / yearMean) < 1e-9);
  assert.ok(Math.abs(index.reduce((a, v) => a + v, 0) / 12 - 1) < 1e-9);
});

test('outlook: level x season, with a range measured on the series itself', () => {
  const ctx = S.context(meta({ data_through: '2024-06-20' }));
  const flat = ctx.months.map(() => 120);
  const index = new Array(12).fill(1);
  index[7] = 1.1;                                                   // Augusts run 10% high
  const out = S.outlook(flat, index, ctx);
  assert.deepEqual(out.map((o) => [o.year, o.month]), [[2024, 6], [2024, 7], [2024, 8]]);
  assert.equal(out[0].expected, 120);
  assert.ok(Math.abs(out[1].expected - 132) < 1e-9);
  // the series never strayed from the rule except in the Augusts the rule over-predicts
  assert.ok(out[0].low <= 120 && out[0].high >= 120);
  assert.ok(out[0].high - out[0].low < 20);

  // a noisy series earns a wider range than a steady one
  const noisy = ctx.months.map((_, i) => 120 + (i % 2 ? 40 : -40));
  const wide = S.outlook(noisy, new Array(12).fill(1), ctx);
  assert.ok(wide[0].high - wide[0].low > 60);

  // with under two years of history the range falls back to a generous allowance for chance
  const short = S.context(meta({ data_through: '2021-06-20' }));
  const few = S.outlook(short.months.map(() => 100), new Array(12).fill(1), short);
  assert.ok(few[0].low < 100 && few[0].high > 100);
  assert.deepEqual(S.outlook([1, 2, 3], new Array(12).fill(1), S.context(meta({ data_through: '2020-03-10' }))), []);
  // and nothing is projected from a couple of reports a month
  assert.deepEqual(S.outlook(ctx.months.map(() => 2), new Array(12).fill(1), ctx), []);
});

test('placeCounts, countByCategory, countByOffense and records', () => {
  const ctx = S.context(meta());
  const places = [['3000 block University Ave', -11713000, 3274850], ['100 block Main St', null, null]];
  const h = hood([
    ['2022-01-05', 0, 0, 0], ['2022-01-06', 0, 1, 0], ['2022-01-07', 0, 1, 1], ['2022-01-08', 0, 2, -1],
    ['2021-06-01', 0, 1, 0],
  ], places);
  const all = S.offenseMask(ctx, null, null);
  const after = day('2021-12-31');
  const counts = S.placeCounts(h, all, ctx, after, ctx.today);
  assert.deepEqual(counts.get(0), { n: 2, sev: [1, 0, 1] });
  assert.deepEqual(counts.get(1), { n: 1, sev: [0, 0, 1] });
  assert.equal(counts.has(-1), false);                              // no address: counted, never placed
  assert.deepEqual(S.countByCategory(h, all, ctx, after, ctx.today), [1, 3]);
  assert.deepEqual(S.countByOffense(h, all, ctx, after, ctx.today), [1, 2, 1]);
  const newest = S.records(h, all, after, ctx.today, { limit: 2 });
  assert.deepEqual(newest.map((i) => h.day[i]), [day('2022-01-08'), day('2022-01-07')]);
  assert.equal(S.records(h, all, -1, ctx.today, { place: 0 }).length, 3);
});

// When the pipeline has been run, the browser and the pipeline must agree on every comparison.
const built = path.join(root, 'site/data/meta.json');
test('the browser reproduces the pipeline’s year-over-year counts', { skip: !existsSync(built) && 'run `python -m pipeline build` first' }, () => {
  const load = (p) => JSON.parse(readFileSync(path.join(root, 'site/data', p), 'utf8'));
  const m = load('meta.json');
  const city = load('city.json');
  const ctx = S.context(m);
  const beats = [m.home_beat, ...m.hoods.find((h) => h.beat === m.home_beat).neighbors.slice(0, 3)];
  const filters = [[null, null], [null, new Set([0])], [new Set([3, 4]), null], [new Set([0]), new Set([0, 1])]];
  for (const beat of beats) {
    const h = load(`hood/${beat}.json`);
    for (const [cats, sevs] of filters) {
      for (const w of m.windows) {
        const mine = S.compare(h, S.offenseMask(ctx, cats, sevs), ctx, w);
        const theirs = S.compareScope(city, String(beat), w, S.cellMask(ctx, cats, sevs));
        assert.deepEqual([mine.cur, mine.prior], [theirs.cur, theirs.prior], `beat ${beat}, window ${w}`);
      }
    }
  }
});
