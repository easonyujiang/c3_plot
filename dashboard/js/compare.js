/**
 * Rocket comparison sidebar widget.
 */
import { $, createEl, escapeHtml, toast } from './utils.js?v=2026072706';
import { getCompareSet, clearCompare, toggleCompare } from './state-v2.js';
import * as Api from './api.js?v=2026073103';
import * as Charts from './charts.js';

export function renderComparePanel() {
  const set = getCompareSet();
  const sec = $('cmp-sec');
  const list = $('cmp-list');
  const count = $('cmp-count');

  if (!set.size) {
    sec.style.display = 'none';
    return;
  }
  sec.style.display = '';
  count.textContent = `(${set.size})`;

  list.innerHTML = '';
  set.forEach((slug) => {
    list.appendChild(createEl('div', { class: 'sb-item', style: 'font-size:.7rem;justify-content:space-between' }, [
      `📊 ${escapeHtml(slug)}`,
      createEl('span', { class: 'rdel', style: 'opacity:.5', text: '×', title: '移除', onClick: () => { toggleCompare(slug); renderComparePanel(); } }),
    ]));
  });

  if (set.size >= 2) {
    list.appendChild(createEl('button', { class: 'sb-btn', style: 'margin:4px 14px', text: '▶ 开始对比', onClick: runCompare }));
  }
}

export function clearComparePanel() {
  clearCompare();
  renderComparePanel();
}

async function runCompare() {
  const slugs = Array.from(getCompareSet());
  if (slugs.length < 2) return;
  try {
    const r = await Api.CompareApi.get(slugs);
    if (!r.rockets.length) {
      toast('无对比数据', 'err');
      return;
    }
    Charts.drawCompare(r.rockets);
    window.dispatchEvent(new CustomEvent('compare-ran'));
  } catch (e) {
    toast(`对比失败: ${e.message}`, 'err');
  }
}
