/**
 * Shared utilities for the C3-Mass dashboard.
 */

export const $ = (id) => document.getElementById(id);
export const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

export function escapeHtml(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

export function createEl(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'data') Object.entries(v).forEach(([dk, dv]) => el.dataset[dk] = dv);
    else el.setAttribute(k, v);
  });
  children.forEach((c) => el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c));
  return el;
}

export function formatNumber(n, digits = 3) {
  if (n == null || Number.isNaN(n)) return '--';
  const v = Number(n);
  if (Math.abs(v) >= 1000) return v.toFixed(Math.max(0, digits - 3));
  if (Math.abs(v) >= 100) return v.toFixed(Math.max(0, digits - 2));
  if (Math.abs(v) >= 10) return v.toFixed(Math.max(0, digits - 1));
  return v.toFixed(digits);
}

export function toast(message, type = 'info', duration = 2500) {
  const el = $('toast');
  if (!el) return;
  el.textContent = message;
  el.className = `toast toast-${type} show`;
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), duration);
}

export async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body != null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let err = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      err = data.error || err;
    } catch { /* ignore */ }
    throw new Error(err);
  }
  return res.json();
}

export function debounce(fn, wait = 300) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

export function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

const NAMED_COLORS = {
  black: '#000000', white: '#ffffff', red: '#ff0000', green: '#008000', blue: '#0000ff',
  orange: '#ffa500', purple: '#800080', magenta: '#ff00ff', cyan: '#00ffff', yellow: '#ffff00',
  darkred: '#8b0000', sienna: '#a0522d', teal: '#008080', navy: '#000080', olive: '#808000',
  gray: '#808080', grey: '#808080', darkblue: '#00008b', darkgreen: '#006400', darkorange: '#ff8c00',
};

export function toRgba(color, alpha) {
  let hex = color?.toLowerCase().trim();
  if (!hex) return `rgba(0,0,0,${alpha})`;
  if (NAMED_COLORS[hex]) hex = NAMED_COLORS[hex];
  // rgb/rgba(...)
  const rgbMatch = hex.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/);
  if (rgbMatch) {
    return `rgba(${rgbMatch[1]},${rgbMatch[2]},${rgbMatch[3]},${alpha})`;
  }
  if (hex.startsWith('#')) {
    const h = hex.slice(1);
    const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
    const bigint = parseInt(full, 16);
    if (!Number.isNaN(bigint)) {
      const r = (bigint >> 16) & 255;
      const g = (bigint >> 8) & 255;
      const b = bigint & 255;
      return `rgba(${r},${g},${b},${alpha})`;
    }
  }
  // Fallback for CSS color() / hsl() etc.
  return `rgba(0,0,0,${alpha})`;
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
