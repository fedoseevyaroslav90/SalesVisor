const test = require('node:test');
const assert = require('node:assert');
const { parseDate, daysBetween } = require('../src/dates');
const { openDb, getSettings } = require('../src/db');
const { evaluateOrder } = require('../src/status');
const { parseCsv, extract, importOrders, importPlan, importSegments } = require('../src/importers');
const { loadOrders } = require('../src/server');

test('parseDate понимает русские и ISO форматы', () => {
  assert.strictEqual(parseDate('05.03.2026'), '2026-03-05');
  assert.strictEqual(parseDate('5.3.26'), '2026-03-05');
  assert.strictEqual(parseDate('2026-03-05'), '2026-03-05');
  assert.strictEqual(parseDate('05.03.2026 0:00:00'), '2026-03-05');
  assert.strictEqual(parseDate(new Date(Date.UTC(2026, 2, 5))), '2026-03-05');
  assert.strictEqual(parseDate(46086), '2026-03-05');
  assert.strictEqual(parseDate('31.02.2026'), null);
  assert.strictEqual(parseDate(''), null);
  assert.strictEqual(daysBetween('2026-03-01', '2026-03-05'), 4);
});

test('CSV с точкой с запятой и кавычками', () => {
  const rows = parseCsv('﻿Номер заказа;Клиент;Обещанная дата\n"ЗП-1";"ООО ""Ромашка""";01.10.2026\r\n');
  assert.deepStrictEqual(rows, [['﻿Номер заказа', 'Клиент', 'Обещанная дата'], ['ЗП-1', 'ООО "Ромашка"', '01.10.2026']]);
});

test('колонки находятся, даже если заголовок не в первой строке', () => {
  const rows = [['Отчёт за сентябрь'], [], ['№ заказа', 'Покупатель', 'Срок отгрузки'], ['ЗП-7', 'Альфа', '10.10.2026']];
  const { records } = extract(rows.filter(r => r.length), 'orders');
  assert.strictEqual(records.length, 1);
  assert.strictEqual(records[0].client, 'Альфа');
  assert.strictEqual(records[0].promised_date, '10.10.2026');
});

test('первая обещанная дата сохраняется при переносе, перенос пишется в историю', () => {
  const db = openDb(':memory:');
  importOrders(db, [{ order_no: 'ЗП-1', promised_date: '01.10.2026' }]);
  const r = importOrders(db, [{ order_no: 'ЗП-1', promised_date: '08.10.2026', reason: 'Нет комплектующих' }]);
  importOrders(db, [{ order_no: 'ЗП-1', promised_date: '08.10.2026' }]);
  assert.strictEqual(r.rescheduled, 1);
  const o = db.prepare('SELECT * FROM orders').get();
  assert.strictEqual(o.first_promised_date, '2026-10-01');
  assert.strictEqual(o.current_promised_date, '2026-10-08');
  const h = db.prepare('SELECT promised_date, reason FROM promise_history ORDER BY id').all();
  assert.deepStrictEqual(h.map(x => x.reason), ['Первая обещанная дата', 'Нет комплектующих']);
});

test('повторная загрузка плана заменяет строки заказа', () => {
  const db = openDb(':memory:');
  importOrders(db, [{ order_no: 'ЗП-1', promised_date: '10.10.2026' }]);
  importPlan(db, [{ order_no: 'ЗП-1', planned_finish: '01.10.2026' }, { order_no: 'ЗП-1', planned_finish: '03.10.2026' }]);
  const r = importPlan(db, [{ order_no: 'ЗП-1', planned_finish: '05.10.2026' }, { order_no: 'ЗП-9', planned_finish: '05.10.2026' }]);
  assert.strictEqual(r.unknownOrders, 1);
  assert.deepStrictEqual(db.prepare("SELECT planned_finish FROM production_plan WHERE order_no = 'ЗП-1'").all(), [{ planned_finish: '2026-10-05' }]);
});

test('статусы заказа', () => {
  const settings = { shipBufferDays: 2, warnDays: 3 };
  const order = { order_no: '1', first_promised_date: '2026-10-10', current_promised_date: '2026-10-10' };
  const plan = f => [{ planned_finish: f }];
  const seg = (p, d) => [{ plan_qty: p, done_qty: d }];
  const today = '2026-10-01';
  assert.strictEqual(evaluateOrder(order, plan('2026-10-03'), seg(10, 2), settings, today).status, 'ok');
  assert.strictEqual(evaluateOrder(order, plan('2026-10-06'), seg(10, 2), settings, today).status, 'warn');
  assert.strictEqual(evaluateOrder(order, plan('2026-10-09'), seg(10, 2), settings, today).status, 'risk');
  assert.strictEqual(evaluateOrder(order, [], seg(10, 2), settings, today).status, 'noplan');
  assert.strictEqual(evaluateOrder(order, [], seg(10, 10), settings, today).status, 'ok');
  assert.strictEqual(evaluateOrder(order, plan('2026-10-09'), seg(10, 2), settings, '2026-10-11').status, 'overdue');
  assert.strictEqual(evaluateOrder({ ...order, shipped_date: '2026-10-09' }, [], [], settings, '2026-10-11').status, 'shipped');
  // План готовности прошёл, а заказ не доделан
  assert.strictEqual(evaluateOrder(order, plan('2026-09-28'), seg(10, 5), settings, today).status, 'risk');
  const e = evaluateOrder({ ...order, current_promised_date: '2026-10-15' }, plan('2026-10-03'), seg(4, 1), settings, today);
  assert.strictEqual(e.shift_days, 5);
  assert.strictEqual(e.reserve_days, 10);
  assert.strictEqual(e.progress, 0.25);
});

test('loadOrders сводит все три источника', () => {
  const db = openDb(':memory:');
  importOrders(db, [{ order_no: 'ЗП-1', manager: 'Иванова', promised_date: '10.10.2026' }, { order_no: 'ЗП-2', promised_date: '01.09.2026' }]);
  importPlan(db, [{ order_no: 'ЗП-1', planned_finish: '09.10.2026' }]);
  importSegments(db, [{ order_no: 'ЗП-1', plan_qty: 2, done_qty: 1 }]);
  const list = loadOrders(db, '2026-10-01');
  assert.deepStrictEqual(list.map(o => [o.order_no, o.status]), [['ЗП-2', 'overdue'], ['ЗП-1', 'risk']]);
  assert.strictEqual(getSettings(db).shipBufferDays, 2);
});
