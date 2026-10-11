/**
 * Central reactive state store + URL hash synchronization.
 */
import { $ } from './utils.js';

const initial = window.__INITIAL_DATA__ || {};

const state = {
  rockets: initial.rockets || [],
  engines: initial.engines || [],
  missions: initial.missions || [],
  folders: initial.folders || { rockets: [], folders: [] },
  currentSlug: null,
  currentData: null,
  currentTab: 'overview',
  compareSet: new Set(),
  missionToggles: {},
  editingSlug: null,
  dark: window.matchMedia('(prefers-color-scheme: dark)').matches,
};

const listeners = new Set();

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notify(key, value) {
  listeners.forEach((fn) => fn(key, value, state));
}

export function getState() {
  // Return a shallow read-only snapshot for debug
  return state;
}

export function getRockets() { return state.rockets; }
export function setRockets(list) {
  state.rockets = list;
  notify('rockets', list);
}

export function getEngines() { return state.engines; }
export function getMissions() { return state.missions; }

export function getFolders() { return state.folders; }
export function setFolders(tree) {
  state.folders = tree;
  notify('folders', tree);
}

export function getCurrentSlug() { return state.currentSlug; }
export function getCurrentData() { return state.currentData; }

export function setCurrent(slug, data = null, tab = null) {
  state.currentSlug = slug;
  state.currentData = data;
  if (tab) state.currentTab = tab;
  state.missionToggles = {};
  updateHash();
  notify('current', { slug, data, tab: state.currentTab });
}

export function clearCurrent() {
  state.currentSlug = null;
  state.currentData = null;
  state.currentTab = 'overview';
  state.missionToggles = {};
  updateHash();
  notify('current', { slug: null, data: null, tab: 'overview' });
}

export function getCurrentTab() { return state.currentTab; }
export function setCurrentTab(tab) {
  if (state.currentTab === tab) return;
  state.currentTab = tab;
  updateHash();
  notify('tab', tab);
}

export function getMissionToggle(label) {
  return state.missionToggles[label] !== false;
}
export function setMissionToggle(label, on) {
  state.missionToggles[label] = on;
  notify('missionToggle', { label, on });
}
export function resetMissionToggles(labels, value = true) {
  state.missionToggles = {};
  labels.forEach((l) => { state.missionToggles[l] = value; });
  notify('missionTogglesReset', { labels, value });
}

export function getCompareSet() { return state.compareSet; }
export function toggleCompare(slug) {
  if (state.compareSet.has(slug)) state.compareSet.delete(slug);
  else state.compareSet.add(slug);
  notify('compare', state.compareSet);
}
export function clearCompare() {
  state.compareSet.clear();
  notify('compare', state.compareSet);
}

export function getEditingSlug() { return state.editingSlug; }
export function setEditingSlug(slug) {
  state.editingSlug = slug;
  notify('editingSlug', slug);
}

export function isDark() { return state.dark; }
export function setDark(dark) {
  state.dark = dark;
  notify('dark', dark);
}

function updateHash() {
  const params = new URLSearchParams();
  if (state.currentSlug) params.set('rocket', state.currentSlug);
  if (state.currentTab && state.currentTab !== 'overview') params.set('tab', state.currentTab);
  const hash = params.toString();
  const newHash = hash ? `#${hash}` : '';
  if (window.location.hash !== newHash) {
    // Replace state to avoid polluting history on every tab switch
    history.replaceState(null, '', `${window.location.pathname}${newHash}`);
  }
}

export function readHash() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  return {
    rocket: params.get('rocket') || null,
    tab: params.get('tab') || 'overview',
  };
}

export function setLoading(id, loading) {
  const el = $(id);
  if (!el) return;
  el.classList.toggle('loading', loading);
  el.disabled = loading;
}
