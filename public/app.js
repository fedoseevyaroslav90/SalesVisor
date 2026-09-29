(() => {
  const $ = sel => document.querySelector(sel);
  const state = { orders: [], today: '', status: '', settings: {} };

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = iso => (iso ? iso.split('-').reverse().join('.') : '—');
  const store = {
    get: k => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* браузер без хранилища */ } },
  };

  async function api(url, opts) {
    const res = await fetch(url, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Ошибка ${res.status}`);
    return data;
  }

  // ---------- вкладки ----------
  document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
    if (b.dataset.tab === 'orders') loadOrders();
  }));

  // ---------- список заказов ----------
  async function loadOrders() {
    const data = await api('/api/orders');
    state.orders = data.orders;
    state.today = data.today;
    state.settings = data.settings;
    $('#today').textContent = 'Сегодня ' + fmt(data.today);

    const sel = $('#manager');
    const current = sel.value || store.get('manager') || '';
    const managers = [...new Set(state.orders.map(o => o.manager).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ru'));
    sel.innerHTML = '<option value="">Все заказы</option>' + managers.map(m => `<option>${esc(m)}</option>`).join('');
    sel.value = managers.includes(current) ? current : '';
    render();
  }

  function visibleOrders(ignoreStatus) {
    const manager = $('#manager').value;
    const q = $('#search').value.trim().toLowerCase();
    const showClosed = $('#showClosed').checked;
    return state.orders.filter(o => {
      if (manager && o.manager !== manager) return false;
      if (!showClosed && (o.status === 'shipped' || o.status === 'cancelled')) return false;
      if (!ignoreStatus && state.status && o.status !== state.status) return false;
      if (q && ![o.order_no, o.client, o.product].some(v => String(v || '').toLowerCase().includes(q))) return false;
      return true;
    });
  }

  const TILES = [
    ['', 'Всего в работе'],
    ['overdue', 'Просрочены'],
    ['risk', 'Не успеваем'],
    ['noplan', 'Нет в плане'],
    ['warn', 'Мало запаса'],
    ['ok', 'В срок'],
  ];

  function render() {
    const base = visibleOrders(true);
    const counts = base.reduce((m, o) => ({ ...m, [o.status]: (m[o.status] || 0) + 1 }), {});
    const moved = base.filter(o => o.shift_days > 0).length;
    $('#tiles').innerHTML = TILES.map(([code, label]) => `
      <button class="tile ${code} ${state.status === code ? 'active' : ''}" data-status="${code}">
        <div class="n">${code ? counts[code] || 0 : base.length}</div><div class="l">${label}</div>
      </button>`).join('') +
      `<div class="tile" title="Заказы, у которых обещанная дата позже первой"><div class="n">${moved}</div><div class="l">Сроки переносились</div></div>`;
    document.querySelectorAll('.tile[data-status]').forEach(t => t.addEventListener('click', () => {
      state.status = t.dataset.status;
      render();
    }));

    const rows = visibleOrders(false);
    $('#empty').hidden = rows.length > 0;
    $('#empty').textContent = state.orders.length ? 'Под фильтр ничего не попало.' : 'Заказов пока нет. Загрузите файлы на вкладке «Загрузка данных».';
    $('#orders tbody').innerHTML = rows.map(o => {
      const pct = o.progress === null ? null : Math.round(o.progress * 100);
      return `<tr data-no="${esc(o.order_no)}" title="${esc(o.reasons.join('\n'))}">
        <td><span class="badge ${o.status}">${esc(o.status_label)}</span></td>
        <td><b>${esc(o.order_no)}</b>${o.product ? `<div class="sub">${esc(o.product)}</div>` : ''}</td>
        <td>${esc(o.client || '—')}</td>
        <td>${esc(o.manager || '—')}</td>
        <td>${fmt(o.first_promised_date)}</td>
        <td>${fmt(o.current_promised_date)}${o.shift_days ? `<span class="shift">${o.shift_days > 0 ? '+' : ''}${o.shift_days} дн.</span>` : ''}${o.reschedules > 1 ? `<div class="sub">переносов: ${o.reschedules}</div>` : ''}</td>
        <td>${fmt(o.plan_finish)}</td>
        <td class="num ${o.reserve_days < 0 ? 'neg' : ''}">${o.reserve_days === null ? '—' : o.reserve_days + ' дн.'}</td>
        <td>${pct === null ? '<span class="muted">нет данных</span>' : `<span class="bar"><i style="width:${pct}%"></i></span><span class="pct">${pct}%</span>`}</td>
      </tr>`;
    }).join('');
    document.querySelectorAll('#orders tbody tr').forEach(tr => tr.addEventListener('click', () => openOrder(tr.dataset.no)));
  }

  $('#manager').addEventListener('change', e => { store.set('manager', e.target.value); render(); });
  $('#search').addEventListener('input', render);
  $('#showClosed').addEventListener('change', render);

  // ---------- карточка заказа ----------
  async function openOrder(no) {
    const d = await api('/api/orders/' + encodeURIComponent(no));
    const o = d.order;
    const pct = o.progress === null ? '—' : Math.round(o.progress * 100) + '%';
    $('#drawerBody').innerHTML = `
      <h2>Заказ ${esc(o.order_no)} <span class="badge ${o.status}">${esc(o.status_label)}</span></h2>
      <div class="muted">${esc(o.client || '')}${o.product ? ' · ' + esc(o.product) : ''}${o.qty ? ' · ' + esc(o.qty) + ' шт.' : ''}</div>
      ${o.reasons.length ? `<ul class="reasons">${o.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
      <div class="facts">
        <div><span>Менеджер</span>${esc(o.manager || '—')}</div>
        <div><span>Дата заказа</span>${fmt(o.order_date)}</div>
        <div><span>Первое обещание клиенту</span>${fmt(o.first_promised_date)}</div>
        <div><span>Обещано сейчас</span>${fmt(o.current_promised_date)}${o.shift_days ? ` <span class="shift">${o.shift_days > 0 ? '+' : ''}${o.shift_days} дн.</span>` : ''}</div>
        <div><span>План готовности</span>${fmt(o.plan_finish)}</div>
        <div><span>Запас с учётом отгрузки (${state.settings.shipBufferDays} дн.)</span>${o.reserve_days === null ? '—' : o.reserve_days + ' дн.'}</div>
        <div><span>Готовность по отрезкам</span>${pct}${o.plan_qty ? ` (${o.done_qty} из ${o.plan_qty})` : ''}</div>
        <div><span>Последний отчёт</span>${fmt(o.last_report)}</div>
        <div><span>Отгружен</span>${fmt(o.shipped_date)}</div>
      </div>

      <h3>История обещанных дат</h3>
      ${d.history.length ? `<ul class="timeline">${d.history.map(h => `<li><b>${fmt(h.promised_date)}</b> — ${esc(h.reason || '')}
        <div class="sub">${h.source === 'manual' ? 'вручную' : 'из файла'}, ${esc(new Date(h.recorded_at.replace(' ', 'T') + 'Z').toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }))}</div></li>`).join('')}</ul>` : '<p class="muted">Обещанная дата не указана.</p>'}

      <h3>Перенести обещанную дату</h3>
      <form id="promiseForm">
        <div class="form-row">
          <input type="date" name="date" required>
          <input type="text" name="reason" placeholder="Причина переноса и с кем согласовано" required>
          <button class="primary" type="submit">Перенести</button>
        </div>
        <div class="error-text" id="promiseErr"></div>
      </form>

      <h3>План производства</h3>
      ${d.plan.length ? `<table class="mini"><thead><tr><th>Операция</th><th>Начало</th><th>Окончание</th></tr></thead><tbody>
        ${d.plan.map(p => `<tr><td>${esc(p.operation || '—')}</td><td>${fmt(p.planned_start)}</td><td>${fmt(p.planned_finish)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Заказа нет в плане.</p>'}

      <h3>Отчёт по отрезкам</h3>
      ${d.segments.length ? `<table class="mini"><thead><tr><th>Отрезок</th><th>План</th><th>Факт</th><th>Дата</th></tr></thead><tbody>
        ${d.segments.map(s => `<tr><td>${esc(s.segment || '—')}</td><td class="num">${esc(s.plan_qty ?? '—')}</td><td class="num">${esc(s.done_qty ?? '—')}</td><td>${fmt(s.report_date)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Данных по отрезкам нет.</p>'}

      <h3>Комментарий и отгрузка</h3>
      <form id="updateForm">
        <textarea name="comment" placeholder="Что сейчас происходит с заказом">${esc(o.comment || '')}</textarea>
        <div class="form-row" style="margin-top:8px">
          <label>Дата отгрузки <input type="date" name="shipped" value="${esc(o.shipped_date || '')}"></label>
          <label><input type="checkbox" name="cancelled" ${o.cancelled ? 'checked' : ''}> Заказ отменён</label>
          <button class="primary" type="submit">Сохранить</button>
        </div>
        <div class="error-text" id="updateErr"></div>
      </form>`;
    $('#drawer').hidden = false;

    $('#promiseForm').addEventListener('submit', async e => {
      e.preventDefault();
      const f = e.target;
      try {
        await api(`/api/orders/${encodeURIComponent(no)}/promise`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ date: f.date.value, reason: f.reason.value }) });
        await loadOrders();
        openOrder(no);
      } catch (err) { $('#promiseErr').textContent = err.message; }
    });
    $('#updateForm').addEventListener('submit', async e => {
      e.preventDefault();
      const f = e.target;
      try {
        await api(`/api/orders/${encodeURIComponent(no)}/update`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ comment: f.comment.value, shipped_date: f.shipped.value || null, cancelled: f.cancelled.checked }) });
        await loadOrders();
        openOrder(no);
      } catch (err) { $('#updateErr').textContent = err.message; }
    });
  }

  $('#closeDrawer').addEventListener('click', () => { $('#drawer').hidden = true; });
  $('#drawer').addEventListener('click', e => { if (e.target.id === 'drawer') $('#drawer').hidden = true; });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#drawer').hidden = true; });

  // ---------- загрузка файлов ----------
  document.querySelectorAll('.card[data-kind]').forEach(card => {
    const kind = card.dataset.kind;
    card.insertAdjacentHTML('beforeend', `
      <div class="actions">
        <label class="btn">Выбрать файл<input type="file" accept=".xlsx,.csv,.txt" hidden></label>
        <a href="/api/templates/${kind}">Скачать шаблон</a>
      </div>
      <div class="result"></div>`);
    const input = card.querySelector('input[type=file]');
    const out = card.querySelector('.result');
    input.addEventListener('change', async () => {
      if (!input.files[0]) return;
      const fd = new FormData();
      fd.append('file', input.files[0]);
      out.className = 'result';
      out.textContent = 'Загружаем…';
      try {
        const r = await api('/api/import/' + kind, { method: 'POST', body: fd });
        out.textContent = describeImport(r);
      } catch (err) {
        out.className = 'result error';
        out.textContent = err.message;
      }
      input.value = '';
    });
  });

  function describeImport(r) {
    if (r.kind === 'orders') {
      return `Строк: ${r.records}. Новых заказов: ${r.created}, обновлено: ${r.updated}, переносов обещанной даты: ${r.rescheduled}.`;
    }
    let s = `Строк: ${r.rows}, заказов: ${r.orders}.`;
    if (r.badDates) s += ` Без даты окончания: ${r.badDates}.`;
    if (r.unknownOrders) s += ` Заказов нет в списке заказов: ${r.unknownOrders} (загрузите их в первый файл).`;
    return s;
  }

  // ---------- настройки ----------
  async function loadSettings() {
    const s = await api('/api/settings');
    const f = $('#settings');
    f.shipBufferDays.value = s.shipBufferDays;
    f.warnDays.value = s.warnDays;
  }
  $('#settings').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target;
    await api('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shipBufferDays: f.shipBufferDays.value, warnDays: f.warnDays.value }) });
    $('#settingsSaved').textContent = 'Сохранено';
    setTimeout(() => { $('#settingsSaved').textContent = ''; }, 2000);
  });

  loadOrders();
  loadSettings();
})();
