/**
 * Backend API wrappers.
 */
import { api } from './utils.js?v=2026072706';

export const Rockets = {
  list: () => api('GET', '/api/rockets'),
  get: (slug) => api('GET', `/api/rockets/${encodeURIComponent(slug)}`),
  create: (cfg) => api('POST', '/api/rockets', cfg),
  update: (slug, cfg) => api('PUT', `/api/rockets/${encodeURIComponent(slug)}`, cfg),
  delete: (slug) => api('DELETE', `/api/rockets/${encodeURIComponent(slug)}`),
  move: (slug, folderId) => api('POST', `/api/rockets/${encodeURIComponent(slug)}/move`, { folder_id: folderId }),
};

export const Data = {
  get: (slug) => api('GET', `/api/data/${encodeURIComponent(slug)}`),
  recompute: (slug) => api('POST', `/api/recompute/${encodeURIComponent(slug)}`),
  orbitCalc: (params) => api('POST', '/api/orbit-calc', params),
  missionQuery: (params) => api('POST', '/api/mission-query', params),
  find: (payload_t, c3) => api('POST', '/api/find', { payload_t, c3 }),
};

export const Folders = {
  tree: () => api('GET', '/api/folders'),
  create: (name, parentId = null) => api('POST', '/api/folders', { name, parent_id: parentId }),
  rename: (id, name) => api('PUT', `/api/folders/${id}`, { name }),
  delete: (id) => api('DELETE', `/api/folders/${id}`),
};

export const Engines = {
  list: () => api('GET', '/api/engines/detail'),
  reload: () => api('POST', '/api/engines/reload'),
};

export const Query = {
  search: (filters) => api('POST', '/api/query', filters),
  stats: () => api('GET', '/api/stats'),
};

export const CompareApi = {
  get: (slugs) => api('GET', `/api/compare?slugs=${encodeURIComponent(slugs.join(','))}`),
};

export const ExportApi = {
  csvUrl: (slug) => `/api/export/${encodeURIComponent(slug)}/csv`,
  jsonUrl: (slug) => `/api/export/${encodeURIComponent(slug)}/json`,
};

export const ImportApi = {
  library: () => api('GET', '/api/import/library'),
  libraryPreview: (id, sha256) => api('POST', '/api/import/library/preview', { id, sha256 }),
  upload: (files) => {
    const fd = new FormData();
    files.forEach((f) => fd.append('files', f));
    return fetch('/api/import/yaml', { method: 'POST', body: fd })
      .then(async (r) => {
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        return data;
      });
  },
  paste: (yamlText) => api('POST', '/api/import/paste', { yaml_text: yamlText }),
  batchPreview: (files) => {
    const fd = new FormData();
    files.forEach((file) => fd.append('files', file, file.name));
    return fetch('/api/import/batch/preview', { method: 'POST', body: fd })
      .then(async (r) => {
        const data = await r.json();
        if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
        return data;
      });
  },
  batchConfirm: (items, allowUpdates = false) => api('POST', '/api/import/batch/confirm', {
    items,
    allow_updates: allowUpdates,
  }),
};
