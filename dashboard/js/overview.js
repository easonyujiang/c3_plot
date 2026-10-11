/**
 * Overview cards, mission table, mission toggles, and rocket list rendering.
 */
import { $, createEl, escapeHtml, formatNumber, toRgba } from './utils.js';
import { getRockets, getCurrentTab, getMissionToggle } from './state.js';

export function renderOverview(data) {
  const grid = $('overview-grid');
  if (!grid) return;
  grid.innerHTML = '';

  const missions = data.missions || {};
  const stages = data.stages || [];
  const loss = data.loss_model || {};
  const park = data.parking_orbit || {};

    const missionRows = Object.values(missions).map((m) => createEl('tr', {}, [
      createEl('td', {}, [createEl('span', { class: 'tag', style: `background:${toRgba(m.color, 0.13)};color:${m.color}`, text: m.label })]),
      createEl('td', { text: m.c3.toFixed(1) }),
      createEl('td', { text: `${m.dv.toFixed(0)} m/s` }),
      createEl('td', {}, [createEl('strong', { text: m.payload != null ? `${m.payload.toFixed(3)} t` : 'N/A' })]),
    ]));

  const stageRows = stages.map((st, i) => {
    let label = `S${i}`;
    let style = '';
    if (st.isp_type === 'booster') { label = '🚀 助推段'; style = 'color:var(--orange)'; }
    else if (st.isp_type === 'core') { label = '🔵 芯级段'; style = 'color:var(--accent)'; }
    return createEl('tr', {}, [
      createEl('td', { style, text: label }),
      createEl('td', { text: `${st.wet.toFixed(1)} t` }),
      createEl('td', { text: `${st.dry.toFixed(1)} t` }),
      createEl('td', { text: `${st.isp_vac}s / ${st.isp_sl}s` }),
      createEl('td', { text: `${st.thrust.toFixed(0)} kN` }),
      createEl('td', { text: st.isp_type || '' }),
    ]);
  });

  const cards = [
    ['性能摘要', [
      ['最大 LEO 载荷', `${formatNumber(data.m_pl_max)} t`],
      ['C3 = 0 载荷', `${formatNumber(data.c3_0_payload)} t`],
      ['最大 C3 (~10kg)', `${formatNumber(data.c3_max)} km²/s²`],
      ['最小 C3', `${formatNumber(data.c3_min)} km²/s²`],
    ]],
    ['停泊轨道', [
      ['高度', `${park.h_km} km`],
      ['轨道速度', `${park.v_orbit} m/s`],
      ['逃逸速度', `${park.v_esc} m/s`],
      ['逃逸 dv', `${park.dv_to_escape} m/s`],
    ]],
    ['级段参数', stageRows, true, ['#', '湿质量', '干质量', '比冲(真空/海平面)', '推力', '类型']],
    ['任务载荷', missionRows, true, ['任务', 'C3', 'dv', '最大载荷']],
    ['损失模型', [
      ['L0 (常数)', `${loss.L0} m/s`],
      ['L1 (线性)', `${loss.L1} m/s/t`],
      ['L2 (二次)', `${loss.L2} m/s/t²`],
    ]],
  ];

  cards.forEach(([title, content, isTable, headers]) => {
    const card = createEl('div', { class: 'db-card' });
    card.appendChild(createEl('h4', { text: title }));
    if (isTable) {
      const table = createEl('table');
      if (headers) {
        const thead = createEl('thead');
        thead.appendChild(createEl('tr', {}, headers.map((h) => createEl('th', { text: h }))));
        table.appendChild(thead);
      }
      const tbody = createEl('tbody');
      content.forEach((row) => tbody.appendChild(row));
      table.appendChild(tbody);
      card.appendChild(table);
    } else {
      const table = createEl('table');
      content.forEach(([k, v]) => {
        table.appendChild(createEl('tr', {}, [
          createEl('td', { text: k }),
          createEl('td', { text: v }),
        ]));
      });
      card.appendChild(table);
    }
    grid.appendChild(card);
  });
}

export function renderMissionTable(data) {
  const area = $('tbl-area');
  if (!area) return;
  if (!data || !data.missions) {
    area.innerHTML = '<div class="empt"><span class="ic">📋</span>无数据</div>';
    return;
  }
  const ms = Object.values(data.missions);
  const rows = ms.map((m) => createEl('tr', {}, [
    createEl('td', {}, [createEl('span', { class: 'tag', style: `background:${toRgba(m.color, 0.13)};color:${m.color}`, text: m.label })]),
    createEl('td', { style: `color:${m.c3 >= 0 ? 'var(--red)' : 'var(--accent)'}`, text: m.c3.toFixed(1) }),
    createEl('td', { text: `${m.dv.toFixed(0)} m/s` }),
    createEl('td', {}, [m.payload != null ? createEl('strong', { text: `${m.payload.toFixed(3)} t` }) : createEl('span', { style: 'color:var(--muted)', text: 'N/A' })]),
  ]));

  const table = createEl('table');
  table.appendChild(createEl('thead', {}, [createEl('tr', {}, ['任务', 'C3 (km²/s²)', '所需 dv', '最大载荷'].map((h) => createEl('th', { text: h })))]));
  const tbody = createEl('tbody');
  rows.forEach((r) => tbody.appendChild(r));
  table.appendChild(tbody);
  area.innerHTML = '';
  area.appendChild(table);
}

export function renderToggles(data, onToggle, onAll) {
  const el = $('msn-toggles');
  if (!el) return;
  el.innerHTML = '';
  const ms = Object.values(data.missions || {});
  if (!ms.length) return;

  el.appendChild(createEl('span', { style: 'font-size:.7rem;color:var(--muted);font-weight:600;margin-right:4px', text: '参考线:' }));
  ms.forEach((m) => {
    const labelId = m.label.replace(/\s+/g, '_');
    const on = getMissionToggle(m.label);
    const bg = toRgba(m.color, 0.13);
    const lbl = createEl('label', {
      class: 'msn-tgl',
      style: `background:${bg};border-color:${m.color}`,
      id: `tgl-${labelId}`,
      'data-label': m.label,
      'data-color': m.color,
    });
    const cb = createEl('input', { type: 'checkbox', checked: on, style: 'display:none' });
    cb.addEventListener('change', (e) => onToggle(m.label, e.target.checked));
    lbl.appendChild(cb);
    lbl.appendChild(createEl('span', { class: 'dot', style: `background:${m.color}` }));
    lbl.appendChild(document.createTextNode(m.label));
    el.appendChild(lbl);
  });

  el.appendChild(createEl('button', { class: 'btn btn-xs', text: '全开', onClick: () => onAll(true) }));
  el.appendChild(createEl('button', { class: 'btn btn-xs', text: '全关', onClick: () => onAll(false) }));
}

export function updateToggleVisual(label, on) {
  const labelId = label.replace(/\s+/g, '_');
  const el = $(`tgl-${labelId}`);
  if (!el) return;
  const color = el.dataset.color || 'var(--muted)';
  el.style.backgroundColor = on ? toRgba(color, 0.13) : 'var(--bg)';
  el.style.opacity = on ? '1' : '0.5';
}

export function renderRocketList(onSelect, onCompare) {
  document.body.classList.remove('has-rocket');
  const grid = $('overview-grid');
  const title = $('cp-tl');
  const actions = $('cp-act');
  const tabs = $('cp-tabs');
  if (title) title.textContent = '火箭列表';
  if (actions) actions.innerHTML = '';
  if (tabs) tabs.style.display = 'none';
  if (!grid) return;

  const rockets = getRockets();
  if (!rockets.length) {
    grid.innerHTML = '<div class="empt"><span class="ic">🚀</span><p>暂无火箭，点击侧边栏 +🚀 新建</p></div>';
    return;
  }

  const card = createEl('div', { class: 'db-card', style: 'grid-column:1/-1' });
  card.appendChild(createEl('h4', { text: `全部火箭 (${rockets.length})` }));
  const table = createEl('table');
  table.appendChild(createEl('thead', {}, [createEl('tr', {}, ['名称', '最大 LEO', 'C3=0 载荷', '最大 C3', '操作'].map((h, i) => createEl('th', { text: h, style: i === 4 ? 'text-align:center' : '' })))]));
  const tbody = createEl('tbody');
  rockets.forEach((r) => {
    const leo = r.m_pl_max != null ? `${r.m_pl_max.toFixed(1)} t` : '--';
    const c3zero = r.c3_0_payload != null ? `${r.c3_0_payload.toFixed(3)} t` : '--';
    const c3max = r.c3_max != null ? r.c3_max.toFixed(1) : '--';
    const tr = createEl('tr', { style: 'cursor:pointer' });
    tr.addEventListener('click', () => onSelect(r.slug));
    tr.appendChild(createEl('td', {}, [createEl('strong', { text: `🚀 ${r.name}` })]));
    tr.appendChild(createEl('td', { text: leo }));
    tr.appendChild(createEl('td', { text: c3zero }));
    tr.appendChild(createEl('td', { text: c3max }));
    const act = createEl('td', { style: 'text-align:center' });
    act.appendChild(createEl('button', { class: 'btn btn-s btn-a', text: '查看', onClick: (e) => { e.stopPropagation(); onSelect(r.slug); } }));
    act.appendChild(document.createTextNode(' '));
    act.appendChild(createEl('button', { class: 'btn btn-s', text: '对比', onClick: (e) => { e.stopPropagation(); onCompare(r.slug); } }));
    tr.appendChild(act);
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  card.appendChild(table);
  grid.innerHTML = '';
  grid.appendChild(card);
}

export function setViewTitle(name) {
  const el = $('cp-tl');
  if (el) el.textContent = name ? `🚀 ${name}` : '火箭列表';
}

export function setActions(children) {
  const el = $('cp-act');
  if (!el) return;
  el.innerHTML = '';
  children.forEach((c) => el.appendChild(c));
}

export function showTabs(show) {
  const el = $('cp-tabs');
  if (el) el.style.display = show ? '' : 'none';
}

export function setTab(tab) {
  document.querySelectorAll('.cp-tab').forEach((el) => {
    const active = el.dataset.t === tab;
    el.classList.toggle('on', active);
    el.setAttribute('aria-selected', active ? 'true' : 'false');
    el.setAttribute('tabindex', active ? '0' : '-1');
  });
  document.querySelectorAll('.cp-tabc').forEach((el) => {
    el.classList.toggle('show', el.id === `tab-${tab}`);
  });
}
