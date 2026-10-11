/**
 * Advanced query modal.
 */
import { $, createEl, escapeHtml, toast } from './utils.js?v=2026072706';
import * as Api from './api.js?v=2026073103';

const FIELD_SPECS = [
  { id: 'q-s', label: '名称', type: 'text' },
  { id: 'q-ml', label: '最小 LEO (t)', type: 'number', step: 0.1 },
  { id: 'q-mc', label: '最小 C3=0 (t)', type: 'number', step: 0.01 },
  { id: 'q-ct', label: '目标 C3', type: 'number', step: 0.1 },
  { id: 'q-ma', label: '该 C3 最小载荷', type: 'number', step: 0.01 },
  { id: 'q-eng', label: '引擎名称', type: 'text' },
  { id: 'q-sn', label: '最少级数', type: 'number', min: 1 },
  { id: 'q-sx', label: '最多级数', type: 'number', min: 1 },
  { id: 'q-sb', label: '排序', type: 'select', options: [['m_pl_max', '最大 LEO'], ['c3_0_payload', 'C3=0'], ['c3_max', '最大 C3'], ['name', '名称']] },
  { id: 'q-sd', label: '方向', type: 'select', options: [['desc', '降序'], ['asc', '升序']] },
];

export function initQueryUI(onView, onCompare) {
  const container = $('qf-fields');
  if (!container) return;

  FIELD_SPECS.forEach((f) => {
    const div = createEl('div');
    div.appendChild(createEl('label', { text: f.label }));
    let input;
    if (f.type === 'select') {
      input = createEl('select', { id: f.id });
      f.options.forEach(([v, t]) => input.appendChild(createEl('option', { value: v, text: t })));
    } else {
      input = createEl('input', { id: f.id, type: f.type, step: f.step, min: f.min });
    }
    div.appendChild(input);
    container.appendChild(div);
  });

  $('mo-query').addEventListener('click', (e) => {
    if (e.target === $('mo-query')) close();
  });

  // Bind buttons
  const searchBtn = document.querySelector('#mo-query .qa [data-q="search"]');
  const resetBtn = document.querySelector('#mo-query .qa [data-q="reset"]');
  const closeBtn = document.querySelector('#mo-query [data-q="close"]');
  if (searchBtn) searchBtn.addEventListener('click', () => search(onView, onCompare));
  if (resetBtn) resetBtn.addEventListener('click', reset);
  if (closeBtn) closeBtn.addEventListener('click', close);
}

export function open() {
  $('mo-query').classList.add('show');
  setTimeout(() => $('q-s')?.focus(), 50);
}

export function close() {
  $('mo-query').classList.remove('show');
}

export function reset() {
  FIELD_SPECS.forEach((f) => {
    const el = $(f.id);
    if (el) el.value = '';
  });
  $('q-res').innerHTML = '';
}

function g(id) {
  const el = $(id);
  return el ? el.value.trim() : '';
}
function v(id) {
  const x = parseFloat(g(id));
  return x > 0 ? x : null;
}

export async function search(onView, onCompare) {
  const filters = {};
  if (g('q-s')) filters.search = g('q-s');
  const ml = v('q-ml'); if (ml) filters.min_leo_payload = ml;
  const mc = v('q-mc'); if (mc) filters.min_c3_0_payload = mc;
  const ct = v('q-ct'), ma = v('q-ma');
  if (ct && ma) { filters.c3_target = ct; filters.min_payload_at_c3 = ma; }
  if (g('q-eng')) filters.engine_cfg = g('q-eng');
  const sm = parseInt(g('q-sn'), 10); if (sm >= 1) filters.stage_count_min = sm;
  const sx = parseInt(g('q-sx'), 10); if (sx >= 1) filters.stage_count_max = sx;
  filters.sort_by = $('q-sb')?.value || 'm_pl_max';
  filters.sort_dir = $('q-sd')?.value || 'desc';

  $('q-res').innerHTML = '<div class="ld"><div class="spin"></div>查询中...</div>';
  try {
    const r = await Api.Query.search(filters);
    renderResults(r, onView, onCompare);
  } catch (e) {
    $('q-res').innerHTML = `<span style="color:var(--red)">错误: ${escapeHtml(e.message)}</span>`;
  }
}

function renderResults(r, onView, onCompare) {
  const area = $('q-res');
  if (!r.results.length) {
    area.innerHTML = '<div class="empt"><span class="ic">🔍</span>无匹配火箭</div>';
    return;
  }
  const card = createEl('div', { style: 'font-size:.78rem;color:var(--muted);margin-bottom:8px', text: `${r.count} 枚火箭` });
  const table = createEl('table');
  table.appendChild(createEl('thead', {}, [createEl('tr', {}, ['名称', 'LEO(t)', 'C3=0(t)', '最大C3', '级数', '操作'].map((h) => createEl('th', { text: h })))]));
  const tbody = createEl('tbody');
  r.results.forEach((row) => {
    const tr = createEl('tr');
    tr.appendChild(createEl('td', {}, [createEl('strong', { text: row.name })]));
    tr.appendChild(createEl('td', { text: row.m_pl_max.toFixed(3) }));
    tr.appendChild(createEl('td', { text: row.c3_0_payload.toFixed(3) }));
    tr.appendChild(createEl('td', { text: row.c3_max.toFixed(1) }));
    tr.appendChild(createEl('td', { text: row.stage_count }));
    const act = createEl('td', { style: 'text-align:center;white-space:nowrap' });
    act.appendChild(createEl('button', { class: 'btn btn-s btn-a', text: '查看', onClick: () => { onView(row.slug); close(); } }));
    act.appendChild(document.createTextNode(' '));
    act.appendChild(createEl('button', { class: 'btn btn-s', text: '对比', onClick: () => onCompare(row.slug) }));
    tr.appendChild(act);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  area.innerHTML = '';
  area.appendChild(card);
  area.appendChild(table);
}
