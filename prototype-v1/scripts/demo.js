// Заполняет базу демонстрационными заказами и сохраняет примеры файлов в data/demo/.
const fs = require('fs');
const path = require('path');
const ExcelJS = require('exceljs');
const { openDb } = require('../src/db');
const { importFile, LABELS } = require('../src/importers');
const { todayIso } = require('../src/dates');

const today = new Date(todayIso() + 'T00:00:00Z');
const day = n => {
  const d = new Date(today);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10).split('-').reverse().join('.');
};

// [номер, клиент, менеджер, изделие, кол-во, дата заказа, первое обещание, план окончания, план шт, факт шт]
const ORDERS = [
  ['ЗП-1001', 'ООО «Северсталь-Комплект»', 'Иванова А.', 'Шкаф управления ШУ-2', 4, -40, -3, -6, 4, 3],
  ['ЗП-1002', 'АО «ТехноСтрой»', 'Петров С.', 'Щит ВРУ-1', 10, -30, 5, 9, 10, 4],
  ['ЗП-1003', 'ООО «Энергия»', 'Иванова А.', 'Шкаф автоматики', 2, -25, 12, 8, 2, 1],
  ['ЗП-1004', 'ИП Громов', 'Сидоров К.', 'Пульт оператора', 1, -10, 20, null, 1, 0],
  ['ЗП-1005', 'ООО «ВолгаНефть»', 'Петров С.', 'Шкаф КИП', 6, -20, 25, 12, 6, 2],
  ['ЗП-1006', 'АО «Металлург»', 'Сидоров К.', 'Щит освещения', 12, -35, 3, 1, 12, 12],
  ['ЗП-1007', 'ООО «АгроПром»', 'Иванова А.', 'Шкаф насосной', 3, -15, 15, 14, 3, 1],
  ['ЗП-1008', 'ООО «Энергия»', 'Петров С.', 'Шкаф управления ШУ-5', 2, -50, -12, -15, 2, 2],
  ['ЗП-1009', 'ООО «СтройМаш»', 'Сидоров К.', 'Шкаф ввода', 3, -45, -2, -4, 3, 2],
];

async function writeXlsx(file, header, rows) {
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet('Данные');
  ws.addRow(header);
  rows.forEach(r => ws.addRow(r));
  await wb.xlsx.writeFile(file);
}

async function main() {
  const dir = path.join(__dirname, '..', 'data', 'demo');
  fs.mkdirSync(dir, { recursive: true });

  const orders = ORDERS.map(o => [o[0], o[1], o[2], o[3], o[4], day(o[5]), day(o[6]), '']);
  // Второй файл заказов: по двум заказам клиенту перенесли срок
  const moved = ORDERS.map(o => {
    const promise = o[0] === 'ЗП-1002' ? day(o[6] + 7) : o[0] === 'ЗП-1001' ? day(o[6] + 5) : day(o[6]);
    const reason = o[0] === 'ЗП-1002' ? 'Задержка поставки комплектующих' : o[0] === 'ЗП-1001' ? 'Клиент изменил спецификацию' : '';
    return [o[0], o[1], o[2], o[3], o[4], day(o[5]), promise, reason];
  });
  const plan = ORDERS.filter(o => o[7] !== null).flatMap(o => [
    [o[0], 'Сборка', day(o[7] - 6), day(o[7] - 2)],
    [o[0], 'Испытания', day(o[7] - 2), day(o[7])],
  ]);
  const segments = ORDERS.flatMap(o => {
    const half = Math.ceil(o[8] / 2);
    return [
      [o[0], 'Металлоконструкция', o[8], o[8], day(-2)],
      [o[0], 'Монтаж', o[8], Math.min(o[9], o[8]), day(-1)],
      [o[0], 'Испытания', o[8], Math.max(0, o[9] - half), day(-1)],
    ];
  });

  const files = {
    orders: path.join(dir, '1-заказы.xlsx'),
    orders2: path.join(dir, '1-заказы-обновление.xlsx'),
    plan: path.join(dir, '2-план-производства.xlsx'),
    segments: path.join(dir, '3-отчёт-по-отрезкам.xlsx'),
  };
  await writeXlsx(files.orders, LABELS.orders, orders);
  await writeXlsx(files.orders2, LABELS.orders, moved);
  await writeXlsx(files.plan, LABELS.plan, plan);
  await writeXlsx(files.segments, LABELS.segments, segments);

  const db = openDb();
  db.exec('DELETE FROM promise_history; DELETE FROM production_plan; DELETE FROM segments; DELETE FROM orders;');
  for (const [kind, file] of [['orders', files.orders], ['orders', files.orders2], ['plan', files.plan], ['segments', files.segments]]) {
    const r = await importFile(db, kind, fs.readFileSync(file), path.basename(file));
    console.log(path.basename(file), r);
  }
  // Отгруженный заказ
  db.prepare('UPDATE orders SET shipped_date = ? WHERE order_no = ?').run(todayIso(new Date(Date.now() - 10 * 86400000)), 'ЗП-1008');
  console.log('Готово. Запустите npm start и откройте http://localhost:3000');
}

main().catch(e => { console.error(e); process.exit(1); });
