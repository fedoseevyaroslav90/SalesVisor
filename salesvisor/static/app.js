(() => {
  const $ = s => document.querySelector(s);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = iso => (iso ? iso.slice(0, 10).split('-').reverse().join('.') : '—');
  const fmtDT = iso => (iso ? new Date(iso).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '');
  const store = {
    get: k => { try { return localStorage.getItem('salesvisor.' + k) || ''; } catch { return ''; } },
    set: (k, v) => { try { localStorage.setItem('salesvisor.' + k, v); } catch { /* без хранилища */ } },
  };
  const num = (v, d = 1) => (v ? v.toLocaleString('ru-RU', { maximumFractionDigits: d, minimumFractionDigits: 0 }) : '0');
  const COLOR_NAME = { red: 'красный', yellow: 'жёлтый', green: 'зелёный' };
  const state = { meta: {}, orders: [], color: '', overdue: false, changes: [] };

  async function api(url, opts) {
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => ({}));
    const detail = Array.isArray(d.detail) ? d.detail.map(x => x.msg).join('; ') : d.detail;
    if (!r.ok) throw new Error(detail || `Ошибка ${r.status}`);
    return d;
  }

  // ---------- вкладки ----------
  document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
    if (b.dataset.tab === 'changes') loadChanges();
    if (b.dataset.tab === 'day') loadDay().catch(showDayErr);
    if (b.dataset.tab === 'disp') loadDisp().catch(err => { $('#dispEmpty').hidden = false; $('#dispEmpty').textContent = 'Не удалось загрузить: ' + err.message; });
  }));

  // ---------- справочники ----------
  async function loadMeta() {
    state.meta = await api('api/meta');
    const fill = (sel, items, saved) => {
      sel.innerHTML = '<option value="">Все</option>' + items.map(v => `<option>${esc(v)}</option>`).join('');
      sel.value = items.includes(saved) ? saved : '';
    };
    fill($('#manager'), state.meta.managers, store.get('manager'));
    fill($('#dept'), state.meta.depts, store.get('dept'));
    fill($('#dayLine'), state.meta.lines || [], '');
    const line = $('#fLine').value;  // справочники перечитываются после загрузки — выбор не теряем
    $('#fLine').innerHTML = '<option value="">Все</option><option value="-">Не в плане производства</option>'
      + (state.meta.lines || []).map(v => `<option>${esc(v)}</option>`).join('');
    $('#fLine').value = line;
    const rej = $('#fReject').value;
    $('#fReject').innerHTML = '<option value="">Любая</option>'
      + (state.meta.rejects || []).map(r => `<option value="${esc(r.code)}">${esc(r.code)} — ${esc(r.text || '')}</option>`).join('')
      + '<option value="-">(пусто) — принят в производство</option><option value="!Z6,Z7">Кроме прогноза и бизнес-плана (Z6, Z7)</option>';
    $('#fReject').value = rej;
    $('#customerList').innerHTML = (state.meta.customers || []).map(v => `<option value="${esc(v)}">`).join('');
    const loadTab = document.querySelector('.tabs button[data-tab="load"]');
    if (loadTab) loadTab.hidden = state.meta.can_upload === false;
    $('#portalLink').hidden = !state.meta.portal_user;  // открыт через портал «Инкаб ИИ» — ссылка назад
    if (!$('#dayDate').value) $('#dayDate').value = state.meta.today;
    const l = state.meta.last_load || {};
    $('#lastLoad').textContent = [l.segments && 'отрезки ' + fmtDT(l.segments), l.svetofor && 'светофор ' + fmtDT(l.svetofor),
      l.plan && 'план ' + fmtDT(l.plan), l.dispatcher && 'диспетчерский ' + fmtDT(l.dispatcher)].filter(Boolean).join(' · ');
    $('#mbState').textContent = state.meta.metabase_ready ? 'Metabase подключён. Данные обновляются по расписанию, кнопка обновит их сразу.'
      : state.meta.import_dir ? 'Выгрузки приходят в папку на сервере и забираются по расписанию. Кнопка заберёт их сразу.'
      : 'Нужен API-ключ Metabase (METABASE_API_KEY) или папка выгрузок (IMPORT_DIR) в настройках сервера.';
  }

  // ---------- заказы и позиции: отбор, сортировка, ссылка на отбор, выгрузка ----------
  const PAGE = 400, POS_PAGE = 300;
  Object.assign(state, { view: 'orders', sort: '', posSort: '', shown: PAGE, positions: [], posTotal: 0, seq: 0 });
  const flagInputs = () => [...document.querySelectorAll('#filters [data-flag]')];
  const collator = new Intl.Collator('ru', { numeric: true, sensitivity: 'base' });

  // Сортировка на экране: пустые значения — всегда внизу; строки — по-русски и с числами «по смыслу»
  function sortRows(rows, sort, keyOf) {
    if (!sort) return rows;
    const desc = sort[0] === '-', key = keyOf(desc ? sort.slice(1) : sort);
    const filled = [], empty = [];
    for (const r of rows) { const k = key(r); (k === null || k === undefined || k === '' ? empty : filled).push([k, r]); }
    filled.sort((a, b) => (typeof a[0] === 'number' && typeof b[0] === 'number' ? a[0] - b[0]
      : collator.compare(String(a[0]), String(b[0]))) * (desc ? -1 : 1));
    return filled.map(x => x[1]).concat(empty.map(x => x[1]));
  }
  // Щелчок по заголовку: по возрастанию → по убыванию → порядок по умолчанию
  function bindSort(table, get, set) {
    document.querySelectorAll(`${table} th[data-sort]`).forEach(th => th.addEventListener('click', () => {
      const f = th.dataset.sort, cur = get();
      set(cur === f ? '-' + f : cur === '-' + f ? '' : f);
    }));
  }
  function markSort(table, sort) {
    document.querySelectorAll(`${table} th[data-sort]`).forEach(th => {
      th.classList.toggle('sort-asc', sort === th.dataset.sort);
      th.classList.toggle('sort-desc', sort === '-' + th.dataset.sort);
      th.title = th.title.replace(/ · сортировка.*$/, '') + ' · сортировка — щелчок по заголовку';
    });
  }
  const RANK = { red: 0, yellow: 1, green: 2 };
  const orderKey = f => f === 'color' ? (o => RANK[o.color] ?? 3)
    : f === 'ready' ? (o => (o.segments_total ? o.segments_ready / o.segments_total : null))
    : f === 'manager' ? (o => [o.sales_dept, o.manager].filter(Boolean).join(' ') || null)
    : (o => o[f]);

  // Периоды отбора по сроку: декада — 1–10, 11–20, 21–конец месяца
  const isoDate = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  function periodRange(code) {
    const t = new Date((state.meta.today || isoDate(new Date())) + 'T00:00');
    const y = t.getFullYear(), m = t.getMonth(), day = t.getDate(), k = day <= 10 ? 1 : day <= 20 ? 2 : 3;
    const dec = (mm, kk) => [new Date(y, mm, [1, 11, 21][kk - 1]), kk < 3 ? new Date(y, mm, kk * 10) : new Date(y, mm + 1, 0)];
    const r = {
      past: [null, new Date(y, m, day - 1)],
      dec: dec(m, k),
      nextdec: k < 3 ? dec(m, k + 1) : dec(m + 1, 1),
      month: [new Date(y, m, 1), new Date(y, m + 1, 0)],
      prevmonth: [new Date(y, m - 1, 1), new Date(y, m, 0)],
      nextmonth: [new Date(y, m + 1, 1), new Date(y, m + 2, 0)],
    }[code];
    return r ? r.map(d => (d ? isoDate(d) : '')) : null;
  }
  const PERIODS = [['#fDue', '#fDuePeriod', '#fDueFrom', '#fDueTo', 'due'], ['#fFirst', '#fFirstPeriod', '#fFirstFrom', '#fFirstTo', 'first']];

  function baseParams() { return { scope: $('#scope').value, manager: $('#manager').value, dept: $('#dept').value }; }
  function filterParams() {
    const p = {}, v = id => $(id).value.trim();
    if (v('#search')) p.q = v('#search');
    if (v('#fCustomer')) p.customer = v('#fCustomer');
    if (v('#fLine')) p.line = v('#fLine');
    if (v('#fStage')) p.stage = v('#fStage');
    for (const [sel, , from, to, key] of PERIODS) {
      const code = $(sel).value, r = code === 'custom' ? [v(from), v(to)] : code ? periodRange(code) : null;
      if (r && r[0]) p[key + '_from'] = r[0];
      if (r && r[1]) p[key + '_to'] = r[1];
    }
    if (v('#fShift')) p.shift_min = v('#fShift');
    if (v('#fReject')) p.reject = v('#fReject');
    const flags = flagInputs().filter(i => i.checked).map(i => i.dataset.flag);
    if (flags.length) p.flags = flags.join(',');
    return p;
  }
  // Словесное описание отбора — в первую строку выгрузки Excel
  function describeFilters() {
    const text = sel => { const el = $(sel); return el.tagName === 'SELECT' ? el.options[el.selectedIndex]?.text : el.value; };
    const parts = [state.view === 'orders' ? 'Заказы' : 'Позиции', text('#scope')];
    for (const [sel, name] of [['#manager', 'менеджер'], ['#dept', 'отдел'], ['#search', 'поиск'], ['#fCustomer', 'клиент'],
      ['#fLine', 'линия'], ['#fStage', 'этап'], ['#fShift', 'смещение от, дн.'], ['#fReject', 'причина откл.']]) if ($(sel).value) parts.push(`${name}: ${text(sel)}`);
    const p = filterParams();
    if (p.due_from || p.due_to) parts.push(`срок: ${p.due_from ? fmt(p.due_from) : '…'}–${p.due_to ? fmt(p.due_to) : '…'}`);
    if (p.first_from || p.first_to) parts.push(`первая дата: ${p.first_from ? fmt(p.first_from) : '…'}–${p.first_to ? fmt(p.first_to) : '…'}`);
    const flags = flagInputs().filter(i => i.checked).map(i => i.parentElement.textContent.trim());
    if (flags.length) parts.push(flags.join(', '));
    if (state.view === 'orders' && (state.color || state.overdue)) parts.push('плитка: ' + ($(`.tile[data-code="${state.overdue ? 'late' : state.color}"] .l`)?.textContent || ''));
    return 'Отбор: ' + parts.join('; ');
  }

  // Отбор живёт в адресе страницы (?…): ссылку можно отправить коллеге, после обновления страницы он сохраняется.
  // Не во фрагменте #…: заставка входа портала перезагружает страницу переходом на тот же адрес, а переход,
  // отличающийся только фрагментом, браузер страницей не перезагружает — открывший ссылку без сеанса завис бы.
  const URL_FIELDS = { scope: '#scope', manager: '#manager', dept: '#dept', q: '#search', customer: '#fCustomer', line: '#fLine',
    stage: '#fStage', due: '#fDue', due_from: '#fDueFrom', due_to: '#fDueTo', first: '#fFirst', first_from: '#fFirstFrom',
    first_to: '#fFirstTo', shift: '#fShift', reject: '#fReject' };
  function saveUrl() {
    const h = new URLSearchParams();
    if (state.view !== 'orders') h.set('view', state.view);
    for (const [k, sel] of Object.entries(URL_FIELDS)) if ($(sel).value && !(k === 'scope' && $(sel).value === 'open')) h.set(k, $(sel).value);
    const flags = flagInputs().filter(i => i.checked).map(i => i.dataset.flag);
    if (flags.length) h.set('flags', flags.join(','));
    if (state.view === 'orders' && (state.color || state.overdue)) h.set('tile', state.overdue ? 'late' : state.color);
    const sort = state.view === 'orders' ? state.sort : state.posSort;
    if (sort) h.set('sort', sort);
    const s = h.toString();
    history.replaceState(null, '', location.pathname + (s ? '?' + s : ''));
    store.set('lastView', s);  // открыл раздел без ссылки — вернётся его последний отбор
  }
  function readUrl() {
    // старые ссылки с #… тоже открываются
    const h = new URLSearchParams(location.search.slice(1) || location.hash.slice(1) || store.get('lastView'));
    if (![...h.keys()].length) return;
    for (const [k, sel] of Object.entries(URL_FIELDS)) if (h.has(k)) $(sel).value = h.get(k);
    const flags = (h.get('flags') || '').split(',');
    flagInputs().forEach(i => { i.checked = flags.includes(i.dataset.flag); });
    const tile = h.get('tile') || '';
    state.overdue = tile === 'late';
    state.color = tile === 'late' ? '' : tile;
    state.view = h.get('view') === 'positions' ? 'positions' : 'orders';
    if (state.view === 'orders') state.sort = h.get('sort') || ''; else state.posSort = h.get('sort') || '';
  }
  // Подсветка заданных фильтров, число их на кнопке «Отбор», видимость полей своего периода
  function markFilters() {
    let n = 0;
    for (const el of document.querySelectorAll('#filters select, #filters input:not([type=checkbox])')) {
      const on = !!el.value && !el.closest('.period[hidden]');
      el.classList.toggle('active-filter', on);
      n += on && !el.closest('.period') ? 1 : 0;
    }
    n += flagInputs().filter(i => i.checked).length;
    for (const [sel, span] of PERIODS) $(span).hidden = $(sel).value !== 'custom';
    $('#filtersCount').hidden = !n;
    $('#filtersCount').textContent = n;
  }

  async function loadOrders() {
    markFilters();
    saveUrl();
    const seq = ++state.seq;  // быстрый ввод: устаревший ответ не перерисовывает новый отбор
    if (state.view === 'positions') return loadPositions(false, seq);
    const orders = await api('api/orders?' + new URLSearchParams({ ...baseParams(), ...filterParams() }));
    if (seq !== state.seq) return;
    state.orders = orders;
    state.shown = PAGE;
    render();
  }
  async function loadPositions(append, seq = ++state.seq) {
    const qs = new URLSearchParams({ ...baseParams(), ...filterParams(), sort: state.posSort,
      offset: append ? state.positions.length : 0, limit: POS_PAGE });
    const d = await api('api/positions?' + qs);
    if (seq !== state.seq) return;
    state.positions = append ? state.positions.concat(d.rows) : d.rows;
    state.posTotal = d.total;
    renderPositions();
  }

  // срок наступает в ближайшие 7 дней, а отгружено ещё не всё (плитка «Мои заказы» пилота VOLS-Zakazy)
  function soon(o) {
    if (!o.nearest_due) return false;
    const today = state.meta.today || isoDate(new Date());
    const t = new Date(today + 'T00:00');
    t.setDate(t.getDate() + 7);
    return o.nearest_due >= today && o.nearest_due <= isoDate(t) && o.segments_ready < o.segments_total;
  }
  function tileRows(all) {
    return all.filter(o => (state.overdue ? o.overdue : true) &&
      (!state.color || (state.color === 'none' ? !o.color : state.color === 'soon' ? soon(o) : state.color === 'nolink' ? !o.bitrix_task && !o.bitrix_deal
        : state.color === 'quality' ? o.quality > 0 : o.color === state.color)));
  }
  function render() {
    const all = state.orders;
    const n = c => all.filter(o => o.color === c).length;
    const late = all.filter(o => o.overdue).length;
    const tiles = [
      ['', 'Заказов', all.length],
      ['late', 'С просрочкой', late],
      ['soon', 'Срок ≤ 7 дней', all.filter(soon).length],
      ['red', 'Красные', n('red')],
      ['yellow', 'Жёлтые', n('yellow')],
      ['green', 'Зелёные', n('green')],
      ['none', 'Без светофора', all.filter(o => !o.color).length],
      ['nolink', 'Без связи с Битрикс24', all.filter(o => !o.bitrix_task && !o.bitrix_deal).length],
      ['quality', 'С несоответствиями', all.filter(o => o.quality).length],
    ];
    $('#tiles').innerHTML = tiles.map(([code, label, v]) => {
      const active = code === 'late' ? state.overdue : !state.overdue && state.color === code;
      return `<button class="tile ${code} ${active ? 'active' : ''}" data-code="${code}"><div class="n">${v}</div><div class="l">${label}</div></button>`;
    }).join('');
    document.querySelectorAll('.tile').forEach(t => t.addEventListener('click', () => {
      const c = t.dataset.code;
      state.overdue = c === 'late';
      state.color = c === 'late' ? '' : c;
      state.shown = PAGE;
      saveUrl();
      render();
    }));

    const rows = sortRows(tileRows(all), state.sort, orderKey);
    const filtered = !!Object.keys(filterParams()).length;
    $('#found').textContent = filtered || state.color || state.overdue
      ? `Под отбор: ${rows.length} заказ(ов); строки считаются по подошедшим позициям.` : '';
    $('#empty').hidden = rows.length > 0;
    $('#empty').textContent = all.length || filtered ? 'Под отбор ничего не попало.' : 'Данных пока нет. Загрузите выгрузки на вкладке «Загрузка».';
    $('#orders tbody').innerHTML = rows.slice(0, state.shown).map(o => {
      const ready = o.segments_total ? Math.round(100 * o.segments_ready / o.segments_total) : null;
      const task = [bxLink('task', o.bitrix_task), bxLink('deal', o.bitrix_deal)].filter(Boolean).join('<br>');
      return `<tr data-no="${esc(o.order_no)}">
        <td><span class="dot ${COLOR_NAME[o.color] ? o.color : ''}" title="${esc(COLOR_NAME[o.color] || 'нет данных светофора')}"></span></td>
        <td><b>${esc(o.order_no)}</b>${o.comments ? ` <span class="sub" title="Комментарии">💬${o.comments}</span>` : ''}${o.overdue ? `<div class="late-tag">просрочено поз.: ${o.overdue}</div>` : ''}${o.quality ? `<div class="q-tag" title="Сообщения о качестве">несоответствий: ${o.quality}</div>` : ''}</td>
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
    const rest = rows.length - state.shown;
    $('#ordersMore').hidden = rest <= 0;
    $('#ordersMore button').textContent = `Показать ещё ${Math.min(rest, PAGE)} (всего ${rows.length})`;
    markSort('#orders', state.sort);
  }

  function renderPositions() {
    const rows = state.positions;
    $('#found').textContent = `Под отбор: ${state.posTotal} позиций` + (state.posTotal > rows.length ? `, показаны ${rows.length}` : '') + '.';
    $('#posEmpty').hidden = rows.length > 0;
    $('#posEmpty').textContent = 'Под отбор ничего не попало.';
    $('#positions tbody').innerHTML = rows.map(p => `<tr data-no="${esc(p.order_no)}">
        <td><span class="dot ${COLOR_NAME[p.color] ? p.color : ''}" title="${esc(COLOR_NAME[p.color] || 'нет данных светофора')}"></span></td>
        <td><b>${esc(p.order_no)}</b> / ${esc(p.pos)}</td>
        <td>${esc(p.customer || '—')}</td>
        <td>${esc(p.product || '')}</td>
        <td>${esc(p.manager || '')}</td>
        <td>${esc(p.first_decade || '—')}</td>
        <td>${esc(p.current_decade || fmt(p.due_date))}${p.overdue ? '<div class="late-tag">просрочено</div>' : ''}</td>
        <td class="num ${p.shift_days > 30 ? 'neg' : ''}">${p.shift_days ?? '—'}</td>
        <td>${fmt(p.plan_ship_date || p.invoice_plan_date)}</td>
        <td>${p.line ? `<b>${esc(p.line)}</b><div class="sub">${fmt(p.plan_end_date)} ${esc(p.plan_end_time || '')}</div>` : '<span class="muted">—</span>'}</td>
        <td>${esc(p.stage)}${p.reject_code ? ` <span class="sub" title="${esc(p.reject_text || 'причина отклонения')}">${esc(p.reject_code)}</span>` : ''}${p.disp_decade ? `<div class="sub">${esc(p.disp_decade)}</div>` : ''}</td>
        <td>${p.segments_total ? `${p.segments_ready}/${p.segments_total}` : '—'}</td>
        <td>${p.quality ? `<div class="q-tag">несоотв.: ${p.quality}</div>` : ''}${p.comments ? `<span class="sub" title="Комментарии к заказу">💬${p.comments}</span> ` : ''}${bxLink('task', p.bitrix_task)}</td>
      </tr>`).join('');
    document.querySelectorAll('#positions tbody tr').forEach(tr => tr.addEventListener('click', () => openOrder(tr.dataset.no)));
    const rest = state.posTotal - rows.length;
    $('#positionsMore').hidden = rest <= 0;
    $('#positionsMore button').textContent = `Показать ещё ${Math.min(rest, POS_PAGE)} (всего ${state.posTotal})`;
    markSort('#positions', state.posSort);
  }

  function setView(v) {
    state.view = v;
    document.querySelectorAll('.view-switch button').forEach(b => b.classList.toggle('on', b.dataset.view === v));
    $('#ordersWrap').hidden = v !== 'orders';
    $('#positionsWrap').hidden = v === 'orders';
    $('#tiles').hidden = v !== 'orders';
  }
  document.querySelectorAll('.view-switch button').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.view === state.view) return;
    setView(b.dataset.view);
    loadOrders().catch(showErr);
  }));
  bindSort('#orders', () => state.sort, s => { state.sort = s; state.shown = PAGE; saveUrl(); render(); });
  bindSort('#positions', () => state.posSort, s => { state.posSort = s; loadOrders().catch(showErr); });
  $('#ordersMore button').addEventListener('click', () => { state.shown += PAGE; render(); });
  $('#positionsMore button').addEventListener('click', () => loadPositions(true).catch(showErr));

  let timer;
  const reload = () => { clearTimeout(timer); timer = setTimeout(() => loadOrders().catch(showErr), 300); };
  $('#manager').addEventListener('change', e => { store.set('manager', e.target.value); reload(); });
  $('#dept').addEventListener('change', e => { store.set('dept', e.target.value); reload(); });
  $('#search').addEventListener('input', reload);
  $('#scope').addEventListener('change', reload);
  document.querySelectorAll('#filters select, #filters input').forEach(el => el.addEventListener(el.type === 'text' || el.type === 'number' || el.type === 'search' || el.id === 'fCustomer' ? 'input' : 'change', reload));
  $('#fReset').addEventListener('click', () => {
    document.querySelectorAll('#filters select, #filters input:not([type=checkbox])').forEach(el => { el.value = ''; });
    flagInputs().forEach(i => { i.checked = false; });
    state.color = ''; state.overdue = false;
    reload();
  });
  $('#filtersToggle').addEventListener('click', () => {
    const open = $('#filters').hidden;
    $('#filters').hidden = !open;
    $('#filtersToggle').setAttribute('aria-expanded', String(open));
    store.set('filtersClosed', open ? '' : '1');
  });
  if (store.get('filtersClosed')) { $('#filters').hidden = true; $('#filtersToggle').setAttribute('aria-expanded', 'false'); }

  $('#exportBtn').addEventListener('click', () => {
    const p = { ...baseParams(), ...filterParams(), view: state.view, note: describeFilters(),
      sort: state.view === 'orders' ? state.sort : state.posSort };
    if (state.view === 'orders') { if (state.overdue) p.overdue = 'true'; else if (state.color) p.color = state.color; }
    location.href = 'api/export.xlsx?' + new URLSearchParams(p);
  });
  // «Мои отборы»: именованные отборы в этом браузере (пресеты пилота: «мои просрочки», «мой отдел»)
  const savedViews = () => { try { return JSON.parse(store.get('views') || '[]'); } catch { return []; } };
  function fillSavedViews() {
    const list = savedViews();
    $('#savedViews').innerHTML = '<option value="">Мои отборы…</option>'
      + list.map((x, i) => `<option value="${i}">${esc(x.name)}</option>`).join('')
      + (list.length ? '<option value="del">Удалить отбор…</option>' : '');
  }
  $('#saveView').addEventListener('click', () => {
    const name = (window.prompt('Название отбора, например «Мои просрочки»:') || '').trim().slice(0, 60);
    if (!name) return;
    const list = savedViews().filter(x => x.name !== name);
    list.push({ name, query: location.search.slice(1) });
    store.set('views', JSON.stringify(list.slice(-30)));
    fillSavedViews();
  });
  $('#savedViews').addEventListener('change', e => {
    const v = e.target.value;
    e.target.value = '';
    if (v === 'del') {
      const name = (window.prompt('Какой отбор удалить? Название:\n' + savedViews().map(x => x.name).join('\n')) || '').trim();
      store.set('views', JSON.stringify(savedViews().filter(x => x.name !== name)));
      fillSavedViews();
      return;
    }
    const x = savedViews()[+v];
    if (!x) return;
    // отбор целиком из сохранённого: сначала всё сбросить, затем применить его адрес
    document.querySelectorAll('#filters select, #filters input:not([type=checkbox])').forEach(el => { el.value = ''; });
    ['#search', '#manager', '#dept'].forEach(id => { $(id).value = ''; });
    $('#scope').value = 'open';
    state.sort = ''; state.posSort = ''; state.color = ''; state.overdue = false;
    const q = x.query ?? x.hash ?? '';
    history.replaceState(null, '', location.pathname + (q ? '?' + q : ''));
    readUrl();
    setView(state.view);
    loadOrders().catch(showErr);
  });
  fillSavedViews();

  $('#copyLink').addEventListener('click', async () => {
    const b = $('#copyLink'), was = b.textContent;
    try { await navigator.clipboard.writeText(location.href); b.textContent = 'Ссылка скопирована'; }
    catch { window.prompt('Скопируйте ссылку на отбор:', location.href); }
    setTimeout(() => { b.textContent = was; }, 2000);
  });

  // Выгрузка таблицы на экране в CSV для Excel (разделитель «;», UTF-8 с BOM). Значения, похожие на формулы, экранируются
  function downloadCsv(name, header, rows) {
    const cell = v => {
      let s = String(v ?? '');
      if (/^[=+\-@]/.test(s) && !/^-?\d+([.,]\d+)?$/.test(s)) s = "'" + s;
      return /[;"\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    const text = '﻿' + [header, ...rows].map(r => r.map(cell).join(';')).join('\r\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
    a.download = name;
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }
  const stamp = () => new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '');
  // Строка «ищем везде» для поиска на вкладках: подстрока в любом из полей
  const matchText = (q, ...vals) => !q || vals.some(v => String(v ?? '').toLowerCase().includes(q));

  function bxLink(kind, id) {
    if (!id) return '';
    const tpl = kind === 'task' ? state.meta.bitrix_task_url : state.meta.bitrix_deal_url;
    const label = (kind === 'task' ? 'задача ' : 'сделка ') + id;
    return /^https:\/\//i.test(tpl || '') ? `<a href="${esc(tpl.replace('{id}', id))}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(label)}</a>` : esc(label);
  }

  // ---------- карточка заказа ----------
  const FLAGS = [['produced', 'П', 'Произведён'], ['stock', 'С', 'На складе'], ['ready', 'Г', 'Готов к отгрузке'], ['in_transit', 'В', 'В пути'], ['shipped', 'О', 'Отгружен'], ['invoiced', 'Ф', 'Отфактурирован']];

  async function openOrder(no) {
    const d = await api('api/orders/' + encodeURIComponent(no));
    const p0 = d.positions.find(p => p.customer) || d.positions[0];
    const posRows = d.positions.map(p => `
      <tr class="pos" data-pos="${esc(p.pos)}">
        <td><span class="dot ${COLOR_NAME[p.color] ? p.color : ''}"></span></td>
        <td><b>${esc(p.pos)}</b></td>
        <td>${esc(p.product || '')}</td>
        <td>${esc(p.first_decade || '—')}</td>
        <td>${esc(p.current_decade || '—')}${p.overdue ? ' <span class="late-tag">просрочено</span>' : ''}</td>
        <td class="num">${p.shift_days == null ? '—' : p.shift_days}</td>
        <td>${fmt(p.required_date)}</td>
        <td>${fmt(p.plan_ship_date || p.invoice_plan_date)}</td>
        <td>${p.line ? `<b>${esc(p.line)}</b><div class="sub">${fmt(p.plan_end_date)} ${esc(p.plan_end_time || '')}${p.plan_qty ? ` · MES ${num(p.plan_fact_qty, 2)} из ${num(p.plan_qty, 2)}` : ''}</div>` : '<span class="muted">—</span>'}</td>
        <td>${esc(p.stage)}</td>
        <td>${p.segments.length ? `<span class="linkish">${p.segments.length}</span>` : '—'}</td>
      </tr>
      <tr class="seg" data-for="${esc(p.pos)}" hidden><td></td><td colspan="10">
        ${p.plan_lines && p.plan_lines !== p.line ? `<div>Операции на линиях: ${esc(p.plan_lines)}</div>` : ''}
        ${p.dse ? `<div>ДСЕ ${esc(p.dse)}</div>` : ''}
        ${dispLine(p)}
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

      ${d.quality && d.quality.length ? `<h3>Несоответствия по качеству</h3>
      <table class="mini"><thead><tr><th>Сообщение</th><th>Поз.</th><th>Линия</th><th>Что выявлено</th><th>План. окончание</th><th>Появилось</th></tr></thead><tbody>
        ${d.quality.map(q => `<tr><td>${esc(q.msg_no)}</td><td>${esc(q.pos || '')}</td><td>${esc(q.line || '—')}</td><td><span class="q-tag">${esc(q.text || 'без описания')}</span></td><td>${fmt(q.plan_end_date)}</td><td>${fmtDT(q.first_seen_at)}</td></tr>`).join('')}
      </tbody></table>` : ''}

      <h3>Позиции</h3>
      <div class="table-wrap"><table class="mini">
        <thead><tr><th></th><th>Поз.</th><th>Изделие</th><th>Первая декада</th><th>Текущая декада</th><th>Смещ., дн.</th><th>Треб. дата</th><th>План отгрузки</th><th title="План производства: линия и плановое окончание">Линия</th><th>Этап</th><th>Отрезки</th></tr></thead>
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
          ${state.meta.portal_user ? `<input type="hidden" name="author" value="${esc(state.meta.portal_user)}">`
            : `<input type="text" name="author" placeholder="Ваше имя" value="${esc(store.get('author'))}">`}
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
        await api(`api/orders/${encodeURIComponent(no)}/bitrix`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: f.task.value, deal_id: f.deal.value, author: store.get('author') }),
        });
        openOrder(no);
        loadOrders();
      } catch (err) { $('#bxErr').textContent = err.message; }
    });
    if (state.meta.bitrix_ready && (d.bitrix.task_id || d.bitrix.deal_id)) {
      $('#bxLive').textContent = 'Запрашиваем Битрикс24…';
      api(`api/orders/${encodeURIComponent(no)}/bitrix/live`).then(b => {
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
      if (!state.meta.portal_user) store.set('author', f.author.value);
      await api(`api/orders/${encodeURIComponent(no)}/comments`, {
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

  function dispLine(p) {
    const parts = [
      p.disp_decade && esc(p.disp_decade),
      p.disp_counted === false && 'не считать',
      p.disp_ready && esc(p.disp_ready),
      p.disp_batch && 'партия ' + esc(p.disp_batch),
    ].filter(Boolean);
    return parts.length ? `<div>Диспетчерский: ${parts.join(' · ')}</div>` : '';
  }

  // ---------- план на день ----------
  state.dayLine = '';
  async function loadDay() {
    const qs = new URLSearchParams({
      date: $('#dayDate').value || state.meta.today || '',
      manager: $('#manager').value, dept: $('#dept').value, backlog: $('#dayBacklog').checked,
    });
    state.day = await api('api/day?' + qs);
    renderDay();
  }
  function showDayErr(err) { $('#dayEmpty').hidden = false; $('#dayEmpty').textContent = 'Не удалось загрузить план: ' + err.message; }
  function taskTag(kind, done, late) {
    const name = kind === 'make' ? 'произвести' : 'отгрузить';
    const cls = done ? 'done' : late ? 'late' : 'todo';
    return `<span class="task ${cls}">${done ? (kind === 'make' ? 'произведено' : 'отгружено') : name}</span>`;
  }
  function renderDay() {
    const d = state.day, what = $('#dayWhat').value, lineSel = $('#dayLine').value || state.dayLine;
    const s = d.summary;
    const hasPlan = (state.meta.lines || []).length > 0;
    $('#dayNote').textContent = (hasPlan ? 'Произвести — по ZPP context (линия, плановое окончание); сделано, если все отрезки произведены или факт MES дошёл до плана. Отгрузить — по плану отгрузки. '
      : 'План производства ещё не загружен: линии и плановое окончание появятся после загрузки ZPP context. Пока показан план отгрузки. ')
      + `Для менеджера и отдела действуют фильтры с вкладки «Заказы».`;
    const tiles = [
      ['', 'Позиций в плане', s.positions],
      ['green', 'Произвести: сделано', `${s.make_done}/${s.make}`],
      ['green', 'Отгрузить: сделано', `${s.ship_done}/${s.ship}`],
      ['late', 'Отстают от плана', s.late],
      ['quality', 'С несоответствиями', s.quality],
      ['late', 'Производство позже отгрузки', s.risk],
    ];
    $('#dayTiles').innerHTML = tiles.map(([c, l, v]) => `<div class="tile ${c}" style="cursor:default"><div class="n">${v}</div><div class="l">${l}</div></div>`).join('');
    $('#dayLines').innerHTML = d.lines.map(b => `<button class="line-chip ${lineSel === (b.line || '-') ? 'active' : ''}" data-line="${esc(b.line || '-')}">
      ${esc(b.line || 'линия не указана')} · <b>${b.make_done}/${b.make}</b>${b.km ? ` · ${b.km.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} км` : ''}</button>`).join('');
    document.querySelectorAll('.line-chip').forEach(c => c.addEventListener('click', () => {
      state.dayLine = state.dayLine === c.dataset.line ? '' : c.dataset.line;
      $('#dayLine').value = '';
      renderDay();
    }));
    const q = $('#daySearch').value.trim().toLowerCase();
    const rows = sortRows(d.positions.filter(r => (!what || (what === 'make' ? r.task_make : r.task_ship)) &&
      (!lineSel || (lineSel === '-' ? !r.line : r.line === lineSel)) &&
      matchText(q, r.order_no, r.customer, r.product, r.manager, r.line)), state.daySort, dayKey);
    state.dayRows = rows;
    $('#dayEmpty').hidden = rows.length > 0;
    $('#dayEmpty').textContent = q ? 'Под поиск ничего не попало.' : 'На этот день по плану ничего нет.';
    let prev = null;
    markSort('#dayTable', state.daySort);
    $('#dayTable tbody').innerHTML = rows.slice(0, 1500).map(r => {
      // группировка по линиям — только в порядке по умолчанию; при сортировке по столбцу строки идут подряд
      const head = !state.daySort && r.line !== prev && hasPlan ? `<tr class="line-head"><td colspan="8">${esc(r.line || 'Линия не указана')}</td></tr>` : '';
      prev = r.line;
      return head + `<tr data-no="${esc(r.order_no)}">
        <td>${r.plan_end_date ? fmt(r.plan_end_date) + ' ' + esc(r.plan_end_time || '') : '—'}</td>
        <td><b>${esc(r.order_no)}</b> / ${esc(r.pos)}</td>
        <td>${esc(r.customer || '—')}</td>
        <td>${esc(r.product || '')}</td>
        <td>${esc(r.manager || '')}</td>
        <td>${fmt(r.ship_plan)}</td>
        <td>${esc(r.stage)}</td>
        <td>${r.task_make ? taskTag('make', r.make_done, r.late && !r.make_done) : ''}${r.task_ship ? taskTag('ship', r.ship_done, r.late && !r.ship_done) : ''}${r.risk ? '<div class="late-tag" title="Плановое окончание производства позже плана отгрузки">производство позже отгрузки</div>' : ''}${r.quality.map(t => `<div class="q-tag">${esc(t)}</div>`).join('')}</td>
      </tr>`;
    }).join('');
    document.querySelectorAll('#dayTable tbody tr[data-no]').forEach(tr => tr.addEventListener('click', () => openOrder(tr.dataset.no)));
  }
  ['#dayDate', '#dayBacklog'].forEach(id => $(id).addEventListener('change', () => loadDay().catch(showDayErr)));
  $('#dayLine').addEventListener('change', () => { state.dayLine = ''; renderDay(); });
  $('#dayWhat').addEventListener('change', renderDay);
  $('#daySearch').addEventListener('input', renderDay);
  state.daySort = '';
  const dayKey = f => f === 'plan_end' ? (r => (r.plan_end_date ? r.plan_end_date + ' ' + (r.plan_end_time || '') : null))
    : f === 'order_no' ? (r => `${r.order_no}/${r.pos}`)
    : f === 'state' ? (r => (r.late ? 0 : (r.task_make && !r.make_done) || (r.task_ship && !r.ship_done) ? 1 : 2))
    : (r => r[f]);
  bindSort('#dayTable', () => state.daySort, v => { state.daySort = v; renderDay(); });
  $('#dayExport').addEventListener('click', () => {
    const rows = state.dayRows || [];
    downloadCsv(`SalesVisor_план_на_${$('#dayDate').value || 'день'}.csv`,
      ['Окончание', 'Время', 'Заказ', 'Поз.', 'Клиент', 'Изделие', 'Менеджер', 'Линия', 'План отгрузки', 'Этап',
        'Произвести', 'Отгрузить', 'Отстаёт', 'Производство позже отгрузки', 'Несоответствия'],
      rows.map(r => [fmt(r.plan_end_date), r.plan_end_time || '', r.order_no, r.pos, r.customer || '', r.product || '',
        r.manager || '', r.line || '', fmt(r.ship_plan), r.stage || '',
        r.task_make ? (r.make_done ? 'произведено' : 'произвести') : '', r.task_ship ? (r.ship_done ? 'отгружено' : 'отгрузить') : '',
        r.late ? 'да' : '', r.risk ? 'да' : '', (r.quality || []).join('; ')]));
  });

  // ---------- диспетчерский ----------
  const MONTHS_UP = ['ЯНВАРЬ', 'ФЕВРАЛЬ', 'МАРТ', 'АПРЕЛЬ', 'МАЙ', 'ИЮНЬ', 'ИЮЛЬ', 'АВГУСТ', 'СЕНТЯБРЬ', 'ОКТЯБРЬ', 'НОЯБРЬ', 'ДЕКАБРЬ'];
  const mln = v => num((v || 0) / 1e6, 1);
  function pctCell(fact, plan) {
    if (!plan) return '<td>—</td>';
    const p = Math.round(100 * fact / plan);
    return `<td class="${p >= 95 ? 'pct-ok' : p >= 70 ? 'pct-mid' : 'pct-low'}">${p}%</td>`;
  }
  function measureCells(x) {
    return `<td>${num(x.km)}</td><td>${num(x.pcs, 0)}</td><td>${num(x.ov_km, 0)}</td><td>${mln(x.mz)}</td><td>${mln(x.vp)}</td>
      <td>${num(x.km_ready)}</td><td>${num(x.pcs_ready, 0)}</td><td>${num(x.ov_km_ready, 0)}</td><td>${mln(x.mz_ready)}</td><td>${mln(x.vp_ready)}</td>
      ${pctCell(x.km_ready, x.km)}${pctCell(x.mz_ready, x.mz)}`;
  }
  function currentDecadeLabel(months) {
    const t = new Date(state.meta.today || Date.now());
    const want = MONTHS_UP[t.getMonth()], n = t.getDate() <= 10 ? 1 : t.getDate() <= 20 ? 2 : 3;
    for (const mo of months) for (const d of mo.decades) if (d.decade.includes(want) && d.decade.replace(/\s/g, '').includes(n + 'декада')) return d.decade;
    return '';
  }
  async function loadDisp(decade) {
    const qs = new URLSearchParams({ manager: $('#manager').value, dept: $('#dept').value, decade: decade ?? state.dispDecade ?? '' });
    let d = await api('api/dispatcher?' + qs);
    if (decade === undefined && state.dispDecade === undefined && d.loaded) {
      state.dispDecade = currentDecadeLabel(d.months);
      if (state.dispDecade) { qs.set('decade', state.dispDecade); d = await api('api/dispatcher?' + qs); }
    }
    if (decade !== undefined) state.dispDecade = decade;
    state.disp = d;
    renderDisp();
  }
  function renderDisp() {
    const d = state.disp;
    $('#dispEmpty').hidden = d.loaded;
    $('#dispNote').textContent = d.loaded ? `План — позиции с ПО «считать», факт — из них с назначенной партией, как в листе «отчет (итог)». `
      + `Не считаются: ${d.not_counted} поз., без декады («без учета»): ${d.no_decade}. Расхождений с отчётом по отрезкам: ${d.mismatch}. Нажмите на декаду, чтобы увидеть позиции.` : '';
    let html = '';
    // По умолчанию — текущий месяц и два соседних; весь год по галочке
    const curIdx = MONTHS_UP.indexOf((state.dispDecade || '').split(/\s+/)[1] || '');
    const visible = mo => $('#dispAll').checked || curIdx < 0 || Math.abs(MONTHS_UP.indexOf(mo.month) - curIdx) <= 1;
    for (const mo of d.months.filter(visible)) {
      for (const x of mo.decades) {
        html += `<tr class="dec ${x.decade === state.dispDecade ? 'active' : ''}" data-dec="${esc(x.decade)}"><td>${esc(x.decade)}</td>${measureCells(x)}
          <td>${x.positions_ready}/${x.positions}</td><td>${x.mismatch ? `<span class="late-tag">${x.mismatch}</span>` : '0'}</td></tr>`;
      }
      html += `<tr class="month"><td>${esc(mo.month)} итого</td>${measureCells(mo)}<td></td><td></td></tr>`;
    }
    if (d.months.length) html += `<tr class="total"><td>Всего за год</td>${measureCells(d.total)}<td></td><td></td></tr>`;
    $('#dispTable tbody').innerHTML = html;
    document.querySelectorAll('#dispTable tr.dec').forEach(tr => tr.addEventListener('click', () => loadDisp(tr.dataset.dec).catch(showErr)));
    const show = !!d.decade;
    ['#dispDetailTitle', '#dispDetailBar', '#dispDetailWrap'].forEach(id => { $(id).hidden = !show; });
    if (!show) return;
    $('#dispDetailTitle').textContent = 'Позиции: ' + d.decade;
    const f = $('#dispFilter').value, q = $('#dispSearch').value.trim().toLowerCase();
    const rows = sortRows(d.positions.filter(r => (!f || (f === 'todo' ? r.segs_ready < r.segs : !!r.mismatch)) &&
      matchText(q, r.order_no, r.customer, r.product, r.manager)), state.dispSort, dispKey);
    state.dispRows = rows;
    markSort('#dispDetail', state.dispSort);
    $('#dispDetail tbody').innerHTML = rows.slice(0, 1000).map(r => `<tr data-no="${esc(r.order_no)}">
      <td><b>${esc(r.order_no)}</b> / ${esc(r.pos)}${r.counted ? '' : ' <span class="sub">не считать</span>'}</td>
      <td>${esc(r.customer || '—')}</td><td>${esc(r.product || '')}</td><td>${esc(r.manager || '')}</td>
      <td class="num">${num(r.km, 3)}</td><td class="num">${num((r.mz || 0) / 1e3, 0)}</td>
      <td>${r.segs_ready === r.segs ? '<span class="task done">да</span>' : r.segs_ready ? `<span class="task todo">${r.segs_ready} из ${r.segs}</span>` : '<span class="task late">нет</span>'}</td>
      <td>${esc(r.stage || '—')}</td>
      <td>${r.mismatch ? `<span class="late-tag">${esc(r.mismatch)}</span>` : '<span class="muted">сходится</span>'}</td>
    </tr>`).join('') || '<tr><td colspan="9" class="muted">Нет позиций под фильтр.</td></tr>';
    document.querySelectorAll('#dispDetail tbody tr[data-no]').forEach(tr => tr.addEventListener('click', () => openOrder(tr.dataset.no).catch(showErr)));
  }
  $('#dispFilter').addEventListener('change', renderDisp);
  $('#dispSearch').addEventListener('input', renderDisp);
  state.dispSort = '';
  const dispKey = f => f === 'order_no' ? (r => `${r.order_no}/${r.pos}`)
    : f === 'ready_share' ? (r => (r.segs ? r.segs_ready / r.segs : null))
    : (r => r[f]);
  bindSort('#dispDetail', () => state.dispSort, v => { state.dispSort = v; renderDisp(); });
  $('#dispExport').addEventListener('click', () => {
    const rows = state.dispRows || [];
    downloadCsv(`SalesVisor_диспетчерский_${(state.dispDecade || 'декада').replace(/[^\wА-Яа-яЁё]+/g, '_')}.csv`,
      ['Заказ', 'Поз.', 'Клиент', 'Изделие', 'Менеджер', 'Декада', 'ПО', 'ГП, км', 'ГП, шт', 'МЗ, руб', 'ВП, руб',
        'Отрезков с партией', 'Отрезков всего', 'Этап по отрезкам', 'Сверка'],
      rows.map(r => [r.order_no, r.pos, r.customer || '', r.product || '', r.manager || '', r.decade || '',
        r.counted ? 'считать' : 'не считать', r.km ?? '', r.pcs ?? '', r.mz ?? '', r.vp ?? '', r.segs_ready, r.segs,
        r.stage || '', r.mismatch || 'сходится']));
  });
  $('#dispAll').addEventListener('change', renderDisp);

  // ---------- изменения ----------
  async function loadChanges() {
    const qs = new URLSearchParams({ days: $('#days').value, manager: $('#manager').value });
    state.changes = await api('api/changes?' + qs);
    renderChanges();
  }
  function renderChanges() {
    const f = $('#field').value, q = $('#changesSearch').value.trim().toLowerCase();
    const rows = sortRows(state.changes.filter(c => (!f || c.field === f) &&
      matchText(q, c.order_no, c.customer, c.manager, c.product, fmtVal(c.old), fmtVal(c.new))), state.changesSort, k => (c => c[k]));
    state.changesRows = rows;
    markSort('#changes', state.changesSort);
    $('#changesEmpty').hidden = rows.length > 0;
    $('#changes tbody').innerHTML = rows.map(c => `<tr data-no="${esc(c.order_no)}">
      <td>${fmtDT(c.at)}</td><td><b>${esc(c.order_no)}</b></td><td>${esc(c.pos)}</td><td>${esc(c.customer || '')}</td>
      <td>${esc(c.manager || '')}</td><td>${esc(c.field_name)}</td><td>${esc(fmtVal(c.old))}</td><td>${esc(fmtVal(c.new))}</td></tr>`).join('');
    document.querySelectorAll('#changes tbody tr').forEach(tr => tr.addEventListener('click', () => openOrder(tr.dataset.no)));
  }
  $('#days').addEventListener('change', loadChanges);
  $('#field').addEventListener('change', renderChanges);
  $('#changesSearch').addEventListener('input', renderChanges);
  state.changesSort = '';
  bindSort('#changes', () => state.changesSort, v => { state.changesSort = v; renderChanges(); });
  $('#changesExport').addEventListener('click', () => {
    downloadCsv(`SalesVisor_изменения_${stamp()}.csv`, ['Когда', 'Заказ', 'Поз.', 'Клиент', 'Менеджер', 'Изделие', 'Что', 'Было', 'Стало'],
      (state.changesRows || []).map(c => [fmtDT(c.at), c.order_no, c.pos, c.customer || '', c.manager || '', c.product || '',
        c.field_name, fmtVal(c.old), fmtVal(c.new)]));
  });

  // ---------- загрузка ----------
  const SOURCE_NAME = { segments: 'Отчёт по отрезкам', svetofor: 'Светофор', plan: 'План производства (ZPP context)', dispatcher: 'Диспетчерский отчёт' };
  function loadSummary(r) {
    if (r.source === 'bitrix') return `Битрикс24: комментариев о переносах ${r.comments_for_changes}.`;
    const parts = [`строк ${r.rows}`, `позиций ${r.positions}`];
    if (r.new) parts.push(`новых ${r.new}`);
    parts.push(`изменений ${r.changes ?? 0}`);
    if (r.not_in_orders) parts.push(`ждут своих позиций (применятся после загрузки отрезков или светофора): ${r.not_in_orders}`);
    if (r.deferred_applied) parts.push(`применено ждавших строк плана и диспетчерского: ${r.deferred_applied}`);
    if (r.quality_new) parts.push(`новых несоответствий: ${r.quality_new}`);
    return `${SOURCE_NAME[r.source] || r.source}: ${parts.join(', ')}.`;
  }

  $('#file').addEventListener('change', async e => {
    const file = e.target.files[0];
    if (!file) return;
    const out = $('#loadResult');
    out.className = 'result';
    out.textContent = 'Загружаем, большие файлы обрабатываются до минуты…';
    const fd = new FormData();
    fd.append('file', file);
    try {
      const r = await api('api/upload', { method: 'POST', body: fd });
      out.textContent = loadSummary(r);
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
      const r = await api('api/sync', { method: 'POST' });
      out.textContent = r.length ? r.map(loadSummary).join(' ') : 'Новых выгрузок нет.';
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

  loadMeta().then(() => { readUrl(); setView(state.view); return loadOrders(); }).catch(showErr);
})();
