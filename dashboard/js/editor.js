/**
 * Rocket create/edit modal.
 */
import { $, createEl, escapeHtml, toast } from './utils.js?v=2026072706';
import { getEngines, getEditingSlug, setEditingSlug } from './state-v2.js';
import * as Api from './api.js?v=2026073103';

const DEFAULT_MISSIONS = [
  { label: 'LEO', c3: -56, color: '#27ae60' },
  { label: 'GTO', c3: -16.35, color: 'orange' },
  { label: 'TLI', c3: -2.01, color: 'purple' },
  { label: 'Earth Escape', c3: 0, color: 'darkred' },
  { label: 'Mars', c3: 8.7, color: 'sienna' },
];

export function initEditorEvents(onSaved) {
  const modal = $('mo-editor');
  if (!modal) return;

  // Close on backdrop click
  modal.addEventListener('click', (e) => {
    if (e.target === modal) close();
  });

  // Editor-level action delegation
  modal.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-ed-action]');
    if (!btn) return;
    const action = btn.dataset.edAction;
    if (action === 'add-stage') addStage();
    else if (action === 'add-cal') addCal();
    else if (action === 'add-mission') addMission();
    else if (action === 'remove') btn.closest('.sr').remove();
    else if (action === 'renumber') renumberStages();
    else if (action === 'save') save(onSaved);
    else if (action === 'cancel') close();
  });

  modal.addEventListener('change', (e) => {
    if (e.target.matches('[data-f="parallel"]')) renumberStages();
  });
}

export async function open(slug = null) {
  setEditingSlug(slug);
  $('ed-title').textContent = slug ? '编辑火箭' : '新建火箭';
  $('ed-stages').innerHTML = '';
  $('ed-cals').innerHTML = '';
  $('ed-missions').innerHTML = '';
  $('ed-err').textContent = '';
  $('ed-notice').style.display = 'none';
  $('ed-notice').textContent = '';
  $('mo-editor').classList.add('show');
  setTimeout(() => $('ed-name')?.focus(), 50);

  if (slug) {
    try {
      const cfg = await Api.Rockets.get(slug);
      $('ed-name').value = cfg.rocket.name;
      cfg.stages.forEach((s) => addStage(s));
      cfg.calibration.forEach((c) => addCal(c));
      if (cfg.reference_missions) {
        Object.values(cfg.reference_missions).forEach((m) => addMission(m));
      }
    } catch (e) {
      toast(`加载失败: ${e.message}`, 'err');
      close();
    }
  } else {
    $('ed-name').value = '';
    addStage();
    addStage();
    addCal();
    addCal();
    addCal();
    DEFAULT_MISSIONS.forEach((m) => addMission(m));
  }
  renumberStages();
}

export function openDraft(draft, warnings = [], structured = []) {
  setEditingSlug(null);
  $('ed-title').textContent = '确认 VAB 导入草稿';
  $('ed-stages').innerHTML = '';
  $('ed-cals').innerHTML = '';
  $('ed-missions').innerHTML = '';
  $('ed-err').textContent = '';
  $('ed-name').value = draft?.rocket?.name || '';

  const stages = Array.isArray(draft?.stages) ? draft.stages : [];
  stages.forEach((stage) => addStage(stage));
  if (!stages.length) addStage();

  // Craft files do not contain the three performance calibration samples
  // required by the C3 model. Provide empty rows instead of fabricating data.
  addCal();
  addCal();
  addCal();
  DEFAULT_MISSIONS.forEach((mission) => addMission(mission));

  const notice = $('ed-notice');
  notice.innerHTML = renderDraftWarnings(warnings, structured);
  notice.style.display = '';
  $('mo-editor').classList.add('show');
  renumberStages();

  const firstInvalid = document.querySelector(
    '#ed-stages input[data-f="wet"][value="0"], #ed-stages select[data-f="cfg"]',
  );
  setTimeout(() => (firstInvalid || $('ed-name'))?.focus(), 50);
}

const ED_SEVERITY_LABEL = { error: '【错误】', warning: '【警告】', info: '【提示】' };

function renderDraftWarnings(legacyWarnings, structured) {
  const items = [];
  const seen = new Set();
  (structured || []).forEach((w) => {
    if (!w || w.severity === 'error') return;
    const dedupeKey = `${w.code || ''}:${w.message || ''}`;
    if (seen.has(dedupeKey)) return;
    seen.add(dedupeKey);
    const label = ED_SEVERITY_LABEL[w.severity] || '';
    const highlight = (w.code === 'mass_not_confirmed' || w.code === 'stage_structure_unconfirmed') ? ' ed-warn-key' : '';
    const impact = w.impact ? `<div class="ed-warn-impact">影响：${escapeHtml(w.impact)}</div>` : '';
    items.push(`<li class="ed-warn ed-warn-${w.severity || 'warning'}${highlight}"><span class="ed-warn-tag">${label}</span>${escapeHtml(w.message)}${impact}</li>`);
  });
  if (!items.length) {
    (legacyWarnings || []).forEach((w) => {
      if (seen.has(w)) return;
      seen.add(w);
      items.push(`<li class="ed-warn">${escapeHtml(w)}</li>`);
    });
  }
  const intro = '已从 .craft 预填载具结构。请确认各级湿/干质量、发动机映射，并填写 3 个实测校准点。';
  return `<div>${intro}</div>` + (items.length ? `<ul class="ed-warns">${items.join('')}</ul>` : '');
}

export function close() {
  $('mo-editor').classList.remove('show');
  setEditingSlug(null);
}

function engineOptions(selected) {
  const engines = getEngines();
  if (!engines.length) return '<option value="">-- 无发动机数据 --</option>';
  const known = engines.some((engine) => (engine.cfg_filename || engine) === selected);
  const placeholder = `<option value=""${known ? '' : ' selected'}>-- 请选择 / 需要映射 --</option>`;
  return placeholder + engines.map((e) => {
    const fn = e.cfg_filename || e;
    const nm = (e.engine_name || fn || '').substring(0, 50);
    const sel = fn === selected ? ' selected' : '';
    return `<option value="${escapeHtml(fn)}"${sel}>${escapeHtml(nm)} · ${escapeHtml(fn)}</option>`;
  }).join('');
}

export function addStage(d = null) {
  const engines = getEngines();
  const firstCfg = engines[0]?.cfg_filename || '';
  d = d || {
    wet_mass_t: '', dry_mass_t: '',
    engines: { cfg: firstCfg, count: 1 },
    engine_groups: [{ cfg: firstCfg, count: 1 }],
    multi_ignition: false,
    parallel: false,
  };
  const idx = $('ed-stages').children.length;
  const div = createEl('div', { class: 'sr' });
  const preservedGroups = Array.isArray(d.engine_groups) && d.engine_groups.length
    ? d.engine_groups
    : (d.engines?.cfg ? [d.engines] : []);
  div.dataset.engineGroups = JSON.stringify(preservedGroups);
  div.innerHTML =
    `<button class="rmv" data-ed-action="remove">×</button>` +
    `<div class="stg-label" style="font-size:.72rem;font-weight:700;color:var(--accent);margin-bottom:4px">第 ${idx + 1} 级</div>` +
    `<div class="fr"><div><label>湿质量 (t)</label><input value="${d.wet_mass_t}" data-f="wet" type="number" step="0.001" min="0"></div>` +
    `<div><label>干质量 (t)</label><input value="${d.dry_mass_t}" data-f="dry" type="number" step="0.001" min="0"></div></div>` +
    `<div class="fr"><div><label>引擎</label><select data-f="cfg">${engineOptions(d.engines?.cfg)}</select></div>` +
    `<div><label>数量</label><input value="${d.engines?.count ?? 1}" data-f="count" type="number" min="1"></div></div>` +
    `<div class="fr" style="margin-top:4px;gap:12px">` +
    `<label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:.72rem"><input type="checkbox" data-f="multi_ignition" ${d.multi_ignition ? 'checked' : ''}> 多点点火</label>` +
    `<label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:.72rem"><input type="checkbox" data-f="parallel" data-ed-action="renumber" ${d.parallel ? 'checked' : ''}> 助推器（并联点火）</label>` +
    `</div>`;
  $('ed-stages').appendChild(div);
  renumberStages();
}

export function addCal(d = null) {
  d = d || { payload_t: '', orbit_alt_km: '220', dv_rem_m_s: '' };
  const div = createEl('div', { class: 'sr' });
  div.innerHTML =
    `<button class="rmv" data-ed-action="remove">×</button>` +
    `<div class="fr"><div><label>载荷 (t)</label><input value="${d.payload_t}" data-f="pl" type="number" step="0.001" min="0"></div>` +
    `<div><label>轨道高度 (km)</label><input value="${d.orbit_alt_km}" data-f="alt" type="number" step="1" min="0"></div>` +
    `<div><label>剩余 dv (m/s)</label><input value="${d.dv_rem_m_s}" data-f="dv" type="number" step="0.1"></div></div>`;
  $('ed-cals').appendChild(div);
}

export function addMission(d = null) {
  d = d || { label: '', c3: '', color: '#888888' };
  const div = createEl('div', { class: 'sr' });
  div.innerHTML =
    `<button class="rmv" data-ed-action="remove">×</button>` +
    `<div class="fr"><div><label>名称</label><input value="${escapeHtml(d.label || '')}" data-f="lbl" placeholder="GTO"></div>` +
    `<div><label>C3</label><input value="${d.c3 != null ? d.c3 : ''}" data-f="c3" type="number" step="0.1" placeholder="8.7"></div>` +
    `<div><label>颜色</label><input value="${d.color || '#888888'}" data-f="clr" type="color" style="width:36px;height:28px;padding:1px"></div></div>`;
  $('ed-missions').appendChild(div);
}

function renumberStages() {
  document.querySelectorAll('#ed-stages .sr').forEach((el, i) => {
    const isP = el.querySelector('[data-f="parallel"]')?.checked;
    const hd = el.querySelector('.stg-label');
    if (hd) {
      hd.innerHTML = isP
        ? '<span style="color:var(--orange)">🚀 助推器 (并联)</span>'
        : `第 ${i + 1} 级`;
    }
  });
}

function val(el, f) {
  const inp = el.querySelector(`[data-f="${f}"]`);
  return inp ? inp.value : '';
}
function num(el, f) {
  const raw = val(el, f).trim();
  if (!raw) return Number.NaN;
  const v = Number(raw);
  return Number.isFinite(v) ? v : Number.NaN;
}
function int(el, f) {
  const v = parseInt(val(el, f), 10);
  return Number.isNaN(v) ? 1 : Math.max(1, v);
}
function bool(el, f) {
  return !!el.querySelector(`[data-f="${f}"]`)?.checked;
}

function validateClient(cfg) {
  const errs = [];
  const name = cfg.rocket?.name?.trim();
  if (!name) errs.push('请输入火箭名称');
  if (!cfg.stages?.length) errs.push('至少需要 1 个级段');
  cfg.stages.forEach((s, i) => {
    const wetValid = Number.isFinite(s.wet_mass_t) && s.wet_mass_t > 0;
    const dryValid = Number.isFinite(s.dry_mass_t) && s.dry_mass_t > 0;
    if (!wetValid) errs.push(`级段 ${i + 1}: 湿质量必须 > 0`);
    if (!dryValid) errs.push(`级段 ${i + 1}: 干质量必须 > 0`);
    if (wetValid && dryValid && s.dry_mass_t >= s.wet_mass_t) {
      errs.push(`级段 ${i + 1}: 干质量必须 < 湿质量`);
    }
    if (!s.engines?.cfg) errs.push(`级段 ${i + 1}: 请选择引擎`);
  });
  if (!cfg.calibration?.length || cfg.calibration.length < 3) errs.push('至少需要 3 个校准点');
  const distinctPayloads = new Set();
  cfg.calibration.forEach((c, i) => {
    if (!Number.isFinite(c.payload_t) || c.payload_t <= 0) {
      errs.push(`校准点 ${i + 1}: 载荷必须 > 0`);
    } else {
      distinctPayloads.add(c.payload_t);
    }
    if (!Number.isFinite(c.orbit_alt_km) || c.orbit_alt_km <= 0) {
      errs.push(`校准点 ${i + 1}: 轨道高度必须 > 0`);
    }
    if (!Number.isFinite(c.dv_rem_m_s) || c.dv_rem_m_s < 0) {
      errs.push(`校准点 ${i + 1}: 剩余 dv 必须 >= 0`);
    }
  });
  if (cfg.calibration?.length >= 3 && distinctPayloads.size < 3) {
    errs.push('校准点需要 3 个不同的载荷值');
  }
  return errs;
}

async function save(onSaved) {
  const name = $('ed-name').value.trim();
  const stages = [];
  const calibration = [];
  const missions = {};

  document.querySelectorAll('#ed-stages .sr').forEach((el) => {
    let preservedGroups = [];
    try { preservedGroups = JSON.parse(el.dataset.engineGroups || '[]'); } catch { preservedGroups = []; }
    const primary = { cfg: val(el, 'cfg'), count: int(el, 'count') };
    const engineGroups = preservedGroups.length
      ? [primary, ...preservedGroups.slice(1)]
      : [primary];
    stages.push({
      wet_mass_t: num(el, 'wet'),
      dry_mass_t: num(el, 'dry'),
      engines: primary,
      engine_groups: engineGroups,
      multi_ignition: bool(el, 'multi_ignition'),
      parallel: bool(el, 'parallel'),
    });
  });

  document.querySelectorAll('#ed-cals .sr').forEach((el) => {
    calibration.push({
      payload_t: num(el, 'pl'),
      orbit_alt_km: num(el, 'alt'),
      dv_rem_m_s: num(el, 'dv'),
    });
  });

  document.querySelectorAll('#ed-missions .sr').forEach((el) => {
    const l = val(el, 'lbl').trim();
    if (!l) return;
    const c = num(el, 'c3');
    const co = val(el, 'clr') || '#888888';
    missions[l.toLowerCase().replace(/\s+/g, '_')] = { label: l, c3: c, color: co };
  });

  const cfg = { rocket: { name }, stages, calibration, reference_missions: missions };
  const errs = validateClient(cfg);
  if (errs.length) {
    $('ed-err').textContent = errs.join('; ');
    return;
  }

  const slug = getEditingSlug();

  try {
    const r = slug
      ? await Api.Rockets.update(slug, cfg)
      : await Api.Rockets.create(cfg);
    close();
    toast(`已保存: ${name}`, 'ok');
    if (onSaved) onSaved(r.slug || slug, r.result);
  } catch (e) {
    $('ed-err').textContent = e.message;
  }
}
