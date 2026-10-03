// Hand-rolled SVG charts: monthly stacked columns and indexed lines. No chart library.
// Marks follow one set of rules: thin columns with a 2px gap between stacked segments, 2px lines,
// hairline solid grid, text in ink colors (never the series color), and every value reachable by
// pointer, by keyboard (arrow keys on a focused chart) and in a table next to the chart.

import * as F from './format.js';

const NS = 'http://www.w3.org/2000/svg';

function svg(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function html(tag, className, text, parent) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

// ---- tooltip (one shared element) --------------------------------------------------------------

let tip = null;

export function hideTip() {
  if (tip) tip.hidden = true;
}

/** build(tipElement) fills the tooltip; x / y are viewport coordinates to sit beside. */
export function showTip(build, x, y) {
  if (!tip) {
    tip = html('div', 'tip', null, document.body);
    tip.setAttribute('role', 'status');
  }
  tip.replaceChildren();
  build(tip);
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  let left = x + 14;
  let top = y + 14;
  if (left + box.width > window.innerWidth - 8) left = x - box.width - 14;
  if (top + box.height > window.innerHeight - 8) top = y - box.height - 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}

export function tipTitle(t, title, sub) {
  html('div', 'tip-title', title, t);
  if (sub) html('div', 'tip-sub', sub, t);
}

/** One readout row: a short key in the series color, the value (strong), then the label. */
export function tipRow(t, color, value, label, { line = false } = {}) {
  const row = html('div', 'tip-row', null, t);
  const key = html('span', line ? 'key key-line' : 'key', null, row);
  if (color) key.style.background = color; else key.style.visibility = 'hidden';
  html('strong', null, value, row);
  html('span', null, label, row);
}

// ---- scales --------------------------------------------------------------------------------------

/** Round tick values from 0 to just past `max`. */
export function niceTicks(max, target = 5) {
  if (!(max > 0)) return [0, 1];
  const raw = max / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = Math.max(1, (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag);
  const ticks = [];
  for (let v = 0; v < max + step; v += step) ticks.push(v);
  return ticks;
}

function roundedTop(x, y, w, h, r) {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

function onResize(root, draw) {
  let width = 0;
  const observer = new ResizeObserver(() => {
    if (Math.abs(root.clientWidth - width) > 1) {
      width = root.clientWidth;
      draw();
    }
  });
  observer.observe(root);
  root._observer?.disconnect();
  root._observer = observer;
}

// ---- monthly stacked columns ---------------------------------------------------------------------

/**
 * model = {
 *   months:  [{ year, month, status }]            status: final | provisional | partial
 *   series:  [{ name, color, values[] }]          stacked bottom to top
 *   average: [number|null]                        trailing 12-month mean, same scale as the columns
 *   outlook: [{ year, month, expected, low, high }]
 *   onTrim(firstShownMonth | null)                called when a narrow screen hides early months
 * }
 */
export function trendChart(root, model) {
  const draw = () => {
    hideTip();
    root.replaceChildren();
    const W = root.clientWidth;
    if (W < 120) return;
    const M = { l: 38, r: 10, t: 12, b: 26 };
    const H = W < 520 ? 250 : 310;
    const plotW = W - M.l - M.r;
    const plotH = H - M.t - M.b;
    const nMonths = model.months.length;
    const total = nMonths + model.outlook.length;
    const shown = Math.min(total, Math.max(12, Math.floor(plotW / 7)));
    const first = total - shown;
    const slot = plotW / shown;
    const barW = Math.max(2, Math.min(24, Math.floor(slot - 2)));
    model.onTrim?.(first > 0 ? model.months[first] : null);

    const totals = model.months.map((_, i) => model.series.reduce((a, s) => a + s.values[i], 0));
    let max = 1;
    for (let i = first; i < nMonths; i++) max = Math.max(max, totals[i], model.average[i] || 0);
    for (const o of model.outlook) max = Math.max(max, o.high);
    const ticks = niceTicks(max);
    const top = ticks[ticks.length - 1];
    const y = (v) => M.t + plotH - (v / top) * plotH;
    const x = (i) => M.l + (i - first) * slot + (slot - barW) / 2;

    const root_ = svg('svg', {
      width: W, height: H, viewBox: `0 0 ${W} ${H}`, tabindex: 0, role: 'img', class: 'chart',
      'aria-label': 'Reported offenses per month, stacked by severity. Arrow keys step through the months; the table below the chart has the same figures.',
    }, root);

    for (const t of ticks) {
      svg('line', { x1: M.l, x2: W - M.r, y1: y(t), y2: y(t), class: t === 0 ? 'axis' : 'grid' }, root_);
      svg('text', { x: M.l - 8, y: y(t) + 4, class: 'tick', 'text-anchor': 'end' }, root_).textContent = F.int(t);
    }
    const band = svg('rect', { x: 0, y: M.t, width: slot, height: plotH, class: 'hover-band', visibility: 'hidden' }, root_);

    for (let i = first; i < nMonths; i++) {
      const mo = model.months[i];
      const g = svg('g', { opacity: mo.status === 'final' ? 1 : 0.45 }, root_);
      let base = y(0);
      const topSeries = model.series.reduce((last, s, k) => (s.values[i] > 0 ? k : last), -1);
      model.series.forEach((s, k) => {
        const v = s.values[i];
        if (!v) return;
        const h = Math.max(1, (v / top) * plotH);
        // 2px of surface between stacked segments, when the segment is tall enough to spare it
        const gap = k !== topSeries && h > 4 ? 2 : 0;
        if (k === topSeries) {
          svg('path', { d: roundedTop(x(i), base - h, barW, h, 4), fill: s.color }, g);
        } else {
          svg('rect', { x: x(i), y: base - h + gap, width: barW, height: h - gap, fill: s.color }, g);
        }
        base -= h;
      });
      if (mo.month === 0) {
        svg('text', { x: x(i), y: H - 8, class: 'tick' }, root_).textContent = String(mo.year);
      }
    }

    model.outlook.forEach((o, k) => {
      const i = nMonths + k;
      if (i < first) return;
      const cx = x(i) + barW / 2;
      svg('line', { x1: cx, x2: cx, y1: y(o.high), y2: y(o.low), class: 'whisker' }, root_);
      svg('rect', { x: x(i) + 0.5, y: y(o.expected), width: barW - 1, height: y(0) - y(o.expected), class: 'estimate' }, root_);
    });

    let d = '';
    for (let i = first; i < nMonths; i++) {
      if (model.average[i] === null || model.average[i] === undefined) continue;
      d += `${d ? 'L' : 'M'}${(x(i) + barW / 2).toFixed(1)},${y(model.average[i]).toFixed(1)}`;
    }
    if (d) svg('path', { d, class: 'avg-line' }, root_);

    // One hit area for the whole plot: the pointer picks the nearest month.
    let active = -1;
    const readout = (i, clientX, clientY) => {
      active = i;
      band.setAttribute('x', M.l + (i - first) * slot);
      band.setAttribute('visibility', 'visible');
      showTip((t) => {
        if (i >= nMonths) {
          const o = model.outlook[i - nMonths];
          tipTitle(t, F.month(o.year, o.month, { long: true }), 'Outlook, if the past year’s level holds');
          tipRow(t, null, `about ${F.int(o.expected)}`, 'expected');
          tipRow(t, null, `${F.int(o.low)}–${F.int(o.high)}`, 'usual range');
          return;
        }
        const mo = model.months[i];
        const note = mo.status === 'partial' ? 'Month in progress' : mo.status === 'provisional' ? 'Reports still arriving' : null;
        tipTitle(t, F.month(mo.year, mo.month, { long: true }), note);
        tipRow(t, null, F.int(totals[i]), 'total');
        [...model.series].reverse().forEach((s) => tipRow(t, s.color, F.int(s.values[i]), s.name));
        if (model.average[i] !== null && model.average[i] !== undefined) {
          tipRow(t, 'var(--text-2)', F.int(model.average[i]), '12-month average', { line: true });
        }
      }, clientX, clientY);
    };
    const leave = () => { band.setAttribute('visibility', 'hidden'); hideTip(); active = -1; };
    const hit = svg('rect', { x: M.l, y: M.t, width: plotW, height: plotH, fill: 'transparent' }, root_);
    hit.addEventListener('pointermove', (e) => {
      const box = root_.getBoundingClientRect();
      const i = first + Math.max(0, Math.min(shown - 1, Math.floor((e.clientX - box.left - M.l) / slot)));
      readout(i, e.clientX, e.clientY);
    });
    hit.addEventListener('pointerleave', leave);
    root_.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const start = active < 0 ? nMonths - 1 : active + (e.key === 'ArrowLeft' ? -1 : 1);
      const i = Math.max(first, Math.min(total - 1, start));
      const box = root_.getBoundingClientRect();
      readout(i, box.left + x(i) + barW, box.top + M.t + 20);
    });
    root_.addEventListener('blur', leave);
  };
  draw();
  onResize(root, draw);
}

// ---- indexed lines -------------------------------------------------------------------------------

/**
 * model = {
 *   months: [{ year, month }]
 *   series: [{ name, color, values[] (null where undefined), strong }]   index values, 100 = start
 * }
 */
export function indexChart(root, model) {
  const draw = () => {
    hideTip();
    root.replaceChildren();
    const W = root.clientWidth;
    if (W < 120) return;
    const first = model.series[0].values.findIndex((v) => v !== null);
    let last = -1;
    model.series[0].values.forEach((v, i) => { if (v !== null) last = i; });
    if (first < 0 || last <= first) {
      html('p', 'empty', 'Not enough history to draw this yet.', root);
      return;
    }
    const M = { l: 38, r: 46, t: 12, b: 26 };
    const H = 230;
    const plotW = W - M.l - M.r;
    const plotH = H - M.t - M.b;
    let lo = 100;
    let hi = 100;
    for (const s of model.series) {
      for (let i = first; i <= last; i++) {
        if (s.values[i] !== null) { lo = Math.min(lo, s.values[i]); hi = Math.max(hi, s.values[i]); }
      }
    }
    const step = hi - lo > 60 ? 20 : 10;
    lo = Math.floor(lo / step) * step;
    hi = Math.ceil(hi / step) * step;
    if (hi === lo) hi = lo + step;
    const x = (i) => M.l + ((i - first) / (last - first)) * plotW;
    const y = (v) => M.t + plotH - ((v - lo) / (hi - lo)) * plotH;

    const root_ = svg('svg', {
      width: W, height: H, viewBox: `0 0 ${W} ${H}`, tabindex: 0, role: 'img', class: 'chart',
      'aria-label': `${model.series.map((s) => s.name).join(' and ')}: 12-month running totals, shown as an index where the first value is 100. Arrow keys step through the months.`,
    }, root);
    for (let t = lo; t <= hi; t += step) {
      svg('line', { x1: M.l, x2: W - M.r, y1: y(t), y2: y(t), class: t === 100 ? 'axis' : 'grid' }, root_);
      svg('text', { x: M.l - 8, y: y(t) + 4, class: 'tick', 'text-anchor': 'end' }, root_).textContent = String(t);
    }
    for (let i = first; i <= last; i++) {
      if (model.months[i].month === 0) {
        svg('text', { x: x(i), y: H - 8, class: 'tick', 'text-anchor': 'middle' }, root_).textContent = String(model.months[i].year);
      }
    }
    const cross = svg('line', { y1: M.t, y2: M.t + plotH, class: 'crosshair', visibility: 'hidden' }, root_);

    const ends = [];
    for (const s of model.series) {
      let d = '';
      for (let i = first; i <= last; i++) {
        if (s.values[i] !== null) d += `${d ? 'L' : 'M'}${x(i).toFixed(1)},${y(s.values[i]).toFixed(1)}`;
      }
      svg('path', { d, class: 'line', stroke: s.color }, root_);
      svg('circle', { cx: x(last), cy: y(s.values[last]), r: 4, fill: s.color, class: 'dot' }, root_);
      ends.push({ s, y: y(s.values[last]) });
    }
    // Direct end labels only when they do not collide; the legend and tooltip carry them otherwise.
    if (ends.length < 2 || Math.abs(ends[0].y - ends[1].y) >= 15) {
      for (const e of ends) {
        svg('text', { x: x(last) + 9, y: e.y + 4, class: e.s.strong ? 'end-label strong' : 'end-label' }, root_)
          .textContent = F.int(e.s.values[last]);
      }
    }
    const dots = model.series.map((s) => svg('circle', { r: 4, fill: s.color, class: 'dot', visibility: 'hidden' }, root_));

    let active = -1;
    const readout = (i, clientX, clientY) => {
      active = i;
      cross.setAttribute('x1', x(i));
      cross.setAttribute('x2', x(i));
      cross.setAttribute('visibility', 'visible');
      model.series.forEach((s, k) => {
        dots[k].setAttribute('cx', x(i));
        dots[k].setAttribute('cy', y(s.values[i]));
        dots[k].setAttribute('visibility', 'visible');
      });
      showTip((t) => {
        const mo = model.months[i];
        tipTitle(t, `12 months to ${F.month(mo.year, mo.month)}`, 'Index: first value = 100');
        model.series.forEach((s) => tipRow(t, s.color, F.int(s.values[i]), s.name, { line: true }));
      }, clientX, clientY);
    };
    const leave = () => {
      cross.setAttribute('visibility', 'hidden');
      dots.forEach((dot) => dot.setAttribute('visibility', 'hidden'));
      hideTip();
      active = -1;
    };
    const hit = svg('rect', { x: M.l, y: M.t, width: plotW, height: plotH, fill: 'transparent' }, root_);
    hit.addEventListener('pointermove', (e) => {
      const box = root_.getBoundingClientRect();
      const frac = (e.clientX - box.left - M.l) / plotW;
      readout(Math.max(first, Math.min(last, Math.round(first + frac * (last - first)))), e.clientX, e.clientY);
    });
    hit.addEventListener('pointerleave', leave);
    root_.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const start = active < 0 ? last : active + (e.key === 'ArrowLeft' ? -1 : 1);
      const i = Math.max(first, Math.min(last, start));
      const box = root_.getBoundingClientRect();
      readout(i, box.left + x(i), box.top + M.t + 20);
    });
    root_.addEventListener('blur', leave);
  };
  draw();
  onResize(root, draw);
}
