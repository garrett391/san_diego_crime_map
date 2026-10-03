// Number and date formatting. Dates are handled in UTC so a day number never shifts by timezone.

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December'];

export const int = (n) => Math.round(n).toLocaleString('en-US');

/** 0.154 -> "15%". The sign is carried by an arrow and a word, never by the number alone. */
export function pct(change) {
  if (change === null || !Number.isFinite(change)) return '–';
  const p = Math.abs(change) * 100;
  return `${p >= 0.5 ? p.toFixed(0) : '0'}%`;
}

export function date(d, { year = true } = {}) {
  const text = `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
  return year ? `${text}, ${d.getUTCFullYear()}` : text;
}

/** "17:05" -> "5:05 pm" */
export function clock(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
}

export function month(year, monthIndex, { long = false } = {}) {
  return `${(long ? MONTHS_LONG : MONTHS)[monthIndex]} ${year}`;
}

/** "3 days ago", "today", for the freshness line. */
export function ago(then, now = new Date()) {
  const days = Math.floor((now - then) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days} days ago`;
}
