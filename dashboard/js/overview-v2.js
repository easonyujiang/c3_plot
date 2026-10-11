/**
 * Overview, mission controls and vehicle list rendering.
 * This module is stateless; application state lives in state.js.
 */
import { $, createEl, formatNumber, toRgba } from './utils.js?v=2026072706';
import { getRockets, getMissionToggle } from './state-v2.js';

function table(headers, rows) {
  const el = createEl('table');
  if (headers?.length) {
    el.appendChild(createEl('thead', {}, [
      createEl('tr', {}, headers.map((header) => createEl('th', { text: header }))),
    ]));
  }
  const body = createEl('tbody');
  rows.forEach((row) => body.appendChild(row));
  el.appendChild(body);
  return el;
}

function metricRows(items) {
  return items.map(([label, value]) => createEl('tr', {}, [
    createEl('td', { text: label }),
    createEl('td', {}, [createEl('strong', { text: value })]),
  ]));
}

function card(title, content) {
  const el = createEl('section', { class: 'db-card' });
  el.appendChild(createEl('h4', { text: title }));
  el.appendChild(content);
  return el;
}

export function renderOverview(data) {
  const grid = $('overview-grid');
  if (!grid) return;
  grid.innerHTML = '';
  const source = data.source_summary || { status: 'legacy_unknown', sources: [] };
  grid.appendChild(card('数据来源与模型', table(null, metricRows([
    ['数据来源', source.status === 'recorded' ? '来源已记录' : '历史数据，来源未回填'],
    ['来源文件', (source.sources || []).map((s) => s.source_filename).join('、') || '未记录'],
    ['人工确认记录', '未单独记录'],
    ['性能结论', '经验校准估算'],
  ]))));
  const stages = data.stages || [];
  const missions = Object.values(data.missions || {});
  const parking = data.parking_orbit || {};
  const loss = data.loss_model || {};

  grid.appendChild(card('性能摘要', table(null, metricRows([
    ['最大 LEO 载荷', `${formatNumber(data.m_pl_max)} t`],
    ['C3 = 0 载荷', `${formatNumber(data.c3_0_payload)} t`],
    ['最大 C3', `${formatNumber(data.c3_max)} km²/s²`],
    ['曲线起始 C3', `${formatNumber(data.c3_min)} km²/s²`],
  ]))));
  grid.appendChild(card('停车轨道', table(null, metricRows([
    ['高度', `${formatNumber(parking.h_km)} km`],
    ['轨道速度', `${formatNumber(parking.v_orbit)} m/s`],
    ['逃逸速度', `${formatNumber(parking.v_esc)} m/s`],
    ['逃逸 Δv', `${formatNumber(parking.dv_to_escape)} m/s`],
  ]))));

  grid.appendChild(card('级段参数', table(
    ['级段', '湿质量', '干质量', '比冲 真空/海平面', '推力', '类型'],
    stages.map((stage, index) => createEl('tr', {}, [
      createEl('td', { text: `S${index + 1}` }),
      createEl('td', { text: `${formatNumber(stage.wet)} t` }),
      createEl('td', { text: `${formatNumber(stage.dry)} t` }),
      createEl('td', { text: `${formatNumber(stage.isp_vac, 1)} / ${formatNumber(stage.isp_sl, 1)} s` }),
      createEl('td', { text: `${formatNumber(stage.thrust, 1)} kN` }),
      createEl('td', { text: stage.isp_type || '--' }),
    ])),
  )));

  grid.appendChild(card('参考任务运力', table(
    ['任务', 'C3', '所需 Δv', '最大载荷'],
    missions.map((mission) => createEl('tr', {}, [
      createEl('td', {}, [createEl('span', {
        class: 'tag',
        style: `background:${toRgba(mission.color, 0.13)};color:${mission.color}`,
        text: mission.label,
      })]),
      createEl('td', { text: Number(mission.c3).toFixed(2) }),
      createEl('td', { text: `${Number(mission.dv || 0).toFixed(0)} m/s` }),
      createEl('td', { text: mission.payload == null ? '不可达' : `${Number(mission.payload).toFixed(3)} t` }),
    ])),
  )));

  grid.appendChild(card('损失模型', table(null, metricRows([
    ['L0 常数项', `${formatNumber(loss.L0)} m/s`],
    ['L1 线性项', `${formatNumber(loss.L1)} m/s/t`],
    ['L2 二次项', `${formatNumber(loss.L2)} m/s/t²`],
  ]))));
}

export function renderMissionTable(data) {
  const area = $('tbl-area');
  if (!area) return;
  const missions = Object.values(data?.missions || {});
  if (!missions.length) {
    area.innerHTML = '<div class="empt">没有参考任务数据</div>';
    return;
  }
  area.innerHTML = '';
  area.appendChild(table(
    ['任务', 'C3 (km²/s²)', '所需 Δv', '最大载荷'],
    missions.map((mission) => createEl('tr', {}, [
      createEl('td', { text: mission.label }),
      createEl('td', { text: Number(mission.c3).toFixed(2) }),
      createEl('td', { text: `${Number(mission.dv || 0).toFixed(0)} m/s` }),
      createEl('td', { text: mission.payload == null ? '不可达' : `${Number(mission.payload).toFixed(3)} t` }),
    ])),
  ));
}

function missionButton(mission, on, onToggle) {
  const button = createEl('button', {
    class: `mission-toggle${on ? ' is-on' : ''}`,
    type: 'button',
    'aria-pressed': on ? 'true' : 'false',
    'data-mission-label': mission.label,
  });
  button.style.setProperty('--mission-color', mission.color || '#8e8cff');
  button.appendChild(createEl('span', { class: 'dot' }));
  button.appendChild(document.createTextNode(mission.label));
  button.addEventListener('click', () => onToggle(mission.label, !getMissionToggle(mission.label)));
  return button;
}

export function renderToggles(data, onToggle, onAll) {
  const controls = $('msn-toggles');
  if (!controls) return;
  controls.innerHTML = '';
  const missions = Object.values(data?.missions || {});
  if (!missions.length) return;

  controls.appendChild(createEl('span', { class: 'mission-controls-label', text: '参考轨道' }));
  missions.forEach((mission) => {
    controls.appendChild(missionButton(mission, getMissionToggle(mission.label), onToggle));
  });
  controls.appendChild(createEl('span', { class: 'mission-controls-spacer' }));
  controls.appendChild(createEl('button', {
    class: 'mission-bulk-button', type: 'button', text: '全开', onClick: () => onAll(true),
  }));
  controls.appendChild(createEl('button', {
    class: 'mission-bulk-button', type: 'button', text: '全关', onClick: () => onAll(false),
  }));
}

export function updateToggleVisual(label, on) {
  document.querySelectorAll('[data-mission-label]').forEach((button) => {
    if (button.dataset.missionLabel !== label) return;
    button.classList.toggle('is-on', on);
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

export function renderRocketList(onSelect, onCompare) {
  document.body.classList.remove('has-rocket');
  setViewTitle('');
  setActions([]);
  showTabs(false);
  const grid = $('overview-grid');
  if (!grid) return;
  const rockets = getRockets();
  if (!rockets.length) {
    grid.innerHTML = '<div class="empt"><span class="ic">🚀</span><p>暂无火箭，请新建或导入配置。</p></div>';
    return;
  }

  const rows = rockets.map((rocket) => {
    const row = createEl('tr', { class: 'rocket-list-row' });
    row.addEventListener('click', () => onSelect(rocket.slug));
    row.appendChild(createEl('td', {}, [createEl('strong', { text: rocket.name })]));
    row.appendChild(createEl('td', { text: rocket.m_pl_max == null ? '--' : `${Number(rocket.m_pl_max).toFixed(3)} t` }));
    row.appendChild(createEl('td', { text: rocket.c3_0_payload == null ? '--' : `${Number(rocket.c3_0_payload).toFixed(3)} t` }));
    row.appendChild(createEl('td', { text: rocket.c3_max == null ? '--' : Number(rocket.c3_max).toFixed(1) }));
    const actions = createEl('td', { class: 'row-actions' });
    actions.appendChild(createEl('button', {
      class: 'btn btn-s btn-a', type: 'button', text: '查看',
      onClick: (event) => { event.stopPropagation(); onSelect(rocket.slug); },
    }));
    actions.appendChild(createEl('button', {
      class: 'btn btn-s', type: 'button', text: '对比',
      onClick: (event) => { event.stopPropagation(); onCompare(rocket.slug); },
    }));
    row.appendChild(actions);
    return row;
  });

  const wrapper = card(`全部火箭（${rockets.length}）`, table(
    ['名称', '最大 LEO', 'C3=0 载荷', '最大 C3', '操作'], rows,
  ));
  wrapper.style.gridColumn = '1 / -1';
  grid.innerHTML = '';
  grid.appendChild(wrapper);
}

export function setViewTitle(name) {
  const title = $('cp-tl');
  if (title) title.textContent = name ? `🚀 ${name}` : '火箭列表';
}

export function setActions(children) {
  const actions = $('cp-act');
  if (!actions) return;
  actions.innerHTML = '';
  children.forEach((child) => actions.appendChild(child));
}

export function showTabs(show) {
  const tabs = $('cp-tabs');
  if (tabs) tabs.style.display = show ? '' : 'none';
}

export function setTab(tab) {
  document.querySelectorAll('.cp-tab').forEach((button) => {
    const active = button.dataset.t === tab;
    button.classList.toggle('on', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
    button.setAttribute('tabindex', active ? '0' : '-1');
  });
  document.querySelectorAll('.cp-tabc').forEach((panel) => {
    panel.classList.toggle('show', panel.id === `tab-${tab}`);
  });
}
