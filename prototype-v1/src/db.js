const path = require('path');
const fs = require('fs');
const Database = require('better-sqlite3');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS orders (
  order_no              TEXT PRIMARY KEY,
  client                TEXT,
  manager               TEXT,
  product               TEXT,
  qty                   REAL,
  order_date            TEXT,
  first_promised_date   TEXT,   -- первая дата, обещанная клиенту; не меняется при переносах
  current_promised_date TEXT,   -- действующая обещанная дата
  shipped_date          TEXT,
  cancelled             INTEGER NOT NULL DEFAULT 0,
  comment               TEXT,
  updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Каждое изменение обещанной даты, включая первую
CREATE TABLE IF NOT EXISTS promise_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no      TEXT NOT NULL REFERENCES orders(order_no) ON DELETE CASCADE,
  promised_date TEXT NOT NULL,
  reason        TEXT,
  source        TEXT NOT NULL,          -- import | manual
  recorded_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- План производства: заменяется целиком для заказа при каждой загрузке
CREATE TABLE IF NOT EXISTS production_plan (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no       TEXT NOT NULL,
  operation      TEXT,
  planned_start  TEXT,
  planned_finish TEXT,
  imported_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Отчёт по отрезкам: сколько сделано по каждому отрезку заказа
CREATE TABLE IF NOT EXISTS segments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no    TEXT NOT NULL,
  segment     TEXT,
  plan_qty    REAL,
  done_qty    REAL,
  report_date TEXT,
  imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_order ON promise_history(order_no);
CREATE INDEX IF NOT EXISTS idx_plan_order ON production_plan(order_no);
CREATE INDEX IF NOT EXISTS idx_segments_order ON segments(order_no);
`;

function openDb(file) {
  const target = file || process.env.SALESVISOR_DB || path.join(__dirname, '..', 'data', 'salesvisor.db');
  if (target !== ':memory:') fs.mkdirSync(path.dirname(target), { recursive: true });
  const db = new Database(target);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  db.exec(SCHEMA);
  return db;
}

function getSettings(db) {
  const rows = db.prepare('SELECT key, value FROM settings').all();
  const s = Object.fromEntries(rows.map(r => [r.key, r.value]));
  return {
    // Сколько дней нужно между окончанием производства и обещанной датой (упаковка, отгрузка, доставка)
    shipBufferDays: Number(s.shipBufferDays ?? 2),
    // Если запас меньше этого числа дней, заказ помечается «Внимание»
    warnDays: Number(s.warnDays ?? 3),
  };
}

function saveSettings(db, values) {
  const stmt = db.prepare('INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value');
  for (const key of ['shipBufferDays', 'warnDays']) {
    if (values[key] !== undefined && Number.isFinite(Number(values[key]))) stmt.run(key, String(Number(values[key])));
  }
  return getSettings(db);
}

module.exports = { openDb, getSettings, saveSettings };
