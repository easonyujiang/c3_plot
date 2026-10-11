/**
 * Mission planner + high-Earth orbit C3 calculator.
 *
 * Since phase 4C the fleet results are driven by the BACKEND detailed
 * validation (verification_status / verified_feasible / verified_capacity /
 * recommendation_eligible / detailed_validation).  Legacy quick-estimate
 * fields (capacity / margin / feasible / feasible_count ...) are kept only
 * as clearly-labeled auxiliary information and never decide feasibility,
 * recommendation, ranking color or counts.
 */
import { $, createEl, escapeHtml, toast } from './utils.js?v=2026072706';
import * as Api from './api.js?v=2026073101';
import { getMissions } from './state-v2.js';

const ORBIT_PRESETS = {
  iss: { orbit_type: 'circular', altitude: 408, inclination: 51.6 },
  sso: { orbit_type: 'circular', altitude: 600, inclination: 97.8 },
  gto: { orbit_type: 'transfer', altitude: 35786, inclination: 28.5 },
  geo: { orbit_type: 'circular', altitude: 35786, inclination: 0 },
};

const STRATEGY_HINTS = {
  combined: '远地点圆轨与剩余倾角变化合并为一次点火。',
  separate: '远地点圆轨与倾角变化分开执行，因此需要额外点火和重启能力。',
};

let viewRocket = null;
let orbitRequestSeq = 0;

export function initPlanner(onViewRocket = null) {
  viewRocket = onViewRocket;
  const sel = $('mp-msn');
  if (!sel) return;

  let group = '';
  getMissions().forEach((m) => {
    if (m.group_name !== group) {
      group = m.group_name;
      const og = createEl('optgroup', { label: group });
      sel.appendChild(og);
    }
    const o = createEl('option', { value: m.c3, text: `${m.label} (C3=${Number(m.c3).toFixed(1)})` });
    sel.appendChild(o);
  });

  sel.addEventListener('change', () => {
    if (sel.value) $('mp-c3').value = sel.value;
  });

  const strategy = $('oc-strategy');
  const orbitType = $('oc-type');
  if (strategy) {
    strategy.addEventListener('change', () => updateStrategyHint());
  }
  if (orbitType) {
    orbitType.addEventListener('change', () => updateStrategyHint());
  }
  updateStrategyHint();

  $('planner-body').addEventListener('click', (e) => {
    const preset = e.target.closest('[data-orbit-preset]');
    if (preset) {
      applyOrbitPreset(preset.dataset.orbitPreset);
      return;
    }
    const btn = e.target.closest('[data-planner]');
    if (!btn) return;
    if (btn.dataset.planner === 'find') find();
    else if (btn.dataset.planner === 'orbit') runOrbitCalc();
  });
}

function updateStrategyHint() {
  const hint = $('oc-strategy-hint');
  const strategy = $('oc-strategy');
  const type = $('oc-type');
  if (!hint || !strategy) return;
  if (type && type.value === 'transfer') {
    hint.textContent = '转移轨道注入不包含目标高度圆化；到达点火策略不影响本次任务。';
    return;
  }
  hint.textContent = STRATEGY_HINTS[strategy.value] || STRATEGY_HINTS.combined;
}

function applyOrbitPreset(key) {
  const preset = ORBIT_PRESETS[key];
  if (!preset) return;
  $('oc-type').value = preset.orbit_type;
  $('oc-alt').value = preset.altitude;
  $('oc-inc').value = preset.inclination;
  document.querySelectorAll('[data-orbit-preset]').forEach((button) => {
    button.classList.toggle('is-on', button.dataset.orbitPreset === key);
  });
  updateStrategyHint();
}

async function find() {
  const c3 = parseFloat($('mp-c3').value) || parseFloat($('mp-msn').value) || 0;
  const pl = parseFloat($('mp-pl').value) || 0;
  if (!pl || pl <= 0) {
    $('mp-res').innerHTML = '<span style="color:var(--red)">请输入载荷质量</span>';
    return;
  }
  $('mp-res').innerHTML = '<div class="ld"><div class="spin"></div>搜索中...</div>';
  try {
    const r = await Api.Data.find(pl, c3);
    renderFindResults(r, pl);
  } catch (e) {
    $('mp-res').innerHTML = `<span style="color:var(--red)">错误: ${escapeHtml(e.message)}</span>`;
  }
}

function renderFindResults(r, pl) {
  const ml = r.mission ? r.mission.label : `C3=${r.c3.toFixed(1)} km²/s²`;
  const ok = r.results.filter((x) => x.feasible);
  const no = r.results.filter((x) => !x.feasible);

  const container = createEl('div');
  container.appendChild(createEl('div', { style: 'margin-bottom:8px', html: `<strong>${escapeHtml(ml)}</strong> — 载荷 <strong>${pl} t</strong> — ${r.results.length} 枚火箭` }));

  if (ok.length) {
    const table = createEl('table');
    table.appendChild(createEl('thead', {}, [createEl('tr', {}, ['火箭', 'C3 下载荷', '余量', '状态'].map((h) => createEl('th', { text: h })))]));
    const tbody = createEl('tbody');
    ok.forEach((x) => {
      tbody.appendChild(createEl('tr', {}, [
        createEl('td', {}, [createEl('strong', { text: x.rocket })]),
        createEl('td', { text: `${x.capacity.toFixed(3)} t` }),
        createEl('td', { style: 'color:var(--green)', text: `+${x.margin.toFixed(3)} t` }),
        createEl('td', {}, [createEl('span', { style: 'color:var(--green);font-weight:700', text: '✓ 可行' })]),
      ]));
    });
    table.appendChild(tbody);
    container.appendChild(table);
  } else {
    container.appendChild(createEl('p', { style: 'color:var(--red)', text: '没有火箭能完成此任务' }));
  }

  if (no.length) {
    const details = createEl('details', { style: 'margin-top:8px' });
    details.appendChild(createEl('summary', { text: `${no.length} 枚火箭不满足` }));
    const table = createEl('table', { style: 'margin-top:4px' });
    table.appendChild(createEl('thead', {}, [createEl('tr', {}, ['火箭', '能力', '不足'].map((h) => createEl('th', { text: h })))]));
    const tbody = createEl('tbody');
    no.forEach((x) => {
      tbody.appendChild(createEl('tr', {}, [
        createEl('td', { text: x.rocket }),
        createEl('td', { text: `${x.capacity.toFixed(3)} t` }),
        createEl('td', { style: 'color:var(--red)', text: x.margin.toFixed(3) }),
      ]));
    });
    table.appendChild(tbody);
    details.appendChild(table);
    container.appendChild(details);
  }

  $('mp-res').innerHTML = '';
  $('mp-res').appendChild(container);
}

// =============================================================================
// View model: authoritative fields come from the detailed validation.
// =============================================================================

function normalizeRocketValidation(item, requestedPayload) {
  const dv = item && typeof item === 'object' && item.detailed_validation
    ? item.detailed_validation : null;
  const hasDetailed = !!dv;
  const status = hasDetailed ? (dv.verification_status || 'data_insufficient') : 'data_insufficient';
  const verifiedFeasible = hasDetailed ? dv.verified_feasible : null;
  const verifiedCapacity = hasDetailed && dv.capacity
    ? (typeof dv.capacity.max_payload_t === 'number' ? dv.capacity.max_payload_t : null)
    : null;
  const isEligible = hasDetailed ? !!dv.recommendation_eligible : false;
  const detailedReason = hasDetailed
    ? (dv.failure_reason_zh || item.detailed_reason || '')
    : (item.detailed_reason || '');

  let verifiedMargin = null;
  let verifiedUtilization = null;
  if (requestedPayload != null && requestedPayload > 0 && verifiedCapacity != null) {
    verifiedMargin = verifiedCapacity - requestedPayload;
    verifiedUtilization = verifiedCapacity > 0 ? requestedPayload / verifiedCapacity : null;
  }

  let statusLabel;
  let statusTone;
  let statusReason = '';
  if (!hasDetailed) {
    statusLabel = '仅快速估算（未详细验证）';
    statusTone = 'warn';
    statusReason = '服务器未返回详细验证数据；结果仅为旧快速估算。';
  } else if (status === 'verified_feasible') {
    statusLabel = '详细验证可行';
    statusTone = 'ok';
  } else if (status === 'verified_infeasible') {
    statusLabel = '详细验证不可行';
    statusTone = 'no';
    statusReason = detailedReason || '任务级规划失败';
  } else {
    statusLabel = '数据不足';
    statusTone = 'warn';
    const phase = dv.failure_phase || '';
    const warning = (Array.isArray(dv.warnings) ? dv.warnings : [])
      .find((w) => typeof w === 'string' && w) || '';
    statusReason = detailedReason
      || (phase ? `失败阶段：${phase}` : '')
      || warning
      || '缺少足够的详细验证数据';
  }

  const quickEstimate = (item && item.quick_estimate) || {
    rocket: item?.rocket, slug: item?.slug, capacity: item?.capacity,
    requested_payload: item?.requested_payload, margin: item?.margin,
    utilization: item?.utilization, dv_feasible: item?.dv_feasible,
    supports_restart: item?.supports_restart,
    restart_required: item?.restart_required, feasible: item?.feasible,
    reason: item?.reason,
  };

  return {
    slug: item?.slug || '',
    rocket: item?.rocket || '',
    status,
    verifiedFeasible,
    verifiedCapacity,
    isEligible,
    detailedReason,
    verifiedMargin,
    verifiedUtilization,
    statusLabel,
    statusTone,
    statusReason,
    dataQuality: hasDetailed ? (dv.data_quality || null) : null,
    failurePhase: hasDetailed ? (dv.failure_phase || null) : null,
    requestedPayload: hasDetailed && dv.requested_payload
      ? dv.requested_payload : null,
    capacityBounds: hasDetailed && dv.capacity ? dv.capacity : null,
    timeline: hasDetailed && Array.isArray(dv.burn_timeline)
      ? dv.burn_timeline : null,
    warnings: hasDetailed && Array.isArray(dv.warnings) ? dv.warnings : [],
    quickEstimate,
  };
}

function normalizeMissionQueryResult(response) {
  const requestedPayload = response.payload_t != null && Number(response.payload_t) > 0
    ? Number(response.payload_t) : null;
  const results = (Array.isArray(response.results) ? response.results : [])
    .map((item) => normalizeRocketValidation(item, requestedPayload));
  const da = response.detailed_analysis || {};
  const recommendedSlug = response.recommended && response.recommended.slug
    ? response.recommended.slug : null;
  return {
    mission: response.mission || {},
    requestedPayload,
    results,
    recommendedSlug,
    feasibleCount: Number(da.verified_feasible_count != null
      ? da.verified_feasible_count : results.filter((r) => r.status === 'verified_feasible').length),
    infeasibleCount: Number(da.verified_infeasible_count || 0),
    insufficientCount: Number(da.data_insufficient_count || 0),
    legacyFeasibleCount: Number(response.feasible_count || 0),
    model: da.model || 'calibrated_serial_multiburn',
    recommendationBasis: da.recommendation_basis || 'detailed_validation',
  };
}

// Fleet sorting mirrors the backend recommendation semantics.
function fleetGroup(item) {
  if (item.isEligible) return 0;
  return item.status === 'verified_infeasible' ? 1 : 2;
}

function capacityKey(item, requestedPayload) {
  if (item.verifiedCapacity != null) {
    const margin = item.verifiedCapacity - requestedPayload;
    return margin >= 0 ? margin : 1e12 - margin;
  }
  const legacy = item.quickEstimate.margin;
  return 2e12 - (legacy != null ? legacy : -1e12);
}

function compareFleet(a, b, requestedPayload) {
  const ga = fleetGroup(a);
  const gb = fleetGroup(b);
  if (ga !== gb) return ga - gb;
  if (requestedPayload != null) {
    const ka = capacityKey(a, requestedPayload);
    const kb = capacityKey(b, requestedPayload);
    if (ka !== kb) return ka - kb;
  } else {
    const va = a.verifiedCapacity != null ? a.verifiedCapacity : -Infinity;
    const vb = b.verifiedCapacity != null ? b.verifiedCapacity : -Infinity;
    if (va !== vb) return vb - va;
  }
  return (b.quickEstimate.capacity || 0) - (a.quickEstimate.capacity || 0);
}

function fmtTons(value, digits = 3) {
  return value != null ? `${value.toFixed(digits)} t` : '—';
}

// =============================================================================
// Stable enum translations (unknown values fall back to the raw string).
// =============================================================================

const DATA_QUALITY_LABELS = {
  exact: '精确输入',
  estimated: '估算',
  insufficient: '数据不足',
};

const IGNITION_KIND_LABELS = {
  first: '首次点火',
  restart: '重启',
  unassigned: '未分配',
};

function translateDataQuality(value) {
  if (value == null) return '—';
  return DATA_QUALITY_LABELS[value] != null
    ? DATA_QUALITY_LABELS[value] : String(value);
}

function translateIgnitionKind(value) {
  if (value == null) return '未执行';
  return IGNITION_KIND_LABELS[value] != null
    ? IGNITION_KIND_LABELS[value] : String(value);
}

// =============================================================================
// Burn timeline entry semantics.
//
// rocket_calibration entries are NOT actual ignitions: their performance is
// covered by the dv_curve calibration, so they must never be shown as
// "unassigned but successful".  success === null means "not executed".
// =============================================================================

function normalizeBurnTimelineEntry(burn) {
  const source = burn && burn.source ? burn.source : 'unknown';
  const isCalibration = source === 'rocket_calibration';
  const executed = burn && burn.success != null;
  const success = executed ? burn.success : null;

  let typeLabel;
  let stageText;
  let kindLabel;
  let resultText;
  let resultTone;
  if (isCalibration) {
    typeLabel = '性能曲线标定';
    stageText = '不适用';
    kindLabel = '非实际点火';
    resultText = '由校准曲线覆盖';
    resultTone = 'muted';
  } else if (!executed) {
    typeLabel = '任务要求';
    stageText = '未分配';
    kindLabel = '未执行';
    resultText = '未执行';
    resultTone = 'muted';
  } else if (success) {
    typeLabel = '任务燃烧';
    stageText = burn.stage_index != null ? String(burn.stage_index) : '未分配';
    kindLabel = translateIgnitionKind(burn.ignition_kind);
    resultText = '成功';
    resultTone = 'ok';
  } else {
    typeLabel = '任务燃烧';
    stageText = burn.stage_index != null ? String(burn.stage_index) : '未分配';
    kindLabel = translateIgnitionKind(burn.ignition_kind);
    resultText = '失败' + (burn.failure_code ? ` · ${burn.failure_code}` : '');
    resultTone = 'no';
  }

  return {
    source,
    isCalibration,
    executed,
    success,
    typeLabel,
    stageText,
    kindLabel,
    resultText,
    resultTone,
  };
}

function burnDetailValues(entry, burn) {
  if (entry.isCalibration) {
    return {
      sourceText: '标定覆盖',
      actualText: '由标定曲线覆盖',
      beforeText: '—',
      afterText: '—',
      consumedText: '—',
    };
  }
  if (!entry.executed) {
    return {
      sourceText: '任务段',
      actualText: '—',
      beforeText: '—',
      afterText: '—',
      consumedText: '—',
    };
  }
  return {
    sourceText: '任务段',
    actualText: burn.actual_dv_m_s != null
      ? `${Number(burn.actual_dv_m_s).toFixed(1)} m/s` : '—',
    beforeText: burn.mass_before_t != null
      ? `${Number(burn.mass_before_t).toFixed(3)} t` : '—',
    afterText: burn.mass_after_t != null
      ? `${Number(burn.mass_after_t).toFixed(3)} t` : '—',
    consumedText: burn.propellant_consumed_t != null
      ? `${Number(burn.propellant_consumed_t).toFixed(4)} t` : '—',
  };
}

// =============================================================================
// Query execution
// =============================================================================

async function runOrbitCalc() {
  const readRequired = (id, name) => {
    const raw = $(id)?.value?.trim();
    if (raw === '') throw new Error(`${name}不能为空`);
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) throw new Error(`${name}必须是有效数字`);
    return parsed;
  };
  let params;
  try {
    const altitude = readRequired('oc-alt', '目标高度');
    const inclination = readRequired('oc-inc', '目标倾角');
    const parking = readRequired('oc-park', '初始停泊轨道');
    const launchLat = readRequired('oc-lat', '发射场纬度');
    const payloadRaw = $('oc-pl')?.value?.trim();
    let payload = 0;
    if (payloadRaw !== '') {
      payload = Number(payloadRaw);
      if (!Number.isFinite(payload)) throw new Error('任务载荷必须是有效数字');
    }
    if (altitude < 80 || parking < 80) throw new Error('轨道高度必须至少为 80 km');
    if (inclination < 0 || inclination > 180) throw new Error('目标倾角必须在 0° 到 180°之间');
    if (launchLat < -90 || launchLat > 90) throw new Error('发射场纬度必须在 -90° 到 90°之间');
    if (payload < 0) throw new Error('任务载荷不能为负');
    const orbitType = $('oc-type')?.value === 'transfer' ? 'transfer' : 'circular';
    const arrivalStrategy = $('oc-strategy')?.value === 'separate' ? 'separate' : 'combined';
    params = {
      orbit_type: orbitType,
      target_altitude_km: altitude,
      inclination_deg: inclination,
      parking_alt_km: parking,
      launch_lat: launchLat,
      payload_t: payload,
      arrival_strategy: arrivalStrategy,
    };
  } catch (e) {
    $('oc-res').innerHTML = `<span style="color:var(--red)">${escapeHtml(e.message)}</span>`;
    return;
  }

  const button = $('oc-run');
  const originalButtonHTML = button ? button.innerHTML : '';
  const requestId = ++orbitRequestSeq;
  if (button) {
    button.disabled = true;
    button.innerHTML = '详细验证中...';
  }
  $('oc-res').innerHTML = '<div class="ld"><div class="spin"></div>正在计算轨道预算，并对每枚火箭执行详细验证...</div>';
  try {
    const response = await Api.Data.missionQuery(params);
    if (requestId !== orbitRequestSeq) return; // stale response wins nothing
    renderOrbitResults(normalizeMissionQueryResult(response));
  } catch (e) {
    if (requestId === orbitRequestSeq) {
      $('oc-res').innerHTML = `<span style="color:var(--red)">错误: ${escapeHtml(e.message)}</span>`;
    }
  } finally {
    if (requestId === orbitRequestSeq && button) {
      button.disabled = false;
      button.innerHTML = originalButtonHTML;
    }
  }
}

// =============================================================================
// Rendering
// =============================================================================

function renderOrbitResults(model) {
  const r = model.mission;
  const container = createEl('div', { class: 'orbit-results' });
  const delivery = r.orbit_type === 'circular' ? '圆轨道交付' : '转移轨道注入';

  const summary = createEl('section', { class: 'orbit-summary' });
  summary.innerHTML =
    `<div><span class="eyebrow">MISSION SOLUTION</span><h3>${Number(r.target_altitude_km).toFixed(0)} km · ${Number(r.inclination_deg).toFixed(1)}° · ${delivery}</h3>` +
    `<p>参考停泊轨道后的等效需求为 <strong>${Number(r.required_dv).toFixed(0)} m/s</strong>。目标轨道自身 C3 为 ${Number(r.c3).toFixed(2)} km²/s²；含倾角与圆化后的查询等效 C3 为 ${Number(r.c3_effective).toFixed(2)} km²/s²。</p></div>`;
  summary.appendChild(renderRecommendation(model));
  container.appendChild(summary);

  const grid = createEl('div', { class: 'db-grid orbit-metrics' });
  const budget = createEl('div', { class: 'db-card' });
  budget.appendChild(createEl('h4', { text: '速度增量预算' }));
  budget.appendChild(createEl('table', {}, [
    createEl('tr', {}, [createEl('td', { text: '转移出发' }), createEl('td', { text: `${Number(r.dv_departure).toFixed(0)} m/s` })]),
    createEl('tr', {}, [createEl('td', { text: r.orbit_type === 'circular' ? '高点圆化 / 变轨' : '高点平面变化' }), createEl('td', { text: `${Number(r.dv_arrival).toFixed(0)} m/s` })]),
    createEl('tr', {}, [createEl('td', { text: '发射方向 / 自转修正' }), createEl('td', { text: `${Number(r.dv_launch_plane) >= 0 ? '+' : ''}${Number(r.dv_launch_plane).toFixed(0)} m/s` })]),
    createEl('tr', { class: 'orbit-total-row' }, [createEl('td', { text: '总等效需求' }), createEl('td', { text: `${Number(r.required_dv).toFixed(0)} m/s` })]),
  ]));

  const geometry = createEl('div', { class: 'db-card' });
  geometry.appendChild(createEl('h4', { text: '倾角策略' }));
  geometry.appendChild(createEl('table', {}, [
    createEl('tr', {}, [createEl('td', { text: '目标倾角' }), createEl('td', { text: `${Number(r.inclination_deg).toFixed(2)}°` })]),
    createEl('tr', {}, [createEl('td', { text: '初始直达倾角' }), createEl('td', { text: `${Number(r.parking_inc_deg).toFixed(2)}°` })]),
    createEl('tr', {}, [createEl('td', { text: '高点剩余变化' }), createEl('td', { text: `${Number(r.delta_i_deg).toFixed(2)}°` })]),
    createEl('tr', {}, [createEl('td', { text: '发射场直达范围' }), createEl('td', { text: `${Number(r.direct_inclination_min_deg).toFixed(2)}°–${Number(r.direct_inclination_max_deg).toFixed(2)}°` })]),
  ]));

  const orbit = createEl('div', { class: 'db-card' });
  orbit.appendChild(createEl('h4', { text: '目标轨道' }));
  orbit.appendChild(createEl('table', {}, [
    createEl('tr', {}, [createEl('td', { text: '轨道 C3' }), createEl('td', { text: `${Number(r.c3).toFixed(2)} km²/s²` })]),
    createEl('tr', {}, [createEl('td', { text: '半长轴' }), createEl('td', { text: `${Number(r.sma_km).toFixed(0)} km` })]),
    createEl('tr', {}, [createEl('td', { text: '周期' }), createEl('td', { text: `${(Number(r.period_min) / 60).toFixed(2)} h` })]),
    createEl('tr', {}, [createEl('td', { text: '目标点速度' }), createEl('td', { text: `${Number(r.v_target).toFixed(0)} m/s` })]),
  ]));
  grid.append(budget, geometry, orbit);
  container.appendChild(grid);

  const burnCard = createEl('section', { class: 'db-card orbit-burn-card' });
  const postCount = Number(r.post_parking_ignitions || 0);
  const totalCount = Number(r.total_ignitions || (r.burn_sequence || []).length);
  const strategyName = r.arrival_strategy === 'separate' ? '分开圆化与变轨' : '合并圆化与变轨';
  burnCard.innerHTML = `<div class="capacity-heading"><div><span class="eyebrow">BURN SEQUENCE</span><h3>分段点火计划（任务要求）</h3></div><span>${totalCount} 次总点火 · 停泊轨道后 ${postCount} 次 · ${strategyName}</span></div>`;
  const burnList = createEl('div', { class: 'orbit-burn-list' });
  (r.burn_sequence || []).forEach((burn) => {
    const source = burn.source === 'rocket_calibration' ? '由火箭标定曲线覆盖' : '任务段 Δv';
    const row = createEl('div', { class: 'orbit-burn-row' });
    row.innerHTML = `<span class="orbit-burn-order">${burn.order}</span><div><strong>${escapeHtml(burn.name)}</strong><small>${source}</small></div><b>${Number(burn.dv_m_s).toFixed(0)} m/s</b>`;
    burnList.appendChild(row);
  });
  burnCard.appendChild(burnList);
  burnCard.appendChild(createEl('p', {
    class: `orbit-restart-note${postCount > 1 ? '' : ' is-ok'}`,
    text: postCount > 1
      ? `该任务在进入 ${Number(r.parking_alt_km).toFixed(0)} km 停泊轨道后仍需 ${postCount} 次点火；详细验证会按实际级序、剩余推进剂和重复点火能力逐次分配任务燃烧。`
      : '停泊轨道后的任务段只需一次点火，未额外要求上面级重启。',
  }));
  burnCard.appendChild(createEl('p', {
    class: 'orbit-model-note',
    text: '本卡片展示的是任务要求；具体火箭是否完成，以各火箭“分析详情”中的点火时间线与验证状态为准。',
  }));
  container.appendChild(burnCard);

  if (Number(r.delta_i_deg) > 0.01 || r.orbit_type === 'transfer') {
    const notes = createEl('div', { class: 'orbit-warning' });
    if (Number(r.delta_i_deg) > 0.01) {
      notes.appendChild(createEl('p', {
        text: `发射场纬度无法直接进入 ${Number(r.inclination_deg).toFixed(2)}°：计算采用 ${Number(r.parking_inc_deg).toFixed(2)}°初始轨道，并在目标高度完成余下 ${Number(r.delta_i_deg).toFixed(2)}°。`,
      }));
    }
    if (r.orbit_type === 'transfer') {
      notes.appendChild(createEl('p', { text: '当前结果只保证进入转移椭圆，不包含目标高度圆化。' }));
    }
    container.appendChild(notes);
  }

  container.appendChild(renderCapacitySection(model));
  $('oc-res').innerHTML = '';
  $('oc-res').appendChild(container);
}

function renderRecommendation(model) {
  const box = createEl('div', {
    class: model.recommendedSlug ? 'recommendation' : 'recommendation is-empty',
  });
  if (!model.recommendedSlug) {
    let text = '火箭库为空，暂无可推荐载具';
    if (model.results.length) {
      if (model.insufficientCount > 0 && model.feasibleCount === 0) {
        text = '存在数据不足，无法形成可靠推荐';
      } else if (model.feasibleCount === 0) {
        text = '全部火箭详细验证不可行';
      } else {
        text = '暂无满足条件的推荐';
      }
    }
    box.innerHTML = `<span>推荐载具</span><strong>暂无推荐</strong><small>${escapeHtml(text)}</small>`;
    return box;
  }
  const item = model.results.find((r) => r.slug === model.recommendedSlug);
  if (!item) {
    box.innerHTML = '<span>推荐载具</span><strong>暂无推荐</strong><small>推荐条目缺失</small>';
    return box;
  }
  const rows = [];
  rows.push(`已验证运力：${fmtTons(item.verifiedCapacity)}`);
  if (model.requestedPayload != null) {
    rows.push(`任务载荷：${fmtTons(model.requestedPayload)}`);
    if (item.verifiedMargin != null) {
      rows.push(`运力余量：${item.verifiedMargin >= 0 ? '+' : ''}${item.verifiedMargin.toFixed(3)} t`);
    }
  }
  box.innerHTML =
    `<span>推荐载具 · 详细验证</span><strong>${escapeHtml(item.rocket)}</strong>` +
    `<span class="rec-metrics">${rows.map((t) => escapeHtml(t)).join('<br>')}</span>` +
    `<span class="vs-badge is-ok">详细验证可行</span>`;
  return box;
}

function renderCapacitySection(model) {
  const section = createEl('section', { class: 'capacity-section' });
  const hasPayload = model.requestedPayload != null;
  const payloadLabel = hasPayload
    ? `指定载荷 ${model.requestedPayload.toFixed(3)} t · 详细验证可行 ${model.feasibleCount} 枚 · 不可行 ${model.infeasibleCount} · 数据不足 ${model.insufficientCount}`
    : '按详细验证最大运力排序';
  section.innerHTML = `<div class="capacity-heading"><div><span class="eyebrow">FLEET RANKING</span><h3>全库火箭匹配（详细验证）</h3></div><span>${payloadLabel}</span></div>`;

  if (!model.results.length) {
    section.appendChild(createEl('div', { class: 'empt', text: '数据库中还没有已计算的火箭' }));
    return section;
  }

  const sorted = [...model.results].sort((a, b) => compareFleet(a, b, model.requestedPayload));
  const headers = hasPayload
    ? ['排名', '火箭', '已验证运力', '指定载荷', '已验证余量', '利用率', '验证状态', '操作']
    : ['排名', '火箭', '已验证最大运力', '数据质量', '验证状态', '操作'];
  const table = createEl('table', { class: 'capacity-table' });
  table.appendChild(createEl('thead', {}, [
    createEl('tr', {}, headers.map((label) => createEl('th', { text: label }))),
  ]));
  const body = createEl('tbody');
  sorted.forEach((item, index) => {
    const isRecommended = item.slug === model.recommendedSlug;
    const cells = [
      createEl('td', { text: String(index + 1) }),
      createEl('td', {}, [
        ...(isRecommended
          ? [createEl('span', { class: 'rec-tag', text: '推荐' })]
          : []),
        createEl('strong', { text: item.rocket }),
      ]),
      createEl('td', { text: fmtTons(item.verifiedCapacity) }),
    ];
    if (hasPayload) {
      cells.push(createEl('td', { text: fmtTons(model.requestedPayload) }));
      cells.push(createEl('td', {
        class: item.verifiedMargin != null
          ? (item.verifiedMargin >= 0 ? 'capacity-ok' : 'capacity-no')
          : '',
        text: item.verifiedMargin != null
          ? `${item.verifiedMargin >= 0 ? '+' : ''}${item.verifiedMargin.toFixed(3)} t`
          : '—',
      }));
      cells.push(createEl('td', {
        text: item.verifiedUtilization != null ? `${(item.verifiedUtilization * 100).toFixed(1)}%` : '—',
      }));
    } else {
      cells.push(createEl('td', { text: translateDataQuality(item.dataQuality) }));
    }
    cells.push(createEl('td', {}, [
      createEl('span', {
        class: `vs-badge is-${item.statusTone}`,
        text: item.statusLabel,
        title: item.statusReason,
      }),
    ]));
    const action = createEl('td');
    action.appendChild(createEl('button', {
      class: 'btn btn-s',
      text: '查看',
      onClick: () => viewRocket?.(item.slug),
    }));
    cells.push(action);
    const row = createEl('tr', { class: isRecommended ? 'is-recommended' : '' }, cells);
    body.appendChild(row);
    body.appendChild(renderFleetDetailRow(item, model, hasPayload, headers.length));
  });
  table.appendChild(body);
  section.appendChild(table);

  section.appendChild(createEl('p', {
    class: 'orbit-model-note',
    text: `模型：${model.model} · 推荐依据：${model.recommendationBasis} · 旧快速估算认为可行 ${model.legacyFeasibleCount} 枚（仅供对比，不作为可行性依据）。`,
  }));
  return section;
}

function renderFleetDetailRow(item, model, hasPayload, colspan) {
  const details = createEl('details', { class: 'fleet-details' });
  const summaryText = item.status === 'verified_feasible'
    ? '查看详细验证与点火时间线'
    : `查看详细验证 · ${item.statusLabel}`;
  details.appendChild(createEl('summary', { text: summaryText }));

  const grid = createEl('div', { class: 'fleet-details-grid' });

  const meta = createEl('div');
  meta.innerHTML = `<strong>验证信息</strong>`;
  const metaRows = [];
  metaRows.push(`验证状态：${item.statusLabel}`);
  metaRows.push(`数据质量：${escapeHtml(translateDataQuality(item.dataQuality))}`);
  if (item.failurePhase) metaRows.push(`失败阶段：${item.failurePhase}`);
  if (item.statusReason) metaRows.push(`说明：${escapeHtml(item.statusReason)}`);
  if (item.verifiedCapacity != null) metaRows.push(`已验证运力：${item.verifiedCapacity.toFixed(4)} t`);
  if (item.capacityBounds) {
    const c = item.capacityBounds;
    const lower = c.feasible_lower_bound_t != null ? `${c.feasible_lower_bound_t.toFixed(4)} t` : '—';
    const upper = c.infeasible_upper_bound_t != null ? `${c.infeasible_upper_bound_t.toFixed(4)} t` : '—';
    metaRows.push(`容量边界：可行下界 ${lower} / 不可行上界 ${upper}`);
    if (c.boundary_width_t != null) metaRows.push(`边界宽度：${c.boundary_width_t.toFixed(5)} t（容差 ${c.payload_tolerance_t}）`);
    if (c.evaluation_count != null) metaRows.push(`评估次数：${c.evaluation_count}`);
  }
  if (model.requestedPayload != null) {
    metaRows.push(`任务载荷：${model.requestedPayload.toFixed(3)} t`);
    if (item.verifiedMargin != null) {
      metaRows.push(`已验证余量：${item.verifiedMargin >= 0 ? '+' : ''}${item.verifiedMargin.toFixed(3)} t`);
    }
    if (item.verifiedUtilization != null) {
      metaRows.push(`利用率：${(item.verifiedUtilization * 100).toFixed(1)}%`);
    }
  }
  meta.innerHTML = `<strong>验证信息</strong>${metaRows.map((t) => `<div class="da-line">${t}</div>`).join('')}`;
  grid.appendChild(meta);

  if (Array.isArray(item.warnings) && item.warnings.length) {
    const warn = createEl('div');
    warn.innerHTML = `<strong>警告</strong>${item.warnings.map((w) => `<div class="da-line da-warn">${escapeHtml(w)}</div>`).join('')}`;
    grid.appendChild(warn);
  }

  grid.appendChild(renderTimeline(item));
  grid.appendChild(renderQuickEstimateNote(item));

  details.appendChild(grid);
  const wrapper = createEl('tr', { class: 'fleet-detail-row' });
  wrapper.appendChild(createEl('td', { colspan }, [details]));
  return wrapper;
}

function renderTimeline(item) {
  const box = createEl('div');
  box.innerHTML = `<strong>点火时间线（任务要求 → 该火箭执行结果）</strong>`;
  const timeline = item.timeline;
  if (!timeline || !timeline.length) {
    box.appendChild(createEl('div', { class: 'da-line', text: '没有可用的点火时间线数据。' }));
    return box;
  }
  const table = createEl('table', { class: 'timeline-table' });
  table.appendChild(createEl('thead', {}, [
    createEl('tr', {}, ['顺序', '点火名称', '类型', '目标 Δv', '执行级', '点火类型', '结果'].map((h) => createEl('th', { text: h }))),
  ]));
  const body = createEl('tbody');
  timeline.forEach((burn) => {
    const entry = normalizeBurnTimelineEntry(burn);
    const detail = burnDetailValues(entry, burn);
    body.appendChild(createEl('tr', {}, [
      createEl('td', { text: String(burn.order) }),
      createEl('td', { text: burn.name || burn.burn_id }),
      createEl('td', { text: entry.typeLabel }),
      createEl('td', { text: burn.target_dv_m_s != null ? `${Number(burn.target_dv_m_s).toFixed(0)} m/s` : '—' }),
      createEl('td', { text: entry.stageText }),
      createEl('td', { text: entry.kindLabel }),
      createEl('td', { class: entry.resultTone === 'ok' ? 'capacity-ok'
        : (entry.resultTone === 'no' ? 'capacity-no' : 'capacity-muted'),
        text: entry.resultText }),
    ]));
    const detailLine = [
      `来源：${detail.sourceText}`,
      `实际 Δv：${detail.actualText}`,
      `燃烧前质量：${detail.beforeText}`,
      `燃烧后质量：${detail.afterText}`,
      `推进剂消耗：${detail.consumedText}`,
    ].join(' · ');
    body.appendChild(createEl('tr', { class: 'timeline-detail-row' }, [
      createEl('td', { colspan: 7, text: detailLine }),
    ]));
  });
  table.appendChild(body);
  box.appendChild(table);
  return box;
}

function renderQuickEstimateNote(item) {
  const q = item.quickEstimate || {};
  const qs = [
    `旧快速估算：运力 ${q.capacity != null ? `${Number(q.capacity).toFixed(3)} t` : '—'}`,
    q.margin != null ? `余量 ${Number(q.margin).toFixed(3)} t` : null,
    `可行性 ${q.feasible ? '可行' : '不可行'}`,
    q.reason ? `原因：${q.reason}` : null,
  ].filter(Boolean).join(' · ');
  return createEl('div', {
    class: 'quick-estimate-note',
    text: qs || '无快速估算数据',
    title: '旧 dv_curve 快速估算结果，不作为可行性依据',
  });
}
