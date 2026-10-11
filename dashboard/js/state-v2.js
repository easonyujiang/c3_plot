/**
 * Single application state store.
 * Server data is read directly from the globals emitted before module loading.
 */
import { $ } from './utils.js?v=2026072706';

const state = {
  rockets: Array.isArray(window.__ROCKETS) ? window.__ROCKETS : [],
  engines: Array.isArray(window.__ENGINES) ? window.__ENGINES : [],
  missions: Array.isArray(window.__MISSIONS) ? window.__MISSIONS : [],
  folders: window.__FOLDERS || { rockets: [], folders: [] },
  currentSlug: null,
  currentData: null,
  currentTab: 'overview',
  compareSet: new Set(),
  missionToggles: new Map(),
  editingSlug: null,
  dark: false,
};

const listeners = new Set();
const notify = (key, value) => listeners.forEach((fn) => fn(key, value, state));

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getState() { return state; }
export function getRockets() { return state.rockets; }
export function getEngines() { return state.engines; }
export function getMissions() { return state.missions; }
export function getFolders() { return state.folders; }
export function getCurrentSlug() { return state.currentSlug; }
export function getCurrentData() { return state.currentData; }
export function getCurrentTab() { return state.currentTab; }
export function getCompareSet() { return state.compareSet; }
export function getEditingSlug() { return state.editingSlug; }
export function isDark() { return state.dark; }

export function setRockets(rockets) {
  state.rockets = Array.isArray(rockets) ? rockets : [];
  notify('rockets', state.rockets);
}

export function setFolders(tree) {
  state.folders = tree || { rockets: [], folders: [] };
  notify('folders', state.folders);
}

export function setCurrent(slug, data = null, tab = null) {
  state.currentSlug = slug || null;
  state.currentData = data;
  if (tab) state.currentTab = tab;
  updateHash();
  notify('current', { slug: state.currentSlug, data, tab: state.currentTab });
}

export function clearCurrent() {
  state.currentSlug = null;
  state.currentData = null;
  state.currentTab = 'overview';
  state.missionToggles.clear();
  updateHash();
  notify('current', { slug: null, data: null, tab: 'overview' });
}

export function setCurrentTab(tab) {
  if (!tab || state.currentTab === tab) return;
  state.currentTab = tab;
  updateHash();
  notify('tab', tab);
}

export function getMissionToggle(label) {
  return state.missionToggles.get(label) !== false;
}

export function setMissionToggle(label, on) {
  state.missionToggles.set(label, Boolean(on));
  notify('missionToggle', { label, on: Boolean(on) });
}

export function resetMissionToggles(labels, value = true) {
  state.missionToggles.clear();
  labels.forEach((label) => state.missionToggles.set(label, Boolean(value)));
  notify('missionTogglesReset', { labels, value: Boolean(value) });
}

export function toggleCompare(slug) {
  if (state.compareSet.has(slug)) state.compareSet.delete(slug);
  else state.compareSet.add(slug);
  notify('compare', state.compareSet);
}

export function clearCompare() {
  state.compareSet.clear();
  notify('compare', state.compareSet);
}

export function setEditingSlug(slug) {
  state.editingSlug = slug || null;
  notify('editingSlug', state.editingSlug);
}

export function setDark(dark) {
  state.dark = Boolean(dark);
  notify('dark', state.dark);
}

function updateHash() {
  const params = new URLSearchParams();
  if (state.currentSlug) params.set('rocket', state.currentSlug);
  if (state.currentTab !== 'overview') params.set('tab', state.currentTab);
  const nextHash = params.toString();
  history.replaceState(null, '', `${window.location.pathname}${nextHash ? `#${nextHash}` : ''}`);
}

export function readHash() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  return {
    rocket: params.get('rocket') || null,
    tab: params.get('tab') || 'overview',
  };
}

export function setLoading(id, loading) {
  const element = $(id);
  if (!element) return;
  element.classList.toggle('loading', loading);
  element.disabled = loading;
}
