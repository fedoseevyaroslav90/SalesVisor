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

  // ---------- выбор нескольких значений ----------
  // Скрытое поле с прежним id хранит выбор через «|» (остальной код читает и пишет .value как раньше),
  // рядом — кнопка со сводкой («Все», одно значение, «N выбрано») и список с поиском и галками.
  const MS = {};
  function multiSelect(input) {
    const allLabel = input.dataset.multi || 'Все';
    const box = document.createElement('span');
    box.className = 'ms';
    box.innerHTML = `<button type="button" class="ms-btn" aria-haspopup="listbox"></button>
      <div class="ms-pop" hidden><input type="search" class="ms-search" placeholder="Найти…" aria-label="Найти в списке">
      <div class="ms-list" role="listbox" aria-multiselectable="true"></div>
      <div class="ms-acts"><button type="button" class="linkish-btn ms-clear">${esc(allLabel)}</button><span class="ms-n"></span></div></div>`;
    input.after(box);
    const btn = box.querySelector('.ms-btn'), pop = box.querySelector('.ms-pop'), list = box.querySelector('.ms-list'),
      search = box.querySelector('.ms-search');
    let options = [];
    const selected = () => (input.value ? input.value.split('|') : []);
    const nameOf = v => options.find(o => o.value === v)?.label || v;
    function label() {
      const s = selected();
      btn.textContent = !s.length ? allLabel : s.length === 1 ? nameOf(s[0]) : `${nameOf(s[0])} и ещё ${s.length - 1}`;
      btn.title = s.length ? s.map(nameOf).join('\n') : allLabel;
      btn.classList.toggle('active-filter', s.length > 0);
      box.querySelector('.ms-n').textContent = s.length ? `выбрано: ${s.length}` : '';
    }
    function draw() {
      const q = search.value.trim().toLowerCase(), s = new Set(selected());
      const shown = options.filter(o => !q || o.label.toLowerCase().includes(q));
      list.innerHTML = shown.map(o => `<label class="check"><input type="checkbox" value="${esc(o.value)}"${s.has(o.value) ? ' checked' : ''}> ${esc(o.label)}</label>`).join('')
        || '<div class="muted">Ничего не найдено</div>';
    }
    function set(values) {
      input.value = values.join('|');
      label();
      input.dispatchEvent(new Event('change'));
    }
    list.addEventListener('change', e => {
      const s = new Set(selected());
      if (e.target.checked) s.add(e.target.value); else s.delete(e.target.value);
      // порядок — как в списке; значения, которых в списке нет (из старой ссылки), сохраняются в конце
      set([...options.map(o => o.value).filter(v => s.has(v)), ...[...s].filter(v => !options.some(o => o.value === v))]);
    });
    box.querySelector('.ms-clear').addEventListener('click', () => { set([]); draw(); });
    search.addEventListener('input', draw);
    const close = () => { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', () => {
      const open = pop.hidden;
      document.querySelectorAll('.ms-pop').forEach(p => { p.hidden = true; });
      pop.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
      if (open) { search.value = ''; draw(); search.focus(); }
    });
    document.addEventListener('click', e => { if (!box.contains(e.target)) close(); });
    box.addEventListener('keydown', e => { if (e.key === 'Escape') { e.stopPropagation(); close(); btn.focus(); } });
    MS[input.id] = { setOptions(opts) { options = opts; label(); if (!pop.hidden) draw(); }, refresh() { label(); if (!pop.hidden) draw(); } };
    label();
  }
  document.querySelectorAll('input[data-multi]').forEach(multiSelect);
  const refreshMulti = () => Object.values(MS).forEach(m => m.refresh());
  const STAGE_OPTIONS = [['not_made', 'Не произведено'], ['in_prod', 'В производстве'], ['made', 'Произведено'], ['stock', 'На складе'],
    ['ready', 'Готово к отгрузке'], ['transit', 'В пути'], ['shipped', 'Отгружено'], ['none', 'Нет в отчёте по отрезкам']]
    .map(([value, label]) => ({ value, label }));
  MS.fStage.setOptions(STAGE_OPTIONS);

  // ---------- вкладки ----------
  document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
    if (b.dataset.tab === 'changes') loadChanges();
    if (b.dataset.tab === 'stats') loadStats().catch(err => { $('#statsEmpty').hidden = false; $('#statsEmpty').textContent = 'Не удалось посчитать: ' + err.message; });
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
    const opts = items => items.map(v => ({ value: v, label: v }));
    MS.manager.setOptions(opts(state.meta.managers));
    MS.dept.setOptions(opts(state.meta.depts));
    fill($('#dayLine'), state.meta.lines || [], '');
    MS.fLine.setOptions([{ value: '-', label: 'Не в плане производства' }, ...opts(state.meta.lines || [])]);
    MS.fReject.setOptions([...(state.meta.rejects || []).map(r => ({ value: r.code, label: `${r.code} — ${r.text || ''}` })),
      { value: '-', label: '(пусто) — принят в производство' }]);
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
  Object.assign(state, { view: 'orders', sort: '', posSort: '', shown: PAGE, positions: [], posTotal: 0, seq: 0,
    expanded: new Set(), posCache: {} });
  const kRub = v => (v == null ? '—' : num(v / 1000, 0));  // тыс. ₽
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
    const text = sel => { const el = $(sel); return el.tagName === 'SELECT' ? el.options[el.selectedIndex]?.text : el.value.split('|').join(', '); };
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
    savePrefs({ last: s });
  }
  function readUrl() {
    // ссылка (старые — с #…) → последний отбор сотрудника → его заказы по логину SAP → последний отбор в этом браузере
    const p = state.prefs || {};
    const h = new URLSearchParams(location.search.slice(1) || location.hash.slice(1) || p.last
      || (p.sap_login ? 'manager=' + encodeURIComponent(p.sap_login) : '') || store.get('lastView'));
    if (![...h.keys()].length) return;
    for (const [k, sel] of Object.entries(URL_FIELDS)) if (h.has(k)) $(sel).value = ['stage', 'reject'].includes(k) ? h.get(k).replace(/,/g, '|') : h.get(k);
    const flags = (h.get('flags') || '').split(',');
    flagInputs().forEach(i => { i.checked = flags.includes(i.dataset.flag); });
    const tile = h.get('tile') || '';
    state.overdue = tile === 'late';
    state.color = tile === 'late' ? '' : tile;
    state.view = h.get('view') === 'positions' ? 'positions' : 'orders';
    refreshMulti();
    if (state.view === 'orders') state.sort = h.get('sort') || ''; else state.posSort = h.get('sort') || '';
  }
  // Подсветка заданных фильтров, число их на кнопке «Отбор», видимость полей своего периода
  function markFilters() {
    let n = 0;
    for (const el of document.querySelectorAll('#filters select, #filters input:not([type=checkbox])')) {
      if (el.closest('.ms')) continue;
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
    state.expanded.clear();
    state.posCache = {};
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
        <td><button class="exp" data-no="${esc(o.order_no)}" aria-expanded="${state.expanded.has(o.order_no)}" title="Позиции заказа">${state.expanded.has(o.order_no) ? '▾' : '▸'}</button><b>${esc(o.order_no)}</b>${o.comments ? ` <span class="sub" title="Комментарии">💬${o.comments}</span>` : ''}${o.overdue ? `<div class="late-tag">просрочено поз.: ${o.overdue}</div>` : ''}${o.quality ? `<div class="q-tag" title="Сообщения о качестве">несоответствий: ${o.quality}</div>` : ''}</td>
        <td>${esc(o.customer || '—')}</td>
        <td>${esc(o.sales_dept || '—')}<div class="sub">${esc(o.manager || '')}</div></td>
        <td>${o.positions} ${o.red ? `<span class="cnt red">●${o.red}</span>` : ''}${o.yellow ? `<span class="cnt yellow">●${o.yellow}</span>` : ''}
          <div class="sub"><span class="n-ok" title="Позиций, у которых все отрезки готовы или отгружены">готово ${o.ready_positions}</span>${o.positions - o.ready_positions ? ` · <span class="n-bad">не готово ${o.positions - o.ready_positions}</span>` : ''}</div></td>
        <td>${esc(o.first_decade || '—')}</td>
        <td>${esc(o.current_decade || '—')}</td>
        <td class="num ${o.max_shift > 30 ? 'neg' : ''}">${o.max_shift == null ? '—' : o.max_shift + ' дн.'}</td>
        <td>${ready === null ? '<span class="muted">нет</span>' : `<span class="bar"><i style="width:${ready}%"></i></span><span class="pct">${o.segments_ready}/${o.segments_total}</span>`}</td>
        <td class="num">${kRub(o.mp_rub)}${o.days_late ? `<div class="sub ${o.days_late > 30 ? 'neg' : ''}">опозд. ${o.days_late} дн.</div>` : ''}</td>
        <td>${task}</td>
      </tr>` + (state.expanded.has(o.order_no) ? subRows(o.order_no) : '');
    }).join('');
    document.querySelectorAll('#orders tbody tr[data-no]').forEach(tr => tr.addEventListener('click', () => openOrder(tr.dataset.no)));
    document.querySelectorAll('#orders .exp').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); toggleOrder(b.dataset.no); }));
    $('#expandAll').hidden = !rows.length;
    $('#expandAll').textContent = rows.slice(0, state.shown).every(o => state.expanded.has(o.order_no)) && rows.length ? 'Свернуть все' : 'Развернуть все';
    const rest = rows.length - state.shown;
    $('#ordersMore').hidden = rest <= 0;
    $('#ordersMore button').textContent = `Показать ещё ${Math.min(rest, PAGE)} (всего ${rows.length})`;
    markSort('#orders', state.sort);
  }

  // ---------- позиции под строкой заказа ----------
  function subRows(no) {
    const ps = state.posCache[no];
    const cell = html => `<tr class="sub-row" data-for="${esc(no)}"><td></td><td colspan="10">${html}</td></tr>`;
    if (!ps) return cell('<span class="muted">Загружаю позиции…</span>');
    return cell(`<table class="mini sub"><thead><tr><th>Поз.</th><th></th><th>Изделие</th><th>Первая декада</th><th>Срок сейчас</th>
      <th>Смещ., дн.</th><th>Треб. дата</th><th>План отгрузки</th><th>Линия, окончание</th><th>Этап</th><th>Отрезки</th><th>МП, т. ₽</th></tr></thead><tbody>`
      + ps.map(p => `<tr>
        <td><b>${esc(p.pos)}</b></td>
        <td><span class="dot ${COLOR_NAME[p.color] ? p.color : ''}"></span></td>
        <td>${esc(p.product || '')}${p.quality ? ` <span class="q-tag">несоотв.: ${p.quality}</span>` : ''}</td>
        <td>${esc(p.first_decade || '—')}</td>
        <td>${esc(p.current_decade || fmt(p.due_date))}${p.overdue ? ' <span class="late-tag">просрочено</span>' : ''}</td>
        <td class="num">${p.shift_days ?? '—'}</td>
        <td>${fmt(p.required_date)}</td>
        <td>${fmt(p.plan_ship_date || p.invoice_plan_date)}</td>
        <td>${p.line ? `<b>${esc(p.line)}</b> ${fmt(p.plan_end_date)} ${esc(p.plan_end_time || '')}` : '<span class="muted">—</span>'}</td>
        <td>${esc(p.stage)}${p.reject_code ? ` <span class="sub">${esc(p.reject_code)}</span>` : ''}</td>
        <td class="${p.segments_total && p.segments_ready >= p.segments_total ? 'n-ok' : ''}">${p.segments_total ? `${p.segments_ready}/${p.segments_total}` : '—'}</td>
        <td class="num">${kRub(p.mp_rub)}${p.days_late ? `<div class="sub">опозд. ${p.days_late} дн.</div>` : ''}</td>
      </tr>`).join('') + '</tbody></table>');
  }
  // позиции подходят под тот же отбор, что и строка заказа; за раз — до 150 заказов одним запросом
  async function fetchPositions(nos) {
    const need = nos.filter(no => !state.posCache[no]);
    for (let i = 0; i < need.length; i += 150) {
      const chunk = need.slice(i, i + 150);
      const d = await api('api/positions?' + new URLSearchParams({ ...baseParams(), ...filterParams(), order: chunk.join('|'),
        sort: 'pos', limit: 1000 }));
      for (const no of chunk) state.posCache[no] = [];
      for (const p of d.rows) (state.posCache[p.order_no] ||= []).push(p);
    }
  }
  async function toggleOrder(no) {
    if (state.expanded.has(no)) state.expanded.delete(no); else state.expanded.add(no);
    render();
    if (state.expanded.has(no) && !state.posCache[no]) { await fetchPositions([no]).catch(showErr); render(); }
  }
  $('#expandAll').addEventListener('click', async () => {
    const shown = sortRows(tileRows(state.orders), state.sort, orderKey).slice(0, state.shown).map(o => o.order_no);
    const all = shown.every(no => state.expanded.has(no));
    if (all) { shown.forEach(no => state.expanded.delete(no)); render(); return; }
    shown.forEach(no => state.expanded.add(no));
    render();
    await fetchPositions(shown).catch(showErr);
    render();
  });

  // ---------- столбцы: какие показывать (у каждого сотрудника свои, в преднастройках) ----------
  const colStyle = document.head.appendChild(document.createElement('style'));
  const COL_TABLES = [['orders', '#orders'], ['positions', '#positions']];
  const colName = th => th.textContent.trim() || (th.dataset.sort === 'color' ? 'Цвет светофора' : th.dataset.sort);
  function applyCols() {
    const hidden = state.prefs?.hidden_cols || {};
    let css = '';
    for (const [view, sel] of COL_TABLES) {
      const ths = [...document.querySelectorAll(`${sel} > thead > tr > th`)];
      for (const key of hidden[view] || []) {
        const i = ths.findIndex(th => th.dataset.sort === key) + 1;
        // строки с позициями под заказом (sub-row) не трогаем — у них свои столбцы
        if (i > 0) css += `${sel} > thead > tr > th:nth-child(${i}), ${sel} > tbody > tr:not(.sub-row) > td:nth-child(${i}) { display: none; }
`;
      }
    }
    colStyle.textContent = css;
    $('#colsBtn').textContent = (hidden[state.view] || []).length ? `Столбцы (скрыто ${(hidden[state.view] || []).length})` : 'Столбцы';
  }
  function drawCols() {
    const sel = state.view === 'orders' ? '#orders' : '#positions';
    const hidden = new Set((state.prefs?.hidden_cols || {})[state.view] || []);
    $('#colsList').innerHTML = [...document.querySelectorAll(`${sel} > thead > tr > th[data-sort]`)]
      .filter(th => th.dataset.sort !== 'order_no')  // номер заказа виден всегда
      .map(th => `<label class="check"><input type="checkbox" value="${esc(th.dataset.sort)}"${hidden.has(th.dataset.sort) ? '' : ' checked'}> ${esc(colName(th))}</label>`).join('');
  }
  function setHidden(list) {
    savePrefs({ hidden_cols: { ...(state.prefs?.hidden_cols || {}), [state.view]: list } });
    applyCols();
  }
  $('#colsBtn').addEventListener('click', () => { const open = $('#colsPop').hidden; $('#colsPop').hidden = !open; if (open) drawCols(); });
  $('#colsList').addEventListener('change', () => setHidden([...document.querySelectorAll('#colsList input')].filter(i => !i.checked).map(i => i.value)));
  $('#colsAll').addEventListener('click', () => { setHidden([]); drawCols(); });
  document.addEventListener('click', e => { if (!e.target.closest('.cols')) $('#colsPop').hidden = true; });

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
        <td>${esc(p.first_decade || '—')} → ${esc(p.current_decade || fmt(p.due_date))}${p.overdue ? '<div class="late-tag">просрочено</div>' : ''}</td>
        <td class="num ${p.shift_days > 30 ? 'neg' : ''}">${p.shift_days ?? '—'}</td>
        <td>${fmt(p.plan_ship_date || p.invoice_plan_date)}</td>
        <td>${p.line ? `<b>${esc(p.line)}</b><div class="sub">${fmt(p.plan_end_date)} ${esc(p.plan_end_time || '')}</div>` : '<span class="muted">—</span>'}</td>
        <td>${esc(p.stage)}${p.reject_code ? ` <span class="sub" title="${esc(p.reject_text || 'причина отклонения')}">${esc(p.reject_code)}</span>` : ''}
          <div class="sub">${p.segments_total ? `отрезков ${p.segments_ready}/${p.segments_total}` : ''}${p.disp_decade ? ` · ${esc(p.disp_decade)}` : ''}</div></td>
        <td class="num">${kRub(p.mp_rub)}${p.days_late ? `<div class="sub ${p.days_late > 30 ? 'neg' : ''}">опозд. ${p.days_late} дн.</div>` : ''}</td>
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
    $('#expandAll').hidden = v !== 'orders';
    applyCols();
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
  $('#manager').addEventListener('change', e => { reload(); });
  $('#dept').addEventListener('change', e => { reload(); });
  $('#search').addEventListener('input', reload);
  $('#scope').addEventListener('change', reload);
  [...document.querySelectorAll('#filters select, #filters input')].filter(el => !el.closest('.ms')).forEach(el => el.addEventListener(el.type === 'text' || el.type === 'number' || el.type === 'search' || el.id === 'fCustomer' ? 'input' : 'change', reload));
  $('#fReset').addEventListener('click', () => {
    document.querySelectorAll('#filters select, #filters input:not([type=checkbox])').forEach(el => { el.value = ''; });
    flagInputs().forEach(i => { i.checked = false; });
    state.color = ''; state.overdue = false;
    refreshMulti();
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
  // ---------- преднастройки сотрудника: у каждого сотрудника портала свои (хранятся в SalesVisor) ----------
  // «Мои отборы» (пресеты пилота: «мои просрочки», «мой отдел»), последний отбор, свой логин SAP для «Мои заказы».
  let prefsTimer = null, prefsPending = {};
  function savePrefs(part) {
    if (!state.prefs) return;  // преднастройки не загрузились — не затираем их на сервере
    Object.assign(state.prefs, part);
    Object.assign(prefsPending, part);
    clearTimeout(prefsTimer);
    prefsTimer = setTimeout(() => {
      const body = prefsPending;
      prefsPending = {};
      api('api/prefs', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
        .catch(err => console.warn('преднастройки не сохранены:', err.message));
    }, 800);
  }
  async function loadPrefs() {
    try { state.prefs = await api('api/prefs'); } catch { state.prefs = null; return; }
    // отборы, сохранённые раньше в этом браузере, переезжают к сотруднику один раз
    let local = [];
    try { local = JSON.parse(store.get('views') || '[]'); } catch { /* нет */ }
    if (local.length && !state.prefs.views.length) {
      savePrefs({ views: local.map(x => ({ name: String(x.name).slice(0, 60), query: x.query ?? x.hash ?? '' })).slice(-30) });
      store.set('views', '');
    }
    fillSavedViews();
    fillMe();
    applyCols();
  }
  const savedViews = () => (state.prefs ? state.prefs.views || [] : []);
  function fillSavedViews() {
    const list = savedViews();
    $('#savedViews').innerHTML = '<option value="">Мои отборы…</option>'
      + list.map((x, i) => `<option value="${i}">${esc(x.name)}</option>`).join('')
      + (list.length ? '<option value="del">Удалить отбор…</option>' : '');
    $('#savedViews').disabled = $('#saveView').disabled = !state.prefs;
  }
  $('#saveView').addEventListener('click', () => {
    const name = (window.prompt('Название отбора, например «Мои просрочки»:') || '').trim().slice(0, 60);
    if (!name) return;
    const list = savedViews().filter(x => x.name !== name);
    list.push({ name, query: location.search.slice(1) });
    savePrefs({ views: list.slice(-30) });
    fillSavedViews();
  });
  // отбор целиком: сначала всё сбросить, затем применить строку адреса
  function applyQuery(q) {
    document.querySelectorAll('#filters select, #filters input:not([type=checkbox])').forEach(el => { el.value = ''; });
    ['#search', '#manager', '#dept'].forEach(id => { $(id).value = ''; });
    flagInputs().forEach(i => { i.checked = false; });
    $('#scope').value = 'open';
    state.sort = ''; state.posSort = ''; state.color = ''; state.overdue = false;
    history.replaceState(null, '', location.pathname + (q ? '?' + q : ''));
    readUrl();
    setView(state.view);
    loadOrders().catch(showErr);
  }
  $('#savedViews').addEventListener('change', e => {
    const v = e.target.value;
    e.target.value = '';
    if (v === 'del') {
      const name = (window.prompt('Какой отбор удалить? Название:\n' + savedViews().map(x => x.name).join('\n')) || '').trim();
      savePrefs({ views: savedViews().filter(x => x.name !== name) });
      fillSavedViews();
      return;
    }
    const x = savedViews()[+v];
    if (x) applyQuery(x.query || '');
  });

  // «Мои заказы»: менеджер = свой логин SAP (поле «Создал»). Логин сотрудник выбирает один раз,
  // подсказка — по фамилии из портала (Иванова → IVANOVA)
  const TRANSLIT = { а: 'A', б: 'B', в: 'V', г: 'G', д: 'D', е: 'E', ё: 'E', ж: 'ZH', з: 'Z', и: 'I', й: 'I', к: 'K', л: 'L', м: 'M',
    н: 'N', о: 'O', п: 'P', р: 'R', с: 'S', т: 'T', у: 'U', ф: 'F', х: 'KH', ц: 'TS', ч: 'CH', ш: 'SH', щ: 'SHCH', ъ: '', ы: 'Y',
    ь: '', э: 'E', ю: 'IU', я: 'IA' };
  function guessLogin() {
    const surname = (state.meta.portal_user || '').trim().split(/\s+/)[0].toLowerCase();
    const lat = [...surname].map(c => TRANSLIT[c] ?? c.toUpperCase()).join('');
    const managers = state.meta.managers || [];
    return managers.find(m => m === lat) || managers.find(m => lat && m.startsWith(lat.slice(0, 5))) || '';
  }
  function fillMe() {
    const login = state.prefs?.sap_login || '';
    $('#myOrders').title = login ? `Заказы, где в SAP «Создал» = ${login}` : 'Выберите свой логин в SAP — кнопка будет открывать ваши заказы';
    $('#meLogin').innerHTML = '<option value="">— не менеджер —</option>'
      + (state.meta.managers || []).map(m => `<option>${esc(m)}</option>`).join('');
    $('#meLogin').value = login || guessLogin();
    $('#myOrders').disabled = $('#meSet').disabled = !state.prefs;
  }
  function openMe() {
    $('#mePop').hidden = false;
    $('#meLogin').focus();
  }
  $('#myOrders').addEventListener('click', () => {
    const login = state.prefs?.sap_login;
    if (!login) { openMe(); return; }
    $('#manager').value = login;
    refreshMulti();
    loadOrders().catch(showErr);
  });
  $('#meSet').addEventListener('click', () => { $('#mePop').hidden ? openMe() : ($('#mePop').hidden = true); });
  $('#meSave').addEventListener('click', () => {
    savePrefs({ sap_login: $('#meLogin').value });
    $('#mePop').hidden = true;
    fillMe();
    if ($('#meLogin').value) $('#myOrders').click();
  });
  document.addEventListener('click', e => { if (!e.target.closest('.me')) $('#mePop').hidden = true; });

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

      ${(() => {  // СТП 02-2-01: больше 30 дней опоздания — покупатель вправе отказаться от поставки
        const late = Math.max(0, ...d.positions.map(p => p.days_late || 0));
        return late > 30 ? `<div class="warn-box">Опоздание к первой дате клиента — до ${late} дн. Больше 30 дней: по СТП 02-2-01 покупатель вправе отказаться от поставки.</div>` : '';
      })()}
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
    $('#dayNote').textContent = (hasPlan ? 'Произвести — по ZPP context (линия, плановое окончание); сделано, если все отрезки произведены или факт MES дошёл до плана. Производственные сутки — с 08:00 до 08:00: окончание до 08:00 относится к предыдущим суткам. Отгрузить — по плану отгрузки. '
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
        <td>${r.plan_end_date ? fmt(r.plan_end_date) + ' ' + esc(r.plan_end_time || '') : '—'}${r.prod_day && r.prod_day !== r.plan_end_date ? `<div class="sub" title="Окончание до 08:00 — предыдущие производственные сутки">сутки ${fmt(r.prod_day)}</div>` : ''}</td>
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

  // ---------- статистика и отчёт по срокам ----------
  const MONTH_RU = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь'];
  const monthName = ym => { const [y, m] = ym.split('-'); return `${MONTH_RU[+m - 1]} ${y}`; };
  const pctTxt = v => (v == null ? '—' : num(v, 1) + '%');
  const BUCKETS = ['в срок', '+1 декада', '+2 декады', '+3 декады', '+4 и больше'];
  async function loadStats() {
    const p = { ...baseParams(), ...filterParams() };
    if ($('#statsShipped').checked) p.scope = 'all';
    $('#statsNote').textContent = 'Считаю…';
    state.stats = await api('api/stats?' + new URLSearchParams(p));
    renderStats();
  }
  function table(sel, head, rows) {
    $(sel).innerHTML = `<thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>`
      + (rows.length ? rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${head.length}" class="muted">Нет данных</td></tr>`)
      + '</tbody>';
  }
  function renderStats() {
    const d = state.stats, s = d.summary;
    $('#statsNote').textContent = describeFilters().replace(/^Отбор: (Заказы|Позиции); [^;]*/, 'Отбор: ' + ($('#statsShipped').checked ? 'вместе с отгруженными' : $('#scope').options[$('#scope').selectedIndex].text))
      + ` · на ${fmt(d.today)}. Отбор задаётся на вкладке «Заказы».`;
    $('#statsEmpty').hidden = s.positions > 0;
    $('#statsEmpty').textContent = 'Под отбор ничего не попало.';
    const tiles = [
      ['', 'Заказов', num(s.orders, 0)], ['', 'Позиций', num(s.positions, 0)],
      [s.otd_positions.pct >= 90 ? 'ok' : 'late', `OTD позиций (${s.otd_positions.ok} из ${s.otd_positions.of})`, pctTxt(s.otd_positions.pct)],
      [s.otd_orders.pct >= 90 ? 'ok' : 'late', `OTD заказов (${s.otd_orders.ok} из ${s.otd_orders.of})`, pctTxt(s.otd_orders.pct)],
      ['late', 'Просрочено позиций', num(s.overdue, 0)],
      ['', `ГП, км (отгружено ${num(s.km_shipped, 0)})`, num(s.km_plan, 0)],
      ['', 'МП, млн ₽', num(s.mp_rub / 1e6, 1)], ['', 'Ср. смещение, дн.', num(s.avg_shift, 1)],
    ];
    $('#statsTiles').innerHTML = tiles.map(([c, l, v]) => `<div class="tile ${c}" style="cursor:default"><div class="n">${v}</div><div class="l">${esc(l)}</div></div>`).join('');
    const COLORS = ['#2b8a3e', '#e0a800', '#f08c00', '#d9480f', '#c62828'];
    $('#statsMonths tbody').innerHTML = d.by_month.map(m => {
      const total = m.positions || 1;
      const seg = [...BUCKETS.map((b, i) => [m[b], COLORS[i], b]), [m['не выполнено'], '#7a0000', 'не выполнено'], [m['в работе'], '#c9ced6', 'в работе']]
        .filter(x => x[0]).map(([v, c, b]) => `<i style="width:${(100 * v / total).toFixed(1)}%;background:${c}" title="${esc(b)}: ${v}"></i>`).join('');
      return `<tr><td>${monthName(m.month)}</td><td>${m.orders}</td><td>${m.positions}</td><td>${num(m.km, 0)}</td>
        ${BUCKETS.map(b => `<td>${m[b] || ''}</td>`).join('')}<td class="${m['не выполнено'] ? 'pct-low' : ''}">${m['не выполнено'] || ''}</td><td>${m['в работе'] || ''}</td>
        <td class="${m.otd_pct == null ? '' : m.otd_pct >= 90 ? 'pct-ok' : m.otd_pct >= 70 ? 'pct-mid' : 'pct-low'}">${pctTxt(m.otd_pct)}</td>
        <td>${m.avg_shift ?? '—'}</td><td>${num(m.mp_rub / 1e6, 1)}</td><td><span class="dist">${seg}</span></td></tr>`;
    }).join('') || '<tr><td colspan="15" class="muted">Нет позиций с первой датой</td></tr>';
    table('#statsDepth', ['Просрочено на', 'Позиций'], d.depth.map(x => [esc(x.bucket), x.positions || '']));
    table('#statsCustomers', ['Заказчик', 'Не в срок', 'Из позиций', '%', 'МП, млн ₽'],
      d.top_customers.map(c => [esc(c.customer), c.bad, c.positions, pctTxt(c.pct_bad), num(c.mp_rub / 1e6, 1)]));
    table('#statsDepts', ['Направление', 'Позиций', 'Просрочено', 'OTD, %'], d.by_dept.map(x => [esc(x.dept), x.positions, x.overdue || '', pctTxt(x.otd_pct)]));
    table('#statsRejects', ['Причина', 'Позиций'], d.rejects.map(x => [esc(x.code), x.positions]));
    table('#statsLines', ['Линия', 'Позиций', 'Просрочено', 'ГП, км'], d.lines.map(x => [esc(x.line), x.positions, x.overdue || '', num(x.km, 0)]));
  }
  $('#statsShipped').addEventListener('change', () => loadStats().catch(showErr));
  $('#statsExport').addEventListener('click', () => {
    const d = state.stats;
    if (!d) return;
    const rows = [['Отчёт по срокам', $('#statsNote').textContent], [],
      ['Месяц', 'Заказов', 'Позиций', 'ГП, км', ...BUCKETS, 'Не выполнено', 'В работе', 'OTD, %', 'Ср. смещение, дн.', 'МП, руб'],
      ...d.by_month.map(m => [monthName(m.month), m.orders, m.positions, m.km, ...BUCKETS.map(b => m[b]), m['не выполнено'], m['в работе'],
        m.otd_pct ?? '', m.avg_shift ?? '', m.mp_rub]),
      [], ['Глубина просрочки', 'Позиций'], ...d.depth.map(x => [x.bucket, x.positions]),
      [], ['Заказчик', 'Не в срок', 'Из позиций', '%', 'МП, руб'], ...d.top_customers.map(c => [c.customer, c.bad, c.positions, c.pct_bad, c.mp_rub]),
      [], ['Направление', 'Позиций', 'Просрочено', 'OTD, %'], ...d.by_dept.map(x => [x.dept, x.positions, x.overdue, x.otd_pct ?? '']),
      [], ['Причина отклонения', 'Позиций'], ...d.rejects.map(x => [x.code, x.positions]),
      [], ['Линия', 'Позиций', 'Просрочено', 'ГП, км'], ...d.lines.map(x => [x.line, x.positions, x.overdue, x.km])];
    downloadCsv(`SalesVisor_статистика_${stamp()}.csv`, rows[0], rows.slice(1));
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

  loadMeta().then(loadPrefs).then(() => { readUrl(); setView(state.view); return loadOrders(); }).catch(showErr);
})();
