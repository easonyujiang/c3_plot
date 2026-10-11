/**
 * Sidebar folder / rocket tree.
 */
import { $, createEl, escapeHtml, api } from './utils.js?v=2026072706';
import { getFolders, getRockets, getCurrentSlug, setFolders } from './state-v2.js';

const collapsed = new Set();
let dragPayload = null;

export function renderTree() {
  const root = getFolders();
  const container = $('nav-tree');
  if (!container) return;

  const frag = document.createDocumentFragment();
  const rootRockets = root.rockets?.length ? root.rockets : getRockets();
  rootRockets.forEach((r) => frag.appendChild(rocketItem(r, 16)));
  (root.folders || []).forEach((f) => frag.appendChild(folderNode(f, 0)));

  if (!frag.childNodes.length) {
    frag.appendChild(createEl('div', {
      text: '暂无火箭，点击 +🚀 新建',
      style: 'padding:4px 14px;font-size:.7rem;color:var(--muted)',
    }));
  }

  container.innerHTML = '';
  container.appendChild(frag);
}

function folderNode(f, depth) {
  const isCollapsed = collapsed.has(f.id);
  const indent = 16 + depth * 14;
  const row = createEl('div', { class: 'f-row' });

  const header = createEl('div', {
    class: 'f-hd',
    style: `padding-left:${indent}px`,
    draggable: true,
    'data-folder-id': f.id,
  });

  header.appendChild(createEl('span', {
    class: 'arw',
    text: isCollapsed ? '▶' : '▼',
    'data-action': 'toggle-folder',
    'data-id': f.id,
  }));
  header.appendChild(createEl('span', {
    text: isCollapsed ? '📁' : '📂',
    'data-action': 'toggle-folder',
    'data-id': f.id,
  }));
  header.appendChild(createEl('span', {
    class: 'fn',
    text: f.name,
    'data-action': 'toggle-folder',
    'data-id': f.id,
  }));
  header.appendChild(createEl('span', { class: 'fc', text: f.rocket_count || 0 }));

  const actions = createEl('span', { class: 'f-act' });
  actions.appendChild(createEl('button', { text: '+', title: '新建子文件夹', 'data-action': 'new-folder', 'data-id': f.id }));
  actions.appendChild(createEl('button', { text: '✎', title: '重命名', 'data-action': 'rename-folder', 'data-id': f.id, 'data-name': f.name }));
  actions.appendChild(createEl('button', { class: 'fdel', text: '×', title: '删除', 'data-action': 'del-folder', 'data-id': f.id }));
  header.appendChild(actions);

  row.appendChild(header);

  if (!isCollapsed) {
    const sub = createEl('div', { class: 'f-sub' });
    (f.rockets || []).forEach((r) => sub.appendChild(rocketItem(r, indent + 16)));
    (f.children || []).forEach((c) => sub.appendChild(folderNode(c, depth + 1)));
    row.appendChild(sub);
  }

  bindDrag(header, { type: 'folder', id: f.id });
  bindDrop(header, f.id);
  return row;
}

function rocketItem(r, indent) {
  const leo = r.m_pl_max != null ? `${r.m_pl_max.toFixed(1)}t` : '--';
  const on = getCurrentSlug() === r.slug;
  const div = createEl('div', {
    class: `r-item${on ? ' on' : ''}`,
    style: `padding-left:${indent}px`,
    draggable: true,
    'data-slug': r.slug,
  });
  div.appendChild(createEl('span', { text: '🚀 ' }));
  div.appendChild(createEl('span', { text: r.name }));
  div.appendChild(createEl('span', { class: 'rleo', text: leo }));
  div.appendChild(createEl('span', { class: 'rdel', text: '×', title: '删除火箭', 'data-action': 'del-rocket', 'data-slug': r.slug }));

  div.addEventListener('click', (e) => {
    if (e.target.closest('[data-action]')) return;
    window.dispatchEvent(new CustomEvent('rocket-select', { detail: r.slug }));
  });

  bindDrag(div, { type: 'rocket', slug: r.slug });
  bindDrop(div, null);
  return div;
}

function bindDrag(el, payload) {
  el.addEventListener('dragstart', (e) => {
    dragPayload = payload;
    e.dataTransfer.effectAllowed = 'move';
    el.classList.add('dragging');
  });
  el.addEventListener('dragend', () => {
    dragPayload = null;
    el.classList.remove('dragging');
  });
}

function bindDrop(el, folderId) {
  el.addEventListener('dragover', (e) => {
    e.preventDefault();
    el.classList.add('drag-over');
  });
  el.addEventListener('dragleave', () => el.classList.remove('drag-over'));
  el.addEventListener('drop', async (e) => {
    e.preventDefault();
    el.classList.remove('drag-over');
    if (!dragPayload) return;
    if (dragPayload.type === 'rocket') {
      await moveRocket(dragPayload.slug, folderId);
    }
    // Folder-to-folder reorder/move could be added here
  });
}

export async function reloadTree() {
  const data = await api('GET', '/api/folders');
  setFolders(data.tree);
  return data;
}

export async function newFolder(parentId = null) {
  const name = prompt('文件夹名称:');
  if (!name || !name.trim()) return;
  await api('POST', '/api/folders', { name: name.trim(), parent_id: parentId });
  await reloadTree();
}

export async function renameFolder(id, curName) {
  const name = prompt('新名称:', curName);
  if (!name || !name.trim()) return;
  await api('PUT', `/api/folders/${id}`, { name: name.trim() });
  await reloadTree();
}

export async function deleteFolder(id) {
  if (!confirm('删除文件夹？内部火箭移回根目录。')) return;
  await api('DELETE', `/api/folders/${id}`);
  await reloadTree();
}

export async function moveRocket(slug, folderId) {
  await api('POST', `/api/rockets/${encodeURIComponent(slug)}/move`, { folder_id: folderId });
  await reloadTree();
}

export function toggleFolder(id) {
  if (collapsed.has(id)) collapsed.delete(id);
  else collapsed.add(id);
  renderTree();
}

export function initTreeEvents() {
  const tree = $('nav-tree');
  if (!tree) return;
  tree.addEventListener('click', (e) => {
    const target = e.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    const id = Number(target.dataset.id);
    if (action === 'toggle-folder') {
      toggleFolder(id);
    } else if (action === 'new-folder') {
      newFolder(id || null);
    } else if (action === 'rename-folder') {
      renameFolder(id, target.dataset.name);
    } else if (action === 'del-folder') {
      deleteFolder(id);
    } else if (action === 'del-rocket') {
      e.stopPropagation();
      window.dispatchEvent(new CustomEvent('rocket-delete', { detail: target.dataset.slug }));
    }
    if (action !== 'del-rocket') {
      e.stopPropagation();
    }
  });
}
