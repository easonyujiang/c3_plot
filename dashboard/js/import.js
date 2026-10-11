/**
 * YAML import modal (file drop / paste).
 */
import { $, createEl, escapeHtml, toast, api, downloadBlob } from './utils.js?v=2026072706';
import * as Api from './api.js?v=2026073103';

let onImportedCallback = null;
let batchPreview = null;
let libraryTimer = null;
let libraryLoading = false;
let libraryVersion = '';

export function initImportUI(onImported) {
  onImportedCallback = onImported;
  const modal = $('mo-import');
  if (!modal) return;

  modal.addEventListener('click', (e) => {
    if (e.target === modal) close();
  });

  const dz = $('im-drop');
  const fileInput = $('im-file');
  if (dz && fileInput) {
    dz.addEventListener('click', () => fileInput.click());
    dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('drag-over'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
    dz.addEventListener('drop', (e) => {
      e.preventDefault();
      dz.classList.remove('drag-over');
      handleFiles(e.dataTransfer.files);
    });
    fileInput.addEventListener('change', () => handleFiles(fileInput.files));
  }
  const folderButton = $('im-folder-btn');
  const folderInput = $('im-folder-file');
  folderButton?.addEventListener('click', (event) => {
    event.stopPropagation();
    folderInput?.click();
  });
  folderInput?.addEventListener('change', () => handleFiles(folderInput.files));
  $('im-batch-confirm')?.addEventListener('click', confirmBatch);
  $('im-library-refresh')?.addEventListener('click', () => refreshLibrary(true));

  $('im-paste-btn')?.addEventListener('click', pasteImport);
  const vabDrop = $('im-vab-drop');
  const vabInput = $('im-vab-file');
  const vabButton = $('im-vab-btn');
  if (vabDrop && vabInput) {
    vabButton?.addEventListener('click', (event) => {
      event.stopPropagation();
      vabInput.click();
    });
    vabDrop.addEventListener('click', (event) => {
      if (event.target === vabInput || event.target.closest('button')) return;
      vabInput.click();
    });
    vabDrop.addEventListener('dragover', (event) => {
      event.preventDefault();
      vabDrop.classList.add('drag-over');
    });
    vabDrop.addEventListener('dragleave', () => vabDrop.classList.remove('drag-over'));
    vabDrop.addEventListener('drop', (event) => {
      event.preventDefault();
      vabDrop.classList.remove('drag-over');
      const craft = Array.from(event.dataTransfer.files).find((file) => file.name.toLowerCase().endsWith('.craft'));
      if (!craft) { toast('请拖入 KSP .craft 文件', 'err'); return; }
      previewVab(craft);
    });
    vabInput.addEventListener('change', () => {
      if (vabInput.files?.[0]) previewVab(vabInput.files[0]);
    });
  }
  $('im-close')?.addEventListener('click', close);
}

async function previewVab(selectedFile = null) {
  const file = selectedFile || $('im-vab-file')?.files?.[0];
  if (!file) { toast('请先选择 .craft 文件', 'err'); return; }
  const fd = new FormData();
  fd.append('file', file);
  const btn = $('im-vab-btn');
  if (btn) { btn.disabled = true; btn.textContent = '解析中...'; }
  try {
    const res = await fetch('/api/import/vab/preview', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) {
      const code = data.error_code ? ` [${data.error_code}]` : '';
      const loc = data.line ? `（第 ${data.line} 行${data.column ? `，第 ${data.column} 列` : ''}）` : '';
      throw new Error(`${data.error || `HTTP ${res.status}`}${code}${loc}`);
    }
    const draft = data.draft;
    $('im-paste').value = draftToYaml(draft);
    const warnings = renderWarnings(draft.structured_warnings || [], draft.warnings || []);
    const note = data.status === 'needs_review'
      ? '<p>该草稿质量未确认，需人工补全后才可导入。</p>'
      : '<p>草稿已放入下方 YAML 编辑区。请补充至少 3 个校准点后导入。</p>';
    $('im-res').innerHTML = `<div class="vab-preview"><strong>已读取 ${draft.source.part_count} 个部件，${draft.stages.length} 个级段</strong>${note}${warnings}</div>`;
    window.dispatchEvent(new CustomEvent('vab-draft-ready', {
      detail: { draft, warnings: editableWarnings(draft), structured_warnings: editableStructured(draft) },
    }));
  } catch (e) {
    $('im-res').innerHTML = `<span style="color:var(--red)">VAB 解析失败: ${escapeHtml(e.message)}</span>`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '选择 .craft 文件'; }
  }
}

const SEVERITY_LABEL = { error: '【错误】', warning: '【警告】', info: '【提示】' };

function renderWarnings(structured, legacy) {
  const items = (structured || []).map((w) => {
    const label = SEVERITY_LABEL[w.severity] || '';
    const key = (w.code === 'mass_not_confirmed' || w.code === 'stage_structure_unconfirmed') ? ' vab-warn-key' : '';
    const impact = w.impact ? `<div class="vab-warn-impact">影响：${escapeHtml(w.impact)}</div>` : '';
    return `<li class="vab-warn vab-warn-${w.severity || 'warning'}${key}"><span class="vab-warn-tag">${label}</span>${escapeHtml(w.message)}${impact}</li>`;
  });
  if (!items.length) {
    (legacy || []).forEach((w) => items.push(`<li class="vab-warn">${escapeHtml(w)}</li>`));
  }
  return items.length
    ? `<ul class="vab-warns">${items.join('')}</ul>`
    : '<span class="ok">未发现解析警告</span>';
}

function editableWarnings(draft) {
  const structured = (draft.structured_warnings || [])
    .filter((w) => w.severity !== 'error')
    .map((w) => w.message);
  if (structured.length) return structured;
  return draft.warnings || [];
}

function editableStructured(draft) {
  const seen = new Set();
  const out = [];
  (draft.structured_warnings || []).forEach((w) => {
    if (w.severity === 'error') return;
    const dedupeKey = `${w.code || ''}:${w.message || ''}`;
    if (seen.has(dedupeKey)) return;
    seen.add(dedupeKey);
    out.push(w);
  });
  return out;
}

function batchNotes(item) {
  if (item.status === 'error') return [item.error || item.message || ''];
  const notes = [];
  (item.structured_warnings || []).forEach((w) => {
    if (w.severity === 'info') return;
    notes.push(`${SEVERITY_LABEL[w.severity] || ''}${w.message}`);
  });
  if (!notes.length) {
    (item.warnings || []).concat(item.errors || []).slice(0, 3).forEach((w) => notes.push(w));
  }
  return notes.slice(0, 3);
}

function yamlScalar(value) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(String(value));
}

function draftToYaml(draft) {
  const lines = [`rocket:`, `  name: ${yamlScalar(draft.rocket?.name || 'VAB Vehicle')}`, 'stages:'];
  (draft.stages || []).forEach((stage) => {
    lines.push(`  - wet_mass_t: ${yamlScalar(stage.wet_mass_t)}`);
    lines.push(`    dry_mass_t: ${yamlScalar(stage.dry_mass_t)}`);
    lines.push('    engines:');
    lines.push(`      cfg: ${yamlScalar(stage.engines?.cfg || '')}`);
    lines.push(`      count: ${yamlScalar(stage.engines?.count || 1)}`);
    lines.push(`    multi_ignition: ${Boolean(stage.multi_ignition)}`);
    lines.push(`    parallel: ${Boolean(stage.parallel)}`);
  });
  lines.push('calibration:');
  lines.push('  # 请补充至少三个不同 payload_t / dv_rem_m_s 校准点');
  lines.push('  - payload_t: 1');
  lines.push('    orbit_alt_km: 220');
  lines.push('    dv_rem_m_s: 0');
  lines.push('reference_missions: {}');
  return lines.join('\n');
}

export function open() {
  $('mo-import')?.classList.add('show');
  $('im-res').innerHTML = '';
  $('im-paste').value = '';
  if ($('im-file')) $('im-file').value = '';
  if ($('im-vab-file')) $('im-vab-file').value = '';
  if ($('im-folder-file')) $('im-folder-file').value = '';
  batchPreview = null;
  libraryVersion = '';
  refreshLibrary(true);
  clearInterval(libraryTimer);
  libraryTimer = setInterval(() => refreshLibrary(false), 5000);
  if ($('im-batch-confirm')) $('im-batch-confirm').disabled = true;
  if ($('im-batch-summary')) $('im-batch-summary').textContent = '选择文件后先预览，系统不会立即覆盖数据库。';
  setTimeout(() => $('im-paste')?.focus(), 50);
}

export function close() {
  clearInterval(libraryTimer);
  libraryTimer = null;
  $('mo-import')?.classList.remove('show');
}

async function refreshLibrary(force) {
  if (libraryLoading || !$('mo-import')?.classList.contains('show')) return;
  libraryLoading = true;
  try {
    const data = await Api.ImportApi.library();
    if (!$('mo-import')?.classList.contains('show')) return;
    const version = JSON.stringify(data);
    if (!force && version === libraryVersion) return;
    libraryVersion = version;
    const status = $('im-library-status');
    status.textContent = !data.available ? (data.warnings?.[0] || '未找到游戏安装')
      : `${data.items.length} 枚火箭 · 保存后自动更新${data.truncated ? ' · 列表达到 500 项上限' : ''}${data.warnings?.length ? ` · ${data.warnings.length} 个文件读取异常` : ''}`;
    status.title = (data.warnings || []).join('\n');
    const list = $('im-library-list');
    list.replaceChildren();
    data.items.forEach((item) => {
      const row = createEl('div', { class: 'saved-craft-row' });
      const text = createEl('div');
      text.appendChild(createEl('strong', { text: item.name }));
      text.appendChild(createEl('div', { class: 'hint', text: `${item.save} · ${item.filename} · ${new Date(item.modified_unix_s * 1000).toLocaleString()}` }));
      const button = createEl('button', { class: 'btn btn-s btn-a', text: '导入', onClick: async () => {
        button.disabled = true;
        try {
          const result = await Api.ImportApi.libraryPreview(item.id, item.sha256);
          close();
          window.dispatchEvent(new CustomEvent('vab-draft-ready', { detail: {
            draft: result.draft, warnings: editableWarnings(result.draft), structured_warnings: editableStructured(result.draft),
          } }));
        } catch (error) { toast(error.message, 'err'); button.disabled = false; }
      } });
      row.append(text, button);
      list.appendChild(row);
    });
    if (data.available && !data.items.length) list.appendChild(createEl('p', { text: '在游戏 VAB 中保存火箭后，会自动出现在这里。' }));
  } catch (error) { $('im-library-status').textContent = `读取失败：${error.message}`; }
  finally { libraryLoading = false; }
}

async function handleFiles(files) {
  const list = Array.from(files).filter((f) => /\.(yaml|yml|craft|loadmeta)$/i.test(f.name));
  if (!list.length) { toast('请选择 YAML 或 .craft 文件', 'err'); return; }
  setLoading(true);
  try {
    batchPreview = await Api.ImportApi.batchPreview(list);
    renderBatchPreview(batchPreview);
  } catch (e) {
    $('im-res').innerHTML = `<span style="color:var(--red)">批量预览失败: ${escapeHtml(e.message)}</span>`;
  }
  setLoading(false);
}

function renderBatchPreview(data) {
  const summary = $('im-batch-summary');
  if (summary) summary.textContent = `共 ${data.count} 个文件 · 可直接导入 ${data.ready} 个 · 待补全 ${data.review} 个 · 重复 ${data.duplicates} 个 · 错误 ${data.errors} 个`;
  const confirm = $('im-batch-confirm');
  if (confirm) confirm.disabled = !data.ready;
  const area = $('im-res');
  area.innerHTML = '';
  const table = createEl('table', { class: 'batch-preview-table' });
  table.appendChild(createEl('thead', {}, [createEl('tr', {}, ['文件', '名称', '状态', '说明', '操作'].map((label) => createEl('th', { text: label })))]));
  const body = createEl('tbody');
  data.items.forEach((item) => {
    const statusMap = { ready: ['可导入', 'ok'], needs_review: ['待补全', 'warn'], duplicate: ['已导入', 'muted'], metadata: ['辅助文件', 'muted'], error: ['错误', 'err'] };
    const [label, tone] = statusMap[item.status] || ['未知', 'muted'];
    const notes = batchNotes(item).join('；');
    const actions = createEl('td');
    if (item.status === 'needs_review' && item.config) {
      actions.appendChild(createEl('button', {
        class: 'btn btn-s', text: '打开草稿', onClick: () => {
          close();
          window.dispatchEvent(new CustomEvent('vab-draft-ready', { detail: { draft: item.config, warnings: editableWarnings(item.config), structured_warnings: editableStructured(item.config) } }));
        },
      }));
    }
    body.appendChild(createEl('tr', {}, [
      createEl('td', { text: item.file || '' }),
      createEl('td', { text: item.name || '—' }),
      createEl('td', {}, [createEl('span', { class: `batch-status ${tone}`, text: label })]),
      createEl('td', { class: 'batch-note', text: notes }),
      actions,
    ]));
  });
  table.appendChild(body);
  area.appendChild(table);
  if (data.warnings && data.warnings.length) {
    const detail = createEl('details', { class: 'batch-warnings' });
    detail.appendChild(createEl('summary', { text: `${data.warnings.length} 条解析警告` }));
    data.warnings.forEach((w) => {
      detail.appendChild(createEl('div', {
        class: `ri ${w.severity === 'error' ? 'err' : 'warn'}`,
        text: `${w.file || ''}: ${w.message}`,
      }));
    });
    area.appendChild(detail);
  }
}

async function confirmBatch() {
  if (!batchPreview) return;
  const items = batchPreview.items.filter((item) => item.status === 'ready');
  if (!items.length) { toast('没有可以直接导入的项目', 'err'); return; }
  setLoading(true);
  try {
    const result = await Api.ImportApi.batchConfirm(items, $('im-batch-updates')?.checked !== false);
    renderResults(result);
    if (result.imported && onImportedCallback) onImportedCallback();
    if ($('im-batch-confirm')) $('im-batch-confirm').disabled = true;
  } catch (e) {
    $('im-res').innerHTML = `<span style="color:var(--red)">批量导入失败: ${escapeHtml(e.message)}</span>`;
  }
  setLoading(false);
}

async function pasteImport() {
  const text = $('im-paste').value.trim();
  if (!text) { toast('请粘贴 YAML', 'err'); return; }
  setLoading(true);
  try {
    const r = await Api.ImportApi.paste(text);
    renderResults({ imported: r.action ? 1 : 0, errors: 0, results: [{ file: 'pasted.yaml', name: r.name, slug: r.slug, action: r.action }], error_details: [] });
    if (r.action && onImportedCallback) onImportedCallback();
  } catch (e) {
    $('im-res').innerHTML = `<span style="color:var(--red)">导入失败: ${escapeHtml(e.message)}</span>`;
  }
  setLoading(false);
}

function renderResults(r) {
  const el = $('im-res');
  el.innerHTML = '';
  el.appendChild(createEl('div', { style: 'margin-bottom:6px;font-size:.8rem', text: `成功 ${r.imported} 个，失败 ${r.errors} 个` }));
  if (r.results.length) {
    const table = createEl('table');
    const tbody = createEl('tbody');
    r.results.forEach((x) => {
      tbody.appendChild(createEl('tr', {}, [
        createEl('td', { text: x.file }),
        createEl('td', { text: x.name }),
        createEl('td', { text: x.action }),
        createEl('td', {}, [createEl('button', { class: 'btn btn-s', text: '查看', onClick: () => { close(); window.dispatchEvent(new CustomEvent('rocket-select', { detail: x.slug })); } })]),
      ]));
    });
    table.appendChild(tbody);
    el.appendChild(table);
  }
  if (r.error_details?.length) {
    const details = createEl('details');
    details.appendChild(createEl('summary', { text: `${r.error_details.length} 个错误` }));
    r.error_details.forEach((x) => {
      details.appendChild(createEl('div', { class: 'ri err', text: `${x.file}: ${x.error}` }));
    });
    el.appendChild(details);
  }
}

function setLoading(loading) {
  const btn = $('im-paste-btn');
  if (btn) { btn.disabled = loading; btn.textContent = loading ? '导入中...' : '粘贴导入'; }
  const batch = $('im-batch-confirm');
  if (batch && loading) batch.disabled = true;
}
