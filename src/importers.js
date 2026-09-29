const ExcelJS = require('exceljs');
const { parseDate } = require('./dates');

// Названия колонок, которые мы узнаём в файлах. Сравнение без учёта регистра, пробелов и знаков.
const COLUMNS = {
  orders: {
    order_no: ['номер заказа', 'заказ', '№ заказа', 'номер', 'заказ покупателя', 'order'],
    client: ['клиент', 'покупатель', 'контрагент', 'заказчик'],
    manager: ['менеджер', 'ответственный', 'автор'],
    product: ['изделие', 'продукция', 'номенклатура', 'товар'],
    qty: ['количество', 'кол-во', 'колво'],
    order_date: ['дата заказа', 'дата'],
    promised_date: ['обещанная дата', 'дата отгрузки', 'срок', 'срок отгрузки', 'желаемая дата отгрузки', 'дата поставки', 'срок поставки'],
    shipped_date: ['дата факт отгрузки', 'фактическая отгрузка', 'отгружено', 'дата отгрузки факт'],
    reason: ['причина переноса', 'причина'],
  },
  plan: {
    order_no: ['номер заказа', 'заказ', '№ заказа', 'заказ покупателя'],
    operation: ['операция', 'этап', 'участок', 'передел'],
    planned_start: ['начало', 'плановое начало', 'дата начала'],
    planned_finish: ['окончание', 'плановое окончание', 'дата окончания', 'дата готовности', 'план готовности'],
  },
  segments: {
    order_no: ['номер заказа', 'заказ', '№ заказа', 'заказ покупателя'],
    segment: ['отрезок', 'этап', 'операция', 'участок'],
    plan_qty: ['план', 'количество план', 'план кол-во', 'план колво', 'требуется'],
    done_qty: ['факт', 'выполнено', 'сделано', 'количество факт', 'факт кол-во', 'факт колво'],
    report_date: ['дата', 'дата отчета', 'дата отчёта'],
  },
};

const REQUIRED = {
  orders: ['order_no'],
  plan: ['order_no', 'planned_finish'],
  segments: ['order_no', 'done_qty'],
};

const LABELS = {
  orders: ['Номер заказа', 'Клиент', 'Менеджер', 'Изделие', 'Количество', 'Дата заказа', 'Обещанная дата', 'Причина переноса'],
  plan: ['Номер заказа', 'Операция', 'Начало', 'Окончание'],
  segments: ['Номер заказа', 'Отрезок', 'План', 'Факт', 'Дата'],
};

function norm(s) {
  return String(s ?? '').toLowerCase().replace(/ё/g, 'е').replace(/[^a-zа-я0-9]+/g, ' ').trim();
}

function cellValue(v) {
  if (v === null || v === undefined) return null;
  if (v instanceof Date) return v;
  if (typeof v === 'object') {
    if (v.richText) return v.richText.map(t => t.text).join('');
    if (v.text !== undefined) return v.text;
    if (v.result !== undefined) return v.result;
  }
  return v;
}

function decode(buffer) {
  let text = new TextDecoder('utf-8').decode(buffer);
  // Выгрузки из 1С часто в Windows-1251
  if (text.includes('�')) text = new TextDecoder('windows-1251').decode(buffer);
  return text.replace(/^﻿/, '');
}

function parseCsv(text) {
  const firstLine = text.split(/\r?\n/, 1)[0];
  const delim = (firstLine.match(/;/g) || []).length >= (firstLine.match(/,/g) || []).length ? ';' : ',';
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === delim) { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.some(v => String(v).trim() !== ''));
}

async function readTable(buffer, filename) {
  if (/\.csv$|\.txt$/i.test(filename)) return parseCsv(decode(buffer));
  const wb = new ExcelJS.Workbook();
  try {
    await wb.xlsx.load(buffer);
  } catch {
    throw new Error('Не удалось прочитать файл. Нужен Excel в формате .xlsx или CSV. Старый формат .xls сохраните как .xlsx.');
  }
  const ws = wb.worksheets[0];
  if (!ws) return [];
  const rows = [];
  ws.eachRow({ includeEmpty: false }, r => {
    const vals = [];
    for (let c = 1; c <= r.cellCount; c++) vals.push(cellValue(r.getCell(c).value));
    rows.push(vals);
  });
  return rows;
}

// Находит строку заголовков (не обязательно первую) и сопоставляет колонки по названиям.
function mapColumns(rows, kind) {
  const spec = COLUMNS[kind];
  for (let h = 0; h < Math.min(rows.length, 15); h++) {
    const headers = rows[h].map(norm);
    const map = {};
    for (const [field, aliases] of Object.entries(spec)) {
      const normAliases = aliases.map(norm);
      // Сначала точное совпадение, потом по началу названия
      let idx = headers.findIndex(x => normAliases.includes(x));
      if (idx < 0) idx = headers.findIndex(x => x && normAliases.some(a => x.startsWith(a + ' ')));
      if (idx >= 0 && !Object.values(map).includes(idx)) map[field] = idx;
    }
    if (REQUIRED[kind].every(f => map[f] !== undefined)) return { headerRow: h, map };
  }
  return null;
}

function toNumber(v) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return v;
  const n = Number(String(v).replace(/\s/g, '').replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}

function toText(v) {
  if (v === null || v === undefined) return null;
  if (v instanceof Date) return parseDate(v);
  const s = String(v).trim();
  return s === '' ? null : s;
}

function extract(rows, kind) {
  const found = mapColumns(rows, kind);
  if (!found) {
    const need = REQUIRED[kind].map(f => COLUMNS[kind][f][0]).join(', ');
    throw new Error(`Не нашли обязательные колонки: ${need}. Проверьте заголовки в файле.`);
  }
  const { headerRow, map } = found;
  const out = [];
  for (const r of rows.slice(headerRow + 1)) {
    const rec = {};
    for (const [field, idx] of Object.entries(map)) rec[field] = r[idx] ?? null;
    rec.order_no = toText(rec.order_no);
    if (!rec.order_no) continue;
    out.push(rec);
  }
  return { records: out, columns: Object.keys(map) };
}

function importOrders(db, records) {
  const get = db.prepare('SELECT * FROM orders WHERE order_no = ?');
  const insert = db.prepare(`INSERT INTO orders (order_no, client, manager, product, qty, order_date, first_promised_date, current_promised_date, shipped_date)
    VALUES (@order_no, @client, @manager, @product, @qty, @order_date, @promised, @promised, @shipped_date)`);
  const update = db.prepare(`UPDATE orders SET
      client = COALESCE(@client, client), manager = COALESCE(@manager, manager), product = COALESCE(@product, product),
      qty = COALESCE(@qty, qty), order_date = COALESCE(@order_date, order_date),
      first_promised_date = COALESCE(first_promised_date, @promised),
      current_promised_date = COALESCE(@promised, current_promised_date),
      shipped_date = COALESCE(@shipped_date, shipped_date), updated_at = datetime('now')
    WHERE order_no = @order_no`);
  const history = db.prepare('INSERT INTO promise_history (order_no, promised_date, reason, source) VALUES (?, ?, ?, ?)');

  const stats = { created: 0, updated: 0, rescheduled: 0, skipped: 0 };
  db.transaction(() => {
    for (const r of records) {
      const rec = {
        order_no: r.order_no,
        client: toText(r.client),
        manager: toText(r.manager),
        product: toText(r.product),
        qty: toNumber(r.qty),
        order_date: parseDate(r.order_date),
        promised: parseDate(r.promised_date),
        shipped_date: parseDate(r.shipped_date),
      };
      const existing = get.get(rec.order_no);
      if (!existing) {
        insert.run(rec);
        if (rec.promised) history.run(rec.order_no, rec.promised, 'Первая обещанная дата', 'import');
        stats.created++;
      } else {
        update.run(rec);
        if (rec.promised && rec.promised !== existing.current_promised_date) {
          const reason = existing.current_promised_date ? (toText(r.reason) || 'Изменена в загруженном файле') : 'Первая обещанная дата';
          history.run(rec.order_no, rec.promised, reason, 'import');
          if (existing.current_promised_date) stats.rescheduled++;
        }
        stats.updated++;
      }
    }
  })();
  return stats;
}

function importPlan(db, records) {
  const del = db.prepare('DELETE FROM production_plan WHERE order_no = ?');
  const ins = db.prepare('INSERT INTO production_plan (order_no, operation, planned_start, planned_finish) VALUES (?, ?, ?, ?)');
  const orders = new Set();
  let rows = 0;
  let badDates = 0;
  db.transaction(() => {
    for (const r of records) {
      if (!orders.has(r.order_no)) { del.run(r.order_no); orders.add(r.order_no); }
      const finish = parseDate(r.planned_finish);
      if (!finish) badDates++;
      ins.run(r.order_no, toText(r.operation), parseDate(r.planned_start), finish);
      rows++;
    }
  })();
  return { orders: orders.size, rows, badDates, unknownOrders: countUnknown(db, orders) };
}

function importSegments(db, records) {
  const del = db.prepare('DELETE FROM segments WHERE order_no = ?');
  const ins = db.prepare('INSERT INTO segments (order_no, segment, plan_qty, done_qty, report_date) VALUES (?, ?, ?, ?, ?)');
  const orders = new Set();
  let rows = 0;
  db.transaction(() => {
    for (const r of records) {
      if (!orders.has(r.order_no)) { del.run(r.order_no); orders.add(r.order_no); }
      ins.run(r.order_no, toText(r.segment), toNumber(r.plan_qty), toNumber(r.done_qty), parseDate(r.report_date));
      rows++;
    }
  })();
  return { orders: orders.size, rows, unknownOrders: countUnknown(db, orders) };
}

function countUnknown(db, orderNos) {
  const get = db.prepare('SELECT 1 FROM orders WHERE order_no = ?');
  return [...orderNos].filter(o => !get.get(o)).length;
}

async function importFile(db, kind, buffer, filename) {
  if (!COLUMNS[kind]) throw new Error('Неизвестный тип файла');
  const rows = await readTable(buffer, filename);
  const { records, columns } = extract(rows, kind);
  const result = kind === 'orders' ? importOrders(db, records) : kind === 'plan' ? importPlan(db, records) : importSegments(db, records);
  return { kind, records: records.length, columns, ...result };
}

async function templateWorkbook(kind) {
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet('Данные');
  ws.addRow(LABELS[kind]);
  ws.getRow(1).font = { bold: true };
  ws.columns.forEach(c => { c.width = 20; });
  return wb.xlsx.writeBuffer();
}

module.exports = { importFile, readTable, extract, parseCsv, importOrders, importPlan, importSegments, templateWorkbook, LABELS };
