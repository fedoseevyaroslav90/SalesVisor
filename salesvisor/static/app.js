(() => {
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = iso => (iso ? iso.slice(0, 10).split('-').reverse().join('.') : '—');
  const fmtDT = iso => (iso ? new Date(iso).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '');
  const store = {
    get: k => { try { return localStorage.getItem(k) || ''; } catch { return ''; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* без хранилища */ } },
  };
  const COLOR_NAME = { red: 'красный', yellow: 'жёлтый', green: 'зелёный' };
  const LIMIT = 400;
  const state = { meta: {}, orders: [], color: '', overdue: false, changes: [] };

  async function api(url, opts) {
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || `Ошибка ${r.status}`);
    return d;
  }

  // ---------- вкладки ----------
  document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
    if (b.dataset.tab === 'changes') loadChanges();
  }));

  // ---------- справочники ----------
  async function loadMeta() {
    state.meta = await api('/api/meta');
    const fill = (sel, items, saved) => {
      sel.innerHTML = '<option value="">Все</option>' + items.map(v => `<option>${esc(v)}</option>`).join('');
      sel.value = items.includes(saved) ? saved : '';
    };
    fill($('#manager'), state.meta.managers, store.get('manager'));
    fill($('#dept'), state.meta.depts, store.get('dept'));
    const l = state.meta.last_load || {};
    $('#lastLoad').textContent = [l.segments && 'отрезки ' + fmtDT(l.segments), l.svetofor && 'светофор ' + fmtDT(l.svetofor)].filter(Boolean).join(' · ');
    $('#mbState').textContent = state.meta.metabase_ready ? 'Metabase подключён. Данные обновляются по расписанию, кнопка обновит их сразу.' : 'Нужен API-ключ Metabase в настройках сервера (METABASE_API_KEY).';
  }

  // ---------- заказы ----------
  async function loadOrders() {
    const qs = new URLSearchParams({
      scope: $('#scope').value,
      manager: $('#manager').value, dept: $('#dept').value, q: $('#search').value.trim(),
    });
    state.orders = await api('/api/orders?' + qs);
    render();
  }

  function render() {
    const all = state.orders;
    const n = c => all.filter(o => o.color === c).length;
    const late = all.filter(o => o.overdue).length;
    const tiles = [
      ['', 'Заказов', all.length],
      ['late', 'С просрочкой', late],
      ['red', 'Красные', n('red')],
      ['yellow', 'Жёлтые', n('yellow')],
      ['green', 'Зелёные', n('green')],
      ['none', 'Без светофора', all.filter(o => !o.color).length],
      ['nolink', 'Без связи с Битрикс24', all.filter(o => !o.bitrix_task && !o.bitrix_deal).length],
    ];
    $('#tiles').innerHTML = tiles.map(([code, label, v]) => {
      const active = code === 'late' ? state.overdue : !state.overdue && state.color === code;
      return `<button class="tile ${code} ${active ? 'active' : ''}" data-code="${code}"><div class="n">${v}</div><div class="l">${label}</div></button>`;
    }).join('');
    document.querySelectorAll('.tile').forEach(t => t.addEventListener('click', () => {
      const c = t.dataset.code;
      state.overdue = c === 'late';
      state.color = c === 'late' ? '' : c;
      render();
    }));

    const rows = all.filter(o => (state.overdue ? o.overdue : true) &&
      (!state.color || (state.color === 'none' ? !o.color : state.color === 'nolink' ? !o.bitrix_task && !o.bitrix_deal : o.color === state.color)));
    $('#empty').hidden = rows.length > 0;
    $('#empty').textContent = all.length ? 'Под фильтр ничего не попало.' : 'Данных пока нет. Загрузите выгрузки на вкладке «Загрузка».';
    $('#shown').textContent = rows.length > LIMIT ? `Показаны первые ${LIMIT} из ${rows.length}. Уточните фильтр или поиск.` : '';
    $('#orders tbody').innerHTML = rows.slice(0, LIMIT).map(o => {
      const ready = o.segments_total ? Math.round(100 * o.segments_ready / o.segments_total) : null;
      const task = [bxLink('task', o.bitrix_task), bxLink('deal', o.bitrix_deal)].filter(Boolean).join('<br>');
      return `<tr data-no="${esc(o.order_no)}">
        <td><span class="dot ${o.color || ''}" title="${esc(COLOR_NAME[o.color] || 'нет данных светофора')}"></span></td>
        <td><b>${esc(o.order_no)}</b>${o.comments ? ` <span class="sub" title="Комментарии">💬${o.comments}</span>` : ''}${o.overdue ? `<div class="late-tag">просрочено поз.: ${o.overdue}</div>` : ''}</td>
        <td>${esc(o.customer || '—')}</td>
        <td>${esc(o.sales_dept || '—')}<div class="sub">${esc(o.manager || '')}</div></td>
        <td>${o.positions} ${o.red ? `<span class="cnt red">●${o.red}</span>` : ''}${o.yellow ? `<span class="cnt yellow">●${o.yellow}</span>` : ''}</td>
        <td>${esc(o.first_decade || '—')}</td>
        <td>${esc(o.current_decade || '—')}</td>
        <td class="num ${o.max_shift > 30 ? 'neg' : ''}">${o.max_shift == null ? '—' : o.max_shift + ' дн.'}</td>
        <td>${ready === null ? '<span class="muted">нет</span>' : `<span class="bar"><i style="width:${ready}%"></i></span><span class="pct">${o.segments_ready}/${o.segments_total}</span>`}</td>
        <td>${task}</td>
      </tr>`;
    }).join('');
    document.querySelectorAll('#orders tbody tr').forEach(tr => tr.addEventListener('click', () => openOrder(tr.dataset.no)));
  }

  let timer;
  const reload = () => { clearTimeout(timer); timer = setTimeout(() => loadOrders().catch(showErr), 250); };
  $('#manager').addEventListener('change', e => { store.set('manager', e.target.value); reload(); });
  $('#dept').addEventListener('change', e => { store.set('dept', e.target.value); reload(); });
  $('#search').addEventListener('input', reload);
  $('#scope').addEventListener('change', reload);

  function bxLink(kind, id) {
    if (!id) return '';
    const tpl = kind === 'task' ? state.meta.bitrix_task_url : state.meta.bitrix_deal_url;
    const label = (kind === 'task' ? 'задача ' : 'сделка ') + id;
    return tpl ? `<a href="${esc(tpl.replace('{id}', id))}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(label)}</a>` : esc(label);
  }

  // ---------- карточка заказа ----------
  const FLAGS = [['produced', 'П', 'Произведён'], ['stock', 'С', 'На складе'], ['ready', 'Г', 'Готов к отгрузке'], ['in_transit', 'В', 'В пути'], ['shipped', 'О', 'Отгружен'], ['invoiced', 'Ф', 'Отфактурирован']];

  async function openOrder(no) {
    const d = await api('/api/orders/' + encodeURIComponent(no));
    const p0 = d.positions.find(p => p.customer) || d.positions[0];
    const posRows = d.positions.map(p => `
      <tr class="pos" data-pos="${esc(p.pos)}">
        <td><span class="dot ${p.color || ''}"></span></td>
        <td><b>${esc(p.pos)}</b></td>
        <td>${esc(p.product || '')}</td>
        <td>${esc(p.first_decade || '—')}</td>
        <td>${esc(p.current_decade || '—')}${p.overdue ? ' <span class="late-tag">просрочено</span>' : ''}</td>
        <td class="num">${p.shift_days == null ? '—' : p.shift_days}</td>
        <td>${fmt(p.required_date)}</td>
        <td>${fmt(p.plan_ship_date || p.invoice_plan_date)}</td>
        <td>${esc(p.stage)}</td>
        <td>${p.segments.length ? `<span class="linkish">${p.segments.length}</span>` : '—'}</td>
      </tr>
      <tr class="seg" data-for="${esc(p.pos)}" hidden><td></td><td colspan="9">
        ${p.segments.map(s => `<div>№${esc(s.seg_no)}: ${s.length ?? '—'} ${esc(s.unit || '')} <span class="flags">${FLAGS.map(([k, l, t]) => `<span class="${s[k] ? 'on' : ''}" title="${t}">${l}</span>`).join('')}</span>
          ${s.fact_ship_date ? 'отгружен ' + fmt(s.fact_ship_date) : ''} ${s.prod_order ? '· зак. на пр-во ' + esc(s.prod_order) : ''}</div>`).join('')}
        ${p.reject_text ? `<div>Причина отклонения: ${esc(p.reject_code || '')} ${esc(p.reject_text)}</div>` : ''}
      </td></tr>`).join('');

    $('#drawerBody').innerHTML = `
      <h2>Заказ ${esc(no)}</h2>
      <div class="muted">${esc(p0.customer || '')} · ${esc(p0.sales_dept || '')} · менеджер ${esc(p0.manager || '—')}${p0.bitrix_raw ? ' · Битрикс: ' + esc(p0.bitrix_raw) : ''}</div>

      <h3>Битрикс24</h3>
      <div id="bxBox">
        <div>${d.bitrix.task_id || d.bitrix.deal_id ? [bxLink('task', d.bitrix.task_id), bxLink('deal', d.bitrix.deal_id)].filter(Boolean).join(' · ') : '<span class="late-tag">Заказ не связан с Битрикс24</span>'}
          ${d.bitrix.manual ? `<span class="sub">указал ${esc(d.bitrix.manual.set_by || '')} ${fmtDT(d.bitrix.manual.set_at)}</span>` : d.bitrix.sap_task ? '<span class="sub">номер задачи взят из SAP</span>' : ''}</div>
        <div id="bxLive" class="sub"></div>
        <form id="bxForm" class="form-row" style="margin-top:6px">
          <input type="text" name="task" placeholder="№ задачи" value="${esc(d.bitrix.manual ? d.bitrix.manual.task_id || '' : '')}" style="flex:0 0 130px;min-width:0">
          <input type="text" name="deal" placeholder="№ сделки" value="${esc(d.bitrix.manual ? d.bitrix.manual.deal_id || '' : '')}" style="flex:0 0 130px;min-width:0">
          <button class="secondary" type="submit">Сохранить связь</button>
          <span class="error-text" id="bxErr"></span>
        </form>
      </div>

      <h3>Позиции</h3>
      <div class="table-wrap"><table class="mini">
        <thead><tr><th></th><th>Поз.</th><th>Изделие</th><th>Первая декада</th><th>Текущая декада</th><th>Смещ., дн.</th><th>Треб. дата</th><th>План отгрузки</th><th>Этап</th><th>Отрезки</th></tr></thead>
        <tbody>${posRows}</tbody>
      </table></div>

      <h3>История изменений</h3>
      ${d.changes.length ? `<table class="mini"><thead><tr><th>Когда</th><th>Поз.</th><th>Что</th><th>Было</th><th>Стало</th></tr></thead><tbody>
        ${d.changes.map(c => `<tr><td>${fmtDT(c.at)}</td><td>${esc(c.pos)}</td><td>${esc(c.field_name)}</td><td>${esc(fmtVal(c.old))}</td><td>${esc(fmtVal(c.new))}</td></tr>`).join('')}
      </tbody></table>` : '<p class="muted">Изменений между выгрузками пока не было.</p>'}

      <h3>Комментарии</h3>
      <form id="commentForm">
        <textarea name="text" placeholder="Что происходит с заказом, о чём договорились с клиентом" required></textarea>
        <div class="form-row" style="margin-top:8px">
          <input type="text" name="author" placeholder="Ваше имя" value="${esc(store.get('author'))}">
          <button class="primary" type="submit">Добавить</button>
        </div>
      </form>
      ${d.comments.map(c => `<div style="margin:10px 0"><b>${esc(c.author || '')}</b> <span class="sub">${fmtDT(c.created_at)}${c.pos ? ', поз. ' + esc(c.pos) : ''}</span><div>${esc(c.text)}</div></div>`).join('')}`;
    $('#drawer').hidden = false;

    document.querySelectorAll('#drawerBody tr.pos').forEach(tr => tr.addEventListener('click', () => {
      const seg = document.querySelector(`#drawerBody tr.seg[data-for="${CSS.escape(tr.dataset.pos)}"]`);
      if (seg) seg.hidden = !seg.hidden;
    }));
    $('#bxForm').addEventListener('submit', async e => {
      e.preventDefault();
      const f = e.target;
      try {
        await api(`/api/orders/${encodeURIComponent(no)}/bitrix`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: f.task.value, deal_id: f.deal.value, author: store.get('author') }),
        });
        openOrder(no);
        loadOrders();
      } catch (err) { $('#bxErr').textContent = err.message; }
    });
    if (state.meta.bitrix_ready && (d.bitrix.task_id || d.bitrix.deal_id)) {
      $('#bxLive').textContent = 'Запрашиваем Битрикс24…';
      api(`/api/orders/${encodeURIComponent(no)}/bitrix/live`).then(b => {
        const parts = [];
        if (b.task) parts.push(`Задача «${b.task.title || ''}»: ${b.task.status || ''}, ответственный ${b.task.responsible || '—'}, срок ${fmt(b.task.deadline)}`);
        if (b.deal) parts.push(`Сделка «${b.deal.title || ''}», стадия ${b.deal.stage || '—'}`);
        if (b.task_error) parts.push('Задача: ' + b.task_error);
        if (b.deal_error) parts.push('Сделка: ' + b.deal_error);
        const box = $('#bxLive');
        if (box) box.textContent = parts.join(' · ');
      }).catch(err => { const box = $('#bxLive'); if (box) box.textContent = err.message; });
    }
    $('#commentForm').addEventListener('submit', async e => {
      e.preventDefault();
      const f = e.target;
      store.set('author', f.author.value);
      await api(`/api/orders/${encodeURIComponent(no)}/comments`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: f.text.value, author: f.author.value }),
      });
      openOrder(no);
    });
  }

  const fmtVal = v => (v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? fmt(v) : COLOR_NAME[v] || v || '—');

  $('#closeDrawer').addEventListener('click', () => { $('#drawer').hidden = true; });
  $('#drawer').addEventListener('click', e => { if (e.target.id === 'drawer') $('#drawer').hidden = true; });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#drawer').hidden = true; });

  // ---------- изменения ----------
  async function loadChanges() {
    const qs = new URLSearchParams({ days: $('#days').value, manager: $('#manager').value });
    state.changes = await api('/api/changes?' + qs);
    renderChanges();
  }
  function renderChanges() {
    const f = $('#field').value;
    const rows = state.changes.filter(c => !f || c.field === f);
    $('#changesEmpty').hidden = rows.length > 0;
    $('#changes tbody').innerHTML = rows.map(c => `<tr data-no="${esc(c.order_no)}">
      <td>${fmtDT(c.at)}</td><td><b>${esc(c.order_no)}</b></td><td>${esc(c.pos)}</td><td>${esc(c.customer || '')}</td>
      <td>${esc(c.manager || '')}</td><td>${esc(c.field_name)}</td><td>${esc(fmtVal(c.old))}</td><td>${esc(fmtVal(c.new))}</td></tr>`).join('');
    document.querySelectorAll('#changes tbody tr').forEach(tr => tr.addEventListener('click', () => openOrder(tr.dataset.no)));
  }
  $('#days').addEventListener('change', loadChanges);
  $('#field').addEventListener('change', renderChanges);

  // ---------- загрузка ----------
  $('#file').addEventListener('change', async e => {
    const file = e.target.files[0];
    if (!file) return;
    const out = $('#loadResult');
    out.className = 'result';
    out.textContent = 'Загружаем, большие файлы обрабатываются до минуты…';
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await api('/api/upload', { method: 'POST', body: fd });
      out.textContent = `${r.source === 'svetofor' ? 'Светофор' : 'Отчёт по отрезкам'}: строк ${r.rows}, позиций ${r.positions}, новых ${r.new}, изменений ${r.changes}.`;
      await loadMeta();
      await loadOrders();
    } catch (err) {
      out.className = 'result error';
      out.textContent = err.message;
    }
    e.target.value = '';
  });
  $('#syncBtn').addEventListener('click', async () => {
    const out = $('#syncResult');
    out.className = 'result';
    out.textContent = 'Забираем данные из Metabase…';
    try {
      const r = await api('/api/sync', { method: 'POST' });
      out.textContent = r.map(x => `${x.source}: ${x.positions ?? ''} поз., изменений ${x.changes ?? x.comments_for_changes ?? 0}`).join('; ');
      await loadMeta();
      await loadOrders();
    } catch (err) {
      out.className = 'result error';
      out.textContent = err.message;
    }
  });

  function showErr(err) {
    $('#empty').hidden = false;
    $('#empty').textContent = 'Не удалось загрузить данные: ' + err.message;
  }

  loadMeta().then(loadOrders).catch(showErr);
})();
