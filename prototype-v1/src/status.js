const { daysBetween } = require('./dates');

const STATUS = {
  shipped: { code: 'shipped', label: 'Отгружен', rank: 6 },
  cancelled: { code: 'cancelled', label: 'Отменён', rank: 7 },
  overdue: { code: 'overdue', label: 'Просрочен', rank: 0 },
  risk: { code: 'risk', label: 'Не успеваем', rank: 1 },
  noplan: { code: 'noplan', label: 'Нет в плане', rank: 2 },
  warn: { code: 'warn', label: 'Мало запаса', rank: 3 },
  ok: { code: 'ok', label: 'В срок', rank: 4 },
};

// Сводит заказ, план производства и отчёт по отрезкам в одну строку с оценкой срока.
function evaluateOrder(order, planRows, segmentRows, settings, today) {
  const planFinish = planRows.reduce((max, r) => (r.planned_finish && (!max || r.planned_finish > max) ? r.planned_finish : max), null);

  const planQty = segmentRows.reduce((s, r) => s + (Number(r.plan_qty) || 0), 0);
  const doneQty = segmentRows.reduce((s, r) => s + (Number(r.done_qty) || 0), 0);
  const progress = planQty > 0 ? Math.min(1, doneQty / planQty) : null;
  const lastReport = segmentRows.reduce((max, r) => (r.report_date && (!max || r.report_date > max) ? r.report_date : max), null);

  const promised = order.current_promised_date;
  // Запас: сколько дней остаётся между плановой готовностью (+ отгрузка) и обещанной датой
  const reserveDays = planFinish && promised ? daysBetween(planFinish, promised) - settings.shipBufferDays : null;
  const shiftDays = daysBetween(order.first_promised_date, promised);
  const daysLeft = promised ? daysBetween(today, promised) : null;
  const complete = progress !== null && progress >= 1;

  let status;
  const reasons = [];
  if (order.cancelled) status = STATUS.cancelled;
  else if (order.shipped_date) status = STATUS.shipped;
  else if (promised && daysLeft < 0) {
    status = STATUS.overdue;
    reasons.push(`Обещанная дата прошла ${-daysLeft} дн. назад, отгрузки нет`);
  } else if (!planFinish && !complete) {
    status = STATUS.noplan;
    reasons.push('Заказа нет в плане производства');
  } else if (!complete && reserveDays !== null && reserveDays < 0) {
    status = STATUS.risk;
    reasons.push(`План производства заканчивается позже, чем нужно для отгрузки, на ${-reserveDays} дн.`);
  } else if (!complete && reserveDays !== null && reserveDays < settings.warnDays) {
    status = STATUS.warn;
    reasons.push(`Запас до обещанной даты всего ${reserveDays} дн.`);
  } else {
    status = STATUS.ok;
  }

  if (!complete && planFinish && today > planFinish && !order.shipped_date && !order.cancelled) {
    reasons.push('Плановая дата готовности уже прошла, а по отрезкам заказ не закончен');
    if (status === STATUS.ok || status === STATUS.warn) status = STATUS.risk;
  }
  if (!promised) reasons.push('Не указана обещанная дата');

  return {
    ...order,
    plan_finish: planFinish,
    progress,
    done_qty: doneQty,
    plan_qty: planQty,
    last_report: lastReport,
    reserve_days: reserveDays,
    shift_days: shiftDays,
    days_left: daysLeft,
    status: status.code,
    status_label: status.label,
    status_rank: status.rank,
    reasons,
  };
}

module.exports = { evaluateOrder, STATUS };
