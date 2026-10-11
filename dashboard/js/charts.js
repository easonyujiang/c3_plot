/**
 * Plotly chart rendering.
 */
import { isDark, getMissionToggle } from './state-v2.js';
import { toRgba } from './utils.js?v=2026072706';
import { withFairingAllowance } from './fairing.js';
let comparisonSource = null;

export function redrawFairing(data) {
  if (comparisonSource) drawCompare(comparisonSource);
  else if (data) { drawC3Mass(data); drawDvMass(data); }
}

function chartData(data) {
  const enabled = document.getElementById('chart-fairing-enabled')?.checked;
  const input = document.getElementById('chart-fairing-mass');
  const mass = enabled ? Number(input?.value) : 0;
  const valid = Number.isFinite(mass) && mass >= 0 && (!enabled || input?.value.trim() !== '');
  if (input) input.setCustomValidity(valid ? '' : '请输入非负整流罩质量');
  const note = document.getElementById('chart-fairing-note');
  if (note) note.textContent = !valid ? '请输入非负有限质量，当前暂不扣减。'
    : enabled ? `图表净载荷 = 原运力 − ${mass.toFixed(3)} t；仅扣减，不模拟抛弃。任务查询及 CSV/JSON 仍为原始运力。`
    : '仅扣减图表可用载荷；不模拟抛弃，不修改基准点。已计入箭体的整流罩无需重复扣除。';
  return withFairingAllowance(data, valid ? mass : 0);
}

const COLORS = {
  primary: '#2563eb',
  gridDark: '#2a2a45',
  gridLight: '#e0e0e0',
  textDark: '#e0e0e0',
  textLight: '#1a1a2e',
  bgDark: '#1a1a30',
  bgLight: '#ffffff',
};

function theme() {
  const dark = isDark();
  return {
    cl: dark ? COLORS.textDark : COLORS.textLight,
    gc: dark ? COLORS.gridDark : COLORS.gridLight,
    bg: dark ? COLORS.bgDark : COLORS.bgLight,
    line: COLORS.primary,
  };
}

function baseLayout(xtitle, ytitle) {
  const t = theme();
  return {
    xaxis: { title: xtitle, gridcolor: t.gc, color: t.cl },
    yaxis: { title: ytitle, gridcolor: t.gc, rangemode: 'tozero', color: t.cl },
    margin: { l: 60, r: 30, t: 20, b: 50 },
    paper_bgcolor: t.bg,
    plot_bgcolor: t.bg,
    hovermode: 'closest',
    dragmode: 'pan',
    uirevision: 'c3-mass-view',
  };
}

const plotConfig = {
  responsive: true,
  displaylogo: false,
  scrollZoom: true,
  doubleClick: 'reset',
};

function guardChartWheel(id) {
  const element = document.getElementById(id);
  if (!element || element.dataset.wheelGuard === '1') return;
  element.dataset.wheelGuard = '1';
  element.addEventListener('wheel', (event) => event.preventDefault(), { passive: false });
}

function renderPlot(id, traces, layout) {
  Plotly.react(id, traces, layout, plotConfig).then(() => guardChartWheel(id));
}

export function drawC3Mass(data) {
  comparisonSource = null;
  data = chartData(data);
  const t = theme();
  const c3x = data.c3_mass_curve.map((p) => p.c3);
  const payy = data.c3_mass_curve.map((p) => p.payload);
  const traces = [{
    x: c3x,
    y: payy,
    type: 'scatter',
    mode: 'lines',
    name: data.fairing_allowance_t ? '净载荷（已扣整流罩）' : '载荷能力',
    line: { color: t.line, width: 3 },
    fill: 'tozeroy',
    fillcolor: toRgba(t.line, 0.08),
  }];

  const cmin = Math.min(...c3x);
  const cmax = Math.max(...c3x);
  const shapes = [];
  const annotations = [];
  let annIdx = 0;

  Object.values(data.missions || {}).forEach((m) => {
    if (!getMissionToggle(m.label) || m.c3 < cmin || m.c3 > cmax) return;
    shapes.push({
      type: 'line',
      x0: m.c3, x1: m.c3, y0: 0, y1: 1, yref: 'paper',
      line: { color: m.color, dash: 'dash', width: 2 },
      opacity: 0.6,
    });
    annotations.push({
      x: m.c3, y: 1, xref: 'x', yref: 'paper',
      text: `<b>${m.label}</b><br>${m.payload != null ? m.payload.toFixed(2) + ' t' : '—'}`,
      showarrow: false,
      font: { color: m.color, size: 9 },
      yshift: -(annIdx * 22 + 14),
      bgcolor: toRgba(t.bg, 0.8),
      borderpad: 3,
    });
    annIdx += 1;
  });

  renderPlot('ch-c3', traces, {
    ...baseLayout('C3 (km²/s²)', '载荷质量 (t)'),
    shapes,
    annotations,
  });
}

export function drawDvMass(data) {
  data = chartData(data);
  const t = theme();
  const mx = data.dv_curve.map((p) => p.m);
  const dvy = data.dv_curve.map((p) => p.dv);
  const traces = [{
    x: mx,
    y: dvy,
    type: 'scatter',
    mode: 'lines',
    name: '剩余 dv',
    line: { color: t.line, width: 3 },
    fill: 'tozeroy',
    fillcolor: toRgba(t.line, 0.08),
  }];

  const dvmax = Math.max(...dvy);
  const shapes = [];
  const annotations = [];

  Object.values(data.missions || {}).forEach((m) => {
    if (!getMissionToggle(m.label) || m.dv <= 0 || m.dv > dvmax) return;
    shapes.push({
      type: 'line',
      x0: 0, x1: 1, y0: m.dv, y1: m.dv, xref: 'paper',
      line: { color: m.color, dash: 'dash', width: 2 },
      opacity: 0.6,
    });
    annotations.push({
      x: 1, y: m.dv, xref: 'paper', yref: 'y',
      text: `<b>${m.label}</b> ${m.dv.toFixed(0)} m/s`,
      showarrow: false,
      font: { color: m.color, size: 9 },
      xanchor: 'right',
      xshift: -8,
      bgcolor: toRgba(t.bg, 0.8),
      borderpad: 3,
    });
  });

  renderPlot('ch-dv', traces, {
    ...baseLayout('载荷质量 (t)', '剩余 dv (m/s)'),
    shapes,
    annotations,
  });
}

export function drawCompare(rockets) {
  comparisonSource = rockets;
  rockets = rockets.map(chartData);
  const t = theme();
  const palette = ['#2563eb', '#e74c3c', '#27ae60', '#f59e0b', '#9b59b6', '#17becf'];
  const t1 = [];
  const t2 = [];
  rockets.forEach((rk, i) => {
    const c = palette[i % palette.length];
    t1.push({
      x: rk.c3_mass_curve.map((p) => p.c3),
      y: rk.c3_mass_curve.map((p) => p.payload),
      type: 'scatter', mode: 'lines',
      name: rk.name, line: { color: c, width: 3 },
    });
    t2.push({
      x: rk.dv_curve.map((p) => p.m),
      y: rk.dv_curve.map((p) => p.dv),
      type: 'scatter', mode: 'lines',
      name: rk.name, line: { color: c, width: 3 },
    });
  });

  renderPlot('ch-c3', t1, {
    ...baseLayout('C3 (km²/s²)', '载荷质量 (t)'),
  });
  renderPlot('ch-dv', t2, {
    ...baseLayout('载荷质量 (t)', '剩余 dv (m/s)'),
  });
}

export function relayout() {
  const t = theme();
  ['ch-c3', 'ch-dv'].forEach((id) => {
    const el = document.getElementById(id);
    if (!el || !el.data) return;
    Plotly.relayout(id, {
      'paper_bgcolor': t.bg,
      'plot_bgcolor': t.bg,
      'font.color': t.cl,
      'xaxis.color': t.cl,
      'xaxis.gridcolor': t.gc,
      'yaxis.color': t.cl,
      'yaxis.gridcolor': t.gc,
    });
  });
}
