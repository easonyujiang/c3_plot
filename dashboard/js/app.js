/**
 * C3-Mass Dashboard — Main application orchestration.
 */
import { $, toast } from './utils.js?v=2026072706';
import {
  getRockets, setRockets, getEngines, getFolders, setFolders, getCurrentSlug,
  getCurrentData, getCurrentTab, setCurrent, clearCurrent, setCurrentTab,
  getCompareSet, toggleCompare, setMissionToggle,
  resetMissionToggles, readHash, setDark, isDark,
} from './state-v2.js';
import * as Api from './api.js?v=2026073103';
import { renderTree, initTreeEvents, reloadTree, newFolder } from './tree.js?v=2026072706';
import { renderOverview, renderMissionTable, renderToggles, renderRocketList, setViewTitle, setActions, showTabs, setTab, updateToggleVisual } from './overview-v2.js?v=2026072706';
import * as Charts from './charts.js?v=2026072706';
import { initEditorEvents, open as openEditor, openDraft as openEditorDraft, close as closeEditor } from './editor.js?v=2026100901';
import { initQueryUI, open as openQuery, close as closeQuery } from './query.js';
import { initImportUI, open as openImport, close as closeImport } from './import.js?v=2026100901';
import { initPlanner } from './planner.js?v=2026080102';
import { renderComparePanel, clearComparePanel } from './compare.js';

// Re-export legacy globals for console convenience and any inline handlers
window.App = {
  showHome, showPlanner, selectRocket, deleteRocket, recompute,
  newFolder, openEditor, openQuery, openImport, switchTab, compareToggle,
};

export function init() {
  window.__C3_APP_READY = false;
  initShell();
  // Initial render from server data
  renderTree();
  renderComparePanel();
  updateFooter();

  // Event listeners
  initTreeEvents();
  initEditorEvents(handleSaved);
  initQueryUI(selectRocket, compareToggle);
  initImportUI(handleImported);
  initPlanner((slug) => selectRocket(slug));
  const fairingEnabled = $('chart-fairing-enabled');
  const fairingMass = $('chart-fairing-mass');
  const redrawFairing = () => {
    if (fairingMass) fairingMass.disabled = !fairingEnabled?.checked;
    const data = getCurrentData();
    Charts.redrawFairing(data);
  };
  fairingEnabled?.addEventListener('change', redrawFairing);
  fairingMass?.addEventListener('input', redrawFairing);

  // Custom events
  window.addEventListener('rocket-select', (e) => selectRocket(e.detail));
  window.addEventListener('rocket-delete', (e) => deleteRocket(e.detail));
  window.addEventListener('compare-ran', () => { showTabs(true); switchTab('charts'); });
  window.addEventListener('vab-draft-ready', (event) => {
    closeImport();
    openEditorDraft(event.detail?.draft, event.detail?.warnings || [], event.detail?.structured_warnings || []);
  });

  // Global delegated event listeners for static and dynamic UI elements
  document.addEventListener('click', (e) => {
    // Modal close buttons
    const closeBtn = e.target.closest('[data-modal-close]');
    if (closeBtn) {
      const modalMap = {
        'mo-editor': closeEditor,
        'mo-query': closeQuery,
        'mo-import': closeImport,
      };
      const modalId = closeBtn.closest('.mo')?.id;
      if (modalMap[modalId]) modalMap[modalId]();
      return;
    }

    // Sidebar navigation
    const nav = e.target.closest('[data-nav]');
    if (nav && !e.defaultPrevented) {
      e.preventDefault();
      const target = nav.dataset.nav;
      if (target === 'home') showHome();
      else if (target === 'planner') showPlanner();
      else if (target === 'query') openQuery();
      return;
    }

    // Sidebar / toolbar actions
    const action = e.target.closest('[data-action]');
    if (action && !e.defaultPrevented) {
      e.preventDefault();
      const a = action.dataset.action;
      if (a === 'new-folder') newFolder();
      else if (a === 'new-rocket') openEditor();
      else if (a === 'import') openImport();
      else if (a === 'clear-compare') clearComparePanel();
      else if (a === 'toggle-theme') toggleTheme();
      else if (a === 'open-sidebar') document.body.classList.add('sidebar-open');
      else if (a === 'close-sidebar') document.body.classList.remove('sidebar-open');
      return;
    }

    // Tabs
    const tab = e.target.closest('.cp-tab');
    if (tab) {
      e.preventDefault();
      switchTab(tab.dataset.t);
    }
  });

  // Keyboard support for tabs (Enter / Space)
  document.addEventListener('keydown', (e) => {
    const tab = e.target.closest('.cp-tab');
    if (tab && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      switchTab(tab.dataset.t);
    }
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeEditor();
      closeQuery();
      closeImport();
    }
    if (e.ctrlKey && e.key === 'n') {
      e.preventDefault();
      openEditor();
    }
  });

  // Hash routing on load
  const hash = readHash();
  if (hash.rocket && hash.rocket !== getCurrentSlug()) {
    selectRocket(hash.rocket, hash.tab);
  } else {
    showHome();
  }

  // Hash change (browser back/forward)
  window.addEventListener('hashchange', () => {
    const h = readHash();
    if (h.rocket) selectRocket(h.rocket, h.tab);
    else showHome();
  });

  // Dark mode listener for charts
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
    setDark(e.matches);
    Charts.relayout();
  });
  window.__C3_APP_READY = true;
}

function setNavActive(view) {
  document.querySelectorAll('[data-nav]').forEach((el) => {
    const active = el.dataset.nav === view;
    el.classList.toggle('on', active);
    el.setAttribute('aria-current', active ? 'page' : 'false');
  });
}

export function showHome() {
  if (getCurrentSlug()) clearCurrent();
  $('view-home').style.display = '';
  $('view-planner').style.display = 'none';
  setNavActive('home');
  renderTree();
  renderRocketList(selectRocket, compareToggle);
}

function initShell() {
  const savedTheme = localStorage.getItem('c3-theme');
  const dark = savedTheme ? savedTheme === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  applyTheme(dark);
}

function toggleTheme() {
  applyTheme(!isDark());
}

function applyTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  localStorage.setItem('c3-theme', dark ? 'dark' : 'light');
  setDark(dark);
  Charts.relayout();
}

export function showPlanner() {
  $('view-home').style.display = 'none';
  $('view-planner').style.display = '';
  setNavActive('planner');
}

const VALID_TABS = ['overview', 'charts', 'missions', 'data'];

export function switchTab(tab) {
  if (!VALID_TABS.includes(tab) || !document.getElementById(`tab-${tab}`)) return;
  if (getCurrentTab() === tab && document.querySelector(`#tab-${tab}.show`)) return;
  setCurrentTab(tab);
  setTab(tab);
  if (tab === 'charts') {
    const data = getCurrentData();
    if (data) { Charts.drawC3Mass(data); Charts.drawDvMass(data); }
  }
}

export async function selectRocket(slug, tab = null) {
  if (!slug) { showHome(); return; }
  $('view-home').style.display = '';
  $('view-planner').style.display = 'none';
  const targetTab = VALID_TABS.includes(tab) ? tab : 'overview';
  setCurrent(slug, null, targetTab);
  renderTree();
  setNavActive('home');
  setViewTitle(slug);
  $('cp-act').innerHTML = '<span class="ld-inline"><span class="spin"></span>加载中...</span>';
  showTabs(true);
  switchTab(targetTab);

  try {
    const data = await Api.Data.get(slug);
    // Inject default reference missions if backend didn't provide any
    if (!data.missions || Object.keys(data.missions).length === 0) {
      data.missions = {
        leo:    { label: 'LEO',          c3: -56.0,  dv: 0,       payload: data.m_pl_max, color: '#27ae60' },
        gto:    { label: 'GTO',          c3: -16.35, dv: 2450.7,  payload: null, color: 'orange' },
        tli:    { label: 'TLI',          c3: -2.01,  dv: 3129.6,  payload: null, color: 'purple' },
        escape: { label: 'Earth Escape', c3: 0,      dv: 3221.2,  payload: null, color: 'darkred' },
        mars:   { label: 'Mars',         c3: 8.7,    dv: 3609.9,  payload: null, color: 'sienna' },
      };
    }
    setCurrent(slug, data, targetTab);
    resetMissionToggles(Object.values(data.missions).map((m) => m.label), true);
    renderRocketDetail(data);
    renderTree(); // highlight selected
  } catch (e) {
    renderTree();
    showTabs(false);
    $('overview-grid').innerHTML = `<div class="empt"><span class="ic">⚠</span><p>加载失败：${e.message}</p></div>`;
    toast('加载火箭失败', 'err');
  }
}

function renderRocketDetail(data) {
  document.body.classList.add('has-rocket');
  setViewTitle(data.name || getCurrentSlug());
  $('cp-tabs').style.display = '';
  renderOverview(data);
  renderMissionTable(data);
  renderToggles(data, handleToggle, handleToggleAll);
  $('raw-json').textContent = JSON.stringify(data, null, 2);

  const slug = getCurrentSlug();
  const actions = [
    createBtn('↻ 重新计算', 'btn btn-s btn-a', () => recompute(slug)),
    createBtn('CSV', 'btn btn-s', () => window.open(Api.ExportApi.csvUrl(slug))),
    createBtn('JSON', 'btn btn-s', () => window.open(Api.ExportApi.jsonUrl(slug))),
    createBtn('📊 对比', 'btn btn-s', () => compareToggle(slug)),
    createBtn('✏️ 编辑', 'btn btn-s', () => openEditor(slug)),
  ];
  setActions(actions);

  if (getCurrentTab() === 'charts') {
    Charts.drawC3Mass(data);
    Charts.drawDvMass(data);
  }
}

function createBtn(text, cls, onClick) {
  const b = document.createElement('button');
  b.className = cls;
  b.textContent = text;
  b.addEventListener('click', onClick);
  return b;
}

export async function deleteRocket(slug) {
  if (!confirm(`确定删除 "${slug}"？此操作不可撤销。`)) return;
  try {
    await Api.Rockets.delete(slug);
    const rk = await Api.Rockets.list();
    setRockets(rk);
    if (getCurrentSlug() === slug) {
      clearCurrent();
      showHome();
    }
    await reloadTree();
    updateFooter();
    toast('已删除', 'ok');
  } catch (e) {
    toast(`删除失败：${e.message}`, 'err');
  }
}

export async function recompute(slug) {
  if (!slug) return;
  const btn = document.querySelector('#cp-act .btn-a');
  if (btn) { btn.disabled = true; btn.textContent = '计算中...'; }
  try {
    await Api.Data.recompute(slug);
    await selectRocket(slug, getCurrentTab());
    toast('计算完成', 'ok');
  } catch (e) {
    toast(`计算失败：${e.message}`, 'err');
  }
}

export function compareToggle(slug) {
  toggleCompare(slug);
  renderComparePanel();
}

function handleToggle(label, on) {
  setMissionToggle(label, on);
  updateToggleVisual(label, on);
  const data = getCurrentData();
  if (data) { Charts.drawC3Mass(data); Charts.drawDvMass(data); }
}

function handleToggleAll(on) {
  const data = getCurrentData();
  if (!data || !data.missions) return;
  const labels = Object.values(data.missions).map((mission) => mission.label);
  resetMissionToggles(labels, on);
  labels.forEach((label) => updateToggleVisual(label, on));
  Charts.drawC3Mass(data);
  Charts.drawDvMass(data);
}

async function handleSaved(slug, result) {
  const rk = await Api.Rockets.list();
  setRockets(rk);
  await reloadTree();
  updateFooter();
  if (slug) selectRocket(slug, 'overview');
}

async function handleImported() {
  const rk = await Api.Rockets.list();
  setRockets(rk);
  await reloadTree();
  updateFooter();
}

function updateFooter() {
  const rockets = getRockets();
  const rocketStat = $('f-rocket');
  const engineStat = $('f-engine');
  const bestStat = $('f-best');
  if (rocketStat) rocketStat.textContent = rockets.length;
  if (engineStat) engineStat.textContent = getEngines().length;
  if (!bestStat) return;
  if (rockets.length) {
    const best = rockets.reduce((a, b) => (b.m_pl_max || 0) > (a.m_pl_max || 0) ? b : a, rockets[0]);
    bestStat.textContent = `${(best.m_pl_max || 0).toFixed(1)} t`;
  } else {
    bestStat.textContent = '--';
  }
}
