// Все даты храним как строки YYYY-MM-DD, без времени и часовых поясов.

function pad(n) {
  return String(n).padStart(2, '0');
}

function isoFromParts(y, m, d) {
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return null;
  return `${y}-${pad(m)}-${pad(d)}`;
}

// Понимает Date из Excel, серийный номер Excel, «31.12.2026», «31.12.26», «2026-12-31».
function parseDate(value) {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return null;
    return isoFromParts(value.getUTCFullYear(), value.getUTCMonth() + 1, value.getUTCDate());
  }
  if (typeof value === 'number') {
    if (value < 1 || value > 100000) return null;
    const ms = Math.round((value - 25569) * 86400 * 1000);
    return parseDate(new Date(ms));
  }
  if (typeof value === 'object' && value.result !== undefined) return parseDate(value.result);
  const s = String(value).trim();
  let m = s.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?:\s.*)?$/);
  if (m) {
    let y = Number(m[3]);
    if (y < 100) y += 2000;
    return isoFromParts(y, Number(m[2]), Number(m[1]));
  }
  m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s].*)?$/);
  if (m) return isoFromParts(Number(m[1]), Number(m[2]), Number(m[3]));
  return null;
}

function daysBetween(fromIso, toIso) {
  if (!fromIso || !toIso) return null;
  return Math.round((Date.parse(toIso + 'T00:00:00Z') - Date.parse(fromIso + 'T00:00:00Z')) / 86400000);
}

function todayIso(now = new Date()) {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

module.exports = { parseDate, daysBetween, todayIso };
