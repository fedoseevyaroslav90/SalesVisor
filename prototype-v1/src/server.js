const path = require('path');
const express = require('express');
const multer = require('multer');
const { openDb, getSettings, saveSettings } = require('./db');
const { evaluateOrder } = require('./status');
const { importFile, templateWorkbook } = require('./importers');
const { parseDate, todayIso } = require('./dates');

function groupBy(rows, key) {
  const m = new Map();
  for (const r of rows) {
    if (!m.has(r[key])) m.set(r[key], []);
    m.get(r[key]).push(r);
  }
  return m;
}

function loadOrders(db, today = todayIso()) {
  const settings = getSettings(db);
  const orders = db.prepare('SELECT * FROM orders').all();
  const plan = groupBy(db.prepare('SELECT * FROM production_plan').all(), 'order_no');
  const segs = groupBy(db.prepare('SELECT * FROM segments').all(), 'order_no');
  const changes = new Map(db.prepare("SELECT order_no, COUNT(*) - 1 AS n FROM promise_history GROUP BY order_no").all().map(r => [r.order_no, r.n]));
  return orders
    .map(o => ({ ...evaluateOrder(o, plan.get(o.order_no) || [], segs.get(o.order_no) || [], settings, today), reschedules: Math.max(0, changes.get(o.order_no) || 0) }))
    .sort((a, b) => a.status_rank - b.status_rank || (a.current_promised_date || '9999').localeCompare(b.current_promised_date || '9999'));
}

function createApp(db) {
  const app = express();
  const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 20 * 1024 * 1024 } });
  app.use(express.json());
  app.use(express.static(path.join(__dirname, '..', 'public')));

  app.get('/api/orders', (req, res) => {
    res.json({ today: todayIso(), settings: getSettings(db), orders: loadOrders(db) });
  });

  app.get('/api/orders/:no', (req, res) => {
    const no = req.params.no;
    const order = loadOrders(db).find(o => o.order_no === no);
    if (!order) return res.status(404).json({ error: 'Заказ не найден' });
    res.json({
      order,
      history: db.prepare('SELECT * FROM promise_history WHERE order_no = ? ORDER BY recorded_at, id').all(no),
      plan: db.prepare('SELECT * FROM production_plan WHERE order_no = ? ORDER BY planned_start, planned_finish').all(no),
      segments: db.prepare('SELECT * FROM segments WHERE order_no = ? ORDER BY report_date, id').all(no),
    });
  });

  // Перенос обещанной даты вручную: первая дата остаётся, в историю пишется причина
  app.post('/api/orders/:no/promise', (req, res) => {
    const no = req.params.no;
    const date = parseDate(req.body.date);
    const reason = String(req.body.reason || '').trim();
    if (!date) return res.status(400).json({ error: 'Укажите дату' });
    if (!reason) return res.status(400).json({ error: 'Укажите причину переноса' });
    const order = db.prepare('SELECT * FROM orders WHERE order_no = ?').get(no);
    if (!order) return res.status(404).json({ error: 'Заказ не найден' });
    db.transaction(() => {
      db.prepare("UPDATE orders SET current_promised_date = ?, first_promised_date = COALESCE(first_promised_date, ?), updated_at = datetime('now') WHERE order_no = ?").run(date, date, no);
      db.prepare('INSERT INTO promise_history (order_no, promised_date, reason, source) VALUES (?, ?, ?, ?)').run(no, date, reason, 'manual');
    })();
    res.json({ ok: true });
  });

  app.post('/api/orders/:no/update', (req, res) => {
    const no = req.params.no;
    const order = db.prepare('SELECT * FROM orders WHERE order_no = ?').get(no);
    if (!order) return res.status(404).json({ error: 'Заказ не найден' });
    const shipped = req.body.shipped_date === null ? null : req.body.shipped_date !== undefined ? parseDate(req.body.shipped_date) : order.shipped_date;
    const comment = req.body.comment !== undefined ? String(req.body.comment) : order.comment;
    const cancelled = req.body.cancelled !== undefined ? (req.body.cancelled ? 1 : 0) : order.cancelled;
    db.prepare("UPDATE orders SET shipped_date = ?, comment = ?, cancelled = ?, updated_at = datetime('now') WHERE order_no = ?").run(shipped, comment, cancelled, no);
    res.json({ ok: true });
  });

  app.post('/api/import/:kind', upload.single('file'), async (req, res) => {
    if (!req.file) return res.status(400).json({ error: 'Файл не выбран' });
    try {
      const result = await importFile(db, req.params.kind, req.file.buffer, req.file.originalname);
      res.json(result);
    } catch (e) {
      res.status(400).json({ error: e.message });
    }
  });

  app.get('/api/templates/:kind', async (req, res) => {
    const names = { orders: 'Заказы и обещанные даты', plan: 'План производства', segments: 'Отчёт по отрезкам' };
    if (!names[req.params.kind]) return res.status(404).end();
    const buf = await templateWorkbook(req.params.kind);
    res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
    res.setHeader('Content-Disposition', `attachment; filename*=UTF-8''${encodeURIComponent(names[req.params.kind] + '.xlsx')}`);
    res.send(Buffer.from(buf));
  });

  app.get('/api/settings', (req, res) => res.json(getSettings(db)));
  app.post('/api/settings', (req, res) => res.json(saveSettings(db, req.body || {})));

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT || 3000);
  createApp(openDb()).listen(port, () => console.log(`SalesVisor: http://localhost:${port}`));
}

module.exports = { createApp, loadOrders };
